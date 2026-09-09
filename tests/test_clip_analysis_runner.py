"""Enchainement d'une analyse : media, cache, sauvegarde, annulation, erreurs.

Pas de modele Whisper ici : la transcription est remplacee par un faux, ce qui
permet de tester la PLOMBERIE (ce que le runner garantit) sans dependre d'un
telechargement de 75 Mo ni d'une machine rapide. La verification avec un vrai
modele et un vrai fichier audio est faite a part, hors suite de tests.
"""
from __future__ import annotations

import wave
from pathlib import Path

import pytest

from core.cancellation import CancelToken
from core.models import Segment, Transcript, Word
from radar.analysis import media, runner
from radar.analysis.models import LEVEL_DEEP, LEVEL_FAST, LEVEL_STANDARD, ClipAnalysis
from radar.models import KIND_CLIP, KIND_LIVE, PLATFORM_TWITCH, Opportunity
from radar.store import RadarStore
from utils.errors import CancelledError, ClipFarmingError, MediaNotAvailableError

PHRASE = ("je prepare ma manche sur la carte du desert. attends quoi ? "
          "c'est pas possible ! ahah je rigole, je n'ai jamais vu ca.")


def fake_transcript(text: str = PHRASE, duration: float = 12.0) -> Transcript:
    words = []
    t = 0.0
    for token in text.split():
        words.append(Word(text=token, start=t, end=t + 0.25, probability=0.92))
        t += 0.3
    segment = Segment(id=0, start=0.0, end=t, text=text, words=words,
                      avg_logprob=-0.2, no_speech_prob=0.05)
    return Transcript(language="fr", language_probability=0.99, duration=duration,
                      full_text=text, segments=[segment])


@pytest.fixture
def clip():
    return Opportunity(platform=PLATFORM_TWITCH, content_id="AbcClip", kind=KIND_CLIP,
                       creator_key="twitch:42", title="Une manche serrée",
                       url="https://clips.twitch.tv/AbcClip", view_count=1200,
                       duration_s=12, category="Just Chatting", radar_score=64.0)


@pytest.fixture
def media_file(tmp_path) -> Path:
    """Un vrai fichier wav, pour que l'empreinte et ffprobe aient de quoi lire."""
    path = tmp_path / "clip.wav"
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b"\x00\x01" * 16000 * 12)
    return path


@pytest.fixture
def store(tmp_path) -> RadarStore:
    return RadarStore(path=tmp_path / "radar.sqlite3")


@pytest.fixture
def patched(monkeypatch):
    """Remplace la transcription et l'extraction audio, garde tout le reste."""
    calls = {"transcribe": 0, "extract": 0}

    def fake_extract(video_path, out_wav_path, sample_rate=16000):
        calls["extract"] += 1
        Path(out_wav_path).write_bytes(b"RIFF fake")

    def fake_transcribe(wav_path, model_name, language, device_pref, cancel_token=None,
                        on_segment_progress=None, on_download_progress=None):
        calls["transcribe"] += 1
        if on_segment_progress:
            on_segment_progress(6.0, 12.0)
        if cancel_token is not None:
            cancel_token.check()
        return fake_transcript()

    monkeypatch.setattr("video.audio_extractor.extract_audio", fake_extract)
    monkeypatch.setattr("transcription.whisper_engine.transcribe", fake_transcribe)
    monkeypatch.setattr("video.ffmpeg_utils.video_duration", lambda path: 12.0)
    # Le cache de transcription vit dans le dossier utilisateur : neutralise pour
    # que la suite ne depende pas de ce qui traine sur la machine.
    monkeypatch.setattr("transcription.cache.load", lambda *a, **k: None)
    monkeypatch.setattr("transcription.cache.save", lambda *a, **k: None)
    return calls


def request_for(clip, media_file, **kwargs) -> runner.AnalysisRequest:
    params = dict(opportunity=clip, local_path=str(media_file), model="small",
                  creator_label="ZeratoR")
    params.update(kwargs)
    return runner.AnalysisRequest(**params)


class TestMedia:
    def test_twitch_refuse_le_telechargement_et_explique(self, clip):
        with pytest.raises(MediaNotAvailableError) as error:
            media.resolve(clip)
        message = str(error.value)
        assert "aucun moyen officiel" in message
        assert "fichier" in message.lower()

    def test_aucune_promesse_de_libre_de_droits(self, clip):
        message = media.explanation_for(clip)
        assert "libre de droit" not in message.lower()
        assert "légalement" in message

    def test_un_direct_n_est_pas_analysable(self):
        live = Opportunity(platform=PLATFORM_TWITCH, content_id="live1", kind=KIND_LIVE,
                           creator_key="twitch:42", is_live=True)
        assert media.can_analyze(live) is False
        with pytest.raises(MediaNotAvailableError):
            media.resolve(live)

    def test_fichier_local_accepte_et_empreinte_stable(self, clip, media_file):
        first = media.resolve(clip, local_path=str(media_file))
        second = media.resolve(clip, local_path=str(media_file))
        assert first.fingerprint == second.fingerprint
        assert first.origin == "local"

    def test_fichier_absent_signale_clairement(self, clip, tmp_path):
        with pytest.raises(MediaNotAvailableError) as error:
            media.resolve(clip, local_path=str(tmp_path / "nulle-part.mp4"))
        assert "introuvable" in str(error.value)

    def test_fichier_vide_refuse(self, clip, tmp_path):
        empty = tmp_path / "vide.mp4"
        empty.write_bytes(b"")
        with pytest.raises(MediaNotAvailableError):
            media.resolve(clip, local_path=str(empty))

    def test_deux_fichiers_differents_ont_deux_empreintes(self, tmp_path):
        a = tmp_path / "a.bin"
        b = tmp_path / "b.bin"
        a.write_bytes(b"x" * 4096)
        b.write_bytes(b"y" * 4096)
        assert media.fingerprint(a) != media.fingerprint(b)


class TestEnchainement:
    def test_analyse_complete(self, clip, media_file, store, patched):
        analysis = runner.run(request_for(clip, media_file), store=store)
        assert analysis.content_id == clip.key
        assert analysis.transcript_text
        assert analysis.summary
        assert analysis.title_direct
        assert analysis.key_moment is not None
        assert analysis.confidence in ("elevee", "moyenne", "faible")
        assert analysis.processing_time_s is not None
        assert analysis.model_used == "small"

    def test_le_resultat_est_relu_a_l_identique(self, clip, media_file, store, patched):
        original = runner.run(request_for(clip, media_file), store=store)
        reloaded = store.get_analysis(clip.key)
        assert isinstance(reloaded, ClipAnalysis)
        assert reloaded.summary == original.summary
        assert reloaded.hashtags == original.hashtags

    def test_la_progression_suit_les_quatre_etapes(self, clip, media_file, store, patched):
        seen = []
        runner.run(request_for(clip, media_file), store=store,
                   on_progress=lambda event: seen.append(event.label))
        assert [label for label in dict.fromkeys(seen) if label] == runner.STEPS

    def test_rien_n_est_laisse_dans_le_dossier_temporaire(self, clip, media_file, store,
                                                          patched, tmp_path, monkeypatch):
        temp_root = tmp_path / "temp"
        temp_root.mkdir()
        monkeypatch.setenv("TMPDIR", str(temp_root))
        runner.run(request_for(clip, media_file), store=store)
        assert list(temp_root.iterdir()) == []

    def test_le_niveau_rapide_allege_le_resultat(self, clip, media_file, store, patched):
        fast = runner.run(request_for(clip, media_file, level=LEVEL_FAST), store=store)
        assert fast.words == [], "le mode rapide ne conserve pas les mots horodatés"
        assert fast.detected_emotions == []
        assert fast.summary

    def test_le_niveau_approfondi_propose_des_candidats(self, clip, media_file, store, patched):
        deep = runner.run(request_for(clip, media_file, level=LEVEL_DEEP), store=store)
        assert deep.key_moment["alternatives"]

    def test_un_niveau_inconnu_retombe_sur_standard(self, clip, media_file, store, patched):
        analysis = runner.run(request_for(clip, media_file, level="fantaisie"), store=store)
        assert analysis.analysis_level == LEVEL_STANDARD


class TestCache:
    def test_meme_media_meme_modele_meme_niveau_ne_retranscrit_pas(self, clip, media_file,
                                                                   store, patched):
        runner.run(request_for(clip, media_file), store=store)
        assert patched["transcribe"] == 1
        runner.run(request_for(clip, media_file), store=store)
        assert patched["transcribe"] == 1, "la seconde analyse doit être réutilisée"

    def test_changer_de_niveau_relance_l_analyse(self, clip, media_file, store, patched):
        runner.run(request_for(clip, media_file, level=LEVEL_STANDARD), store=store)
        runner.run(request_for(clip, media_file, level=LEVEL_DEEP), store=store)
        assert patched["transcribe"] == 2

    def test_changer_de_media_relance_l_analyse(self, clip, media_file, store, patched, tmp_path):
        runner.run(request_for(clip, media_file), store=store)
        autre = tmp_path / "autre.wav"
        autre.write_bytes(media_file.read_bytes() + b"different")
        runner.run(request_for(clip, autre), store=store)
        assert patched["transcribe"] == 2

    def test_reanalyse_forcee_remplace_sans_doubler(self, clip, media_file, store, patched):
        runner.run(request_for(clip, media_file), store=store)
        runner.run(request_for(clip, media_file, force=True), store=store)
        assert patched["transcribe"] == 2
        assert len(store.list_analyses()) == 1

    def test_les_contenus_analyses_sont_listes_en_une_requete(self, clip, media_file,
                                                              store, patched):
        runner.run(request_for(clip, media_file), store=store)
        assert store.analyzed_ids() == {clip.key}
        assert store.has_analysis(clip.key)

    def test_suppression_d_une_analyse(self, clip, media_file, store, patched):
        runner.run(request_for(clip, media_file), store=store)
        store.delete_analysis(clip.key)
        assert store.get_analysis(clip.key) is None


class TestModeleParNiveau:
    def test_le_mode_rapide_prefere_un_modele_deja_present(self):
        assert runner.choose_model(LEVEL_FAST, "small", lambda name: name == "tiny") == "tiny"

    def test_le_mode_rapide_ne_declenche_aucun_telechargement(self):
        assert runner.choose_model(LEVEL_FAST, "small", lambda name: False) == "small"

    def test_les_autres_niveaux_gardent_le_modele_configure(self):
        assert runner.choose_model(LEVEL_STANDARD, "medium", lambda name: True) == "medium"
        assert runner.choose_model(LEVEL_DEEP, "medium", lambda name: True) == "medium"


class TestAnnulationEtErreurs:
    def test_une_annulation_reste_une_annulation(self, clip, media_file, store, patched):
        token = CancelToken()
        token.cancel()
        with pytest.raises(CancelledError):
            runner.run(request_for(clip, media_file), store=store, cancel_token=token)
        assert store.get_analysis(clip.key) is None

    def test_media_sans_piste_audio_explique_le_probleme(self, clip, media_file, store,
                                                          patched, monkeypatch):
        from utils.errors import NoAudioError

        def no_audio(*args, **kwargs):
            raise NoAudioError("ce fichier ne contient aucune piste audio")

        monkeypatch.setattr("video.audio_extractor.extract_audio", no_audio)
        with pytest.raises(NoAudioError):
            runner.run(request_for(clip, media_file), store=store)

    def test_une_erreur_python_brute_est_traduite(self, clip, media_file, store,
                                                   patched, monkeypatch):
        def explose(*args, **kwargs):
            raise ZeroDivisionError("division by zero")

        monkeypatch.setattr("video.audio_extractor.extract_audio", explose)
        with pytest.raises(ClipFarmingError) as error:
            runner.run(request_for(clip, media_file), store=store)
        message = str(error.value)
        assert "L'analyse de ce clip a échoué" in message
        assert "Traceback" not in message

    def test_un_media_trop_long_est_refuse_avant_de_commencer(self, clip, media_file,
                                                              store, patched, monkeypatch):
        monkeypatch.setattr("video.ffmpeg_utils.video_duration", lambda path: 4 * 3600.0)
        with pytest.raises(MediaNotAvailableError) as error:
            runner.run(request_for(clip, media_file), store=store)
        assert "minutes" in str(error.value)
        assert patched["transcribe"] == 0
