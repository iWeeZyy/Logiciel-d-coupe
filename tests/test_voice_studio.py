"""Voice Studio : URL, sous-titres, transcription verbatim, exports, voix.

Aucun appel reseau : yt-dlp est remplace par un double qui rend les memes
structures que l'API reelle. Tout le decodage, l'enchainement des etapes et
les messages d'erreur sont donc reellement executes.
"""
import json
import wave

import pytest

from core.models import Segment, Transcript, Word
from utils.errors import CancelledError, RightsNotConfirmedError
from voice_studio import exporters, services, store, tts
from voice_studio import transcript as T
from voice_studio.models import (
    SOURCE_AUTO_CAPTIONS,
    SOURCE_SUBTITLES,
    SOURCE_WHISPER,
    VoiceStudioProject,
)
from voice_studio.subtitle_source import parse_seconds, parse_vtt
from voice_studio.youtube_source import InvalidYouTubeUrl, parse_video_id

VIDEO_ID = "7QRuRHgHebQ"

VTT = """WEBVTT

00:00:00.000 --> 00:00:03.000
Donc en gros, bah,

00:00:03.000 --> 00:00:06.500
Donc en gros, bah, aujourd'hui on va voir

00:00:06.500 --> 00:00:09.000
<00:00:06.800><c>ensemble</c> un truc
"""


def _transcript(*triples, duration=0.0):
    segments = [Segment(id=i, start=a, end=b, text=t, words=[])
                for i, (a, b, t) in enumerate(triples)]
    return Transcript(language="fr", language_probability=0.99,
                      duration=duration or (segments[-1].end if segments else 0.0),
                      full_text=" ".join(s.text for s in segments), segments=segments)


# --------------------------------------------------------------------- URL

@pytest.mark.parametrize("url", [
    "https://youtu.be/7QRuRHgHebQ",
    "https://www.youtube.com/watch?v=7QRuRHgHebQ&t=42s",
    "https://www.youtube.com/shorts/7QRuRHgHebQ",
    "https://m.youtube.com/watch?v=7QRuRHgHebQ",
    "youtube.com/watch?v=7QRuRHgHebQ",
    "7QRuRHgHebQ",
])
def test_the_usual_youtube_addresses_are_recognised(url):
    assert parse_video_id(url) == VIDEO_ID


@pytest.mark.parametrize("value,expected", [
    ("", "Colle d'abord"),
    ("https://vimeo.com/1234", "pas une vidéo YouTube"),
    ("https://www.youtube.com/@unechaine", "sans identifiant"),
])
def test_a_refused_address_says_what_is_wrong(value, expected):
    # Un message qui explique se corrige ; "URL invalide" ne se corrige pas.
    with pytest.raises(InvalidYouTubeUrl) as excinfo:
        parse_video_id(value)
    assert expected in str(excinfo.value)


# --------------------------------------------------------------- sous-titres

def test_youtube_rolling_captions_are_not_repeated():
    # Les sous-titres automatiques repetent la ligne precedente a chaque bloc
    # (effet de defilement) : les garder telles quelles triplerait le texte.
    segments = parse_vtt(VTT)

    assert [s.text for s in segments] == [
        "Donc en gros, bah,", "aujourd'hui on va voir", "ensemble un truc"]


def test_word_level_tags_are_removed_but_not_the_words():
    assert "ensemble un truc" == parse_vtt(VTT)[2].text


def test_subtitle_timings_are_kept_as_they_are():
    segments = parse_vtt(VTT)
    assert (segments[1].start, segments[1].end) == (3.0, 6.5)
    assert parse_seconds("01:02:03.500") == 3723.5


# ------------------------------------------------------------ verbatim

def test_the_clean_view_never_changes_a_single_word():
    # EXIGENCE CENTRALE : la vue nettoyee ne fait que de la mise en page. Une
    # hesitation, une repetition, un tic de langage restent dans le texte.
    raw = _transcript(
        (0.0, 3.0, "donc en gros, bah, aujourd'hui on va voir"),
        (3.1, 6.0, "euh   ensemble  un truc"),
        (20.0, 24.0, "et voilà pour les répétitions répétitions"),
    )

    cleaned = T.clean_text(raw)

    assert T.same_words(T.raw_text(raw), cleaned)
    assert "bah" in cleaned and "euh" in cleaned
    assert cleaned.count("répétitions") == 2


def test_a_long_silence_becomes_a_paragraph_not_a_cut():
    raw = _transcript((0.0, 3.0, "première partie"), (20.0, 24.0, "deuxième partie"))

    assert T.clean_text(raw).count("\n\n") == 1
    assert T.same_words(T.raw_text(raw), T.clean_text(raw))


def test_the_raw_view_stays_available_beside_the_clean_one():
    raw = _transcript((0.0, 3.0, "euh, donc"))

    assert T.view_text(raw, T.VIEW_RAW) == "euh, donc"
    assert T.view_text(raw, T.VIEW_CLEAN) == "Euh, donc"


# ------------------------------------------------------------- couverture

def test_a_transcription_that_stops_early_is_reported():
    # 11 min 35 de video, texte qui s'arrete a 6 min : il manque la moitie.
    partial = T.coverage(_transcript((0.0, 360.0, "…")), media_duration_s=695.0)

    assert not partial.reached_end
    assert partial.tail_gap_s == pytest.approx(335.0)


def test_a_silent_ending_is_not_treated_as_a_missing_end():
    # Un generique sans parole n'est pas une transcription incomplete.
    full = T.coverage(_transcript((0.0, 690.0, "…")), media_duration_s=695.0)

    assert full.reached_end


def test_coverage_without_a_known_duration_never_cries_wolf():
    assert T.coverage(_transcript((0.0, 10.0, "…")), media_duration_s=0.0).reached_end


# -------------------------------------------------------------- recherche

def test_the_search_ignores_case_and_accents():
    data = _transcript((0.0, 3.0, "On va RÉPÉTER l'expérience"), (3.0, 6.0, "répéter encore"))

    assert len(T.search(data, "repeter")) == 2
    assert len(T.search(data, "RÉPÉTER")) == 2


def test_the_search_positions_land_on_the_real_text():
    data = _transcript((0.0, 3.0, "On va répéter l'expérience"))
    match = T.search(data, "répéter")[0]

    assert data.segments[0].text[match.start_in_segment:match.end_in_segment] == "répéter"


def test_an_empty_search_finds_nothing_rather_than_everything():
    assert T.search(_transcript((0.0, 1.0, "texte")), "  ") == []


# ---------------------------------------------------------------- exports

def test_the_three_formats_carry_the_real_timings():
    data = _transcript((0.0, 3.2, "Bonjour à tous."), (3.2, 7.75, "On y va."))

    assert "00:00:03,200 --> 00:00:07,750" in exporters.build(data, "srt")
    assert "00:00:03.200 --> 00:00:07.750" in exporters.build(data, "vtt")
    assert exporters.build(data, "vtt").startswith("WEBVTT")
    assert "[00:00:03 → 00:00:07]" in exporters.build(data, "txt")


def test_the_text_export_can_drop_the_timestamps(tmp_path):
    data = _transcript((0.0, 3.0, "Bonjour à tous."))

    plain = exporters.build(data, "txt", with_timestamps=False)

    assert "00:00" not in plain and "Bonjour à tous." in plain


def test_exporting_an_empty_transcription_is_refused(tmp_path):
    with pytest.raises(ValueError):
        exporters.write(_transcript(), str(tmp_path / "vide.srt"))


def test_the_file_written_is_the_content_built(tmp_path):
    data = _transcript((0.0, 3.0, "Bonjour."))
    path = exporters.write(data, str(tmp_path / "sous-titres.srt"))

    assert open(path, encoding="utf-8").read() == exporters.build(data, "srt")


def test_the_suggested_name_comes_from_the_video_title():
    project = VoiceStudioProject(youtube_video_id=VIDEO_ID, title="Mon test : épisode 3/4")

    assert exporters.suggested_filename(project, "txt") == "Mon test épisode 3 4.txt"


# ------------------------------------------------------------ enregistrement

def test_a_project_survives_a_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "store_dir", lambda: tmp_path)
    project = VoiceStudioProject(youtube_video_id=VIDEO_ID, title="Essai",
                                 transcript=_transcript((0.0, 2.0, "Bonjour.")))

    store.save(project)
    again = store.load(VIDEO_ID)

    assert again.title == "Essai"
    assert again.transcript.segments[0].text == "Bonjour."
    assert again.has_transcript


def test_a_damaged_file_is_ignored_rather_than_fatal(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "store_dir", lambda: tmp_path)
    (tmp_path / f"{VIDEO_ID}.json").write_text("{ ceci n'est pas du json", encoding="utf-8")

    assert store.load(VIDEO_ID) is None


def test_a_project_can_be_deleted_and_listed(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "store_dir", lambda: tmp_path)
    store.save(VoiceStudioProject(youtube_video_id=VIDEO_ID, title="Essai"))

    assert [p.youtube_video_id for p in store.list_projects()] == [VIDEO_ID]
    assert store.delete(VIDEO_ID) is True
    assert store.list_projects() == []


# ----------------------------------------------------- enchainement complet

class FakeYdl:
    """Double de yt_dlp.YoutubeDL : memes structures, aucun reseau."""

    def __init__(self, options):
        self.options = options
        FakeYdl.calls.append(options)

    calls: list = []
    info: dict = {}
    subtitle_text: str = VTT
    subtitle_dir = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def extract_info(self, url, download=False):
        if download and self.options.get("writesubtitles"):
            target = FakeYdl.subtitle_dir / f"{VIDEO_ID}.fr.vtt"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(FakeYdl.subtitle_text, encoding="utf-8")
        return dict(FakeYdl.info)


@pytest.fixture
def fake_ydl(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "store_dir", lambda: tmp_path / "projets")
    FakeYdl.calls = []
    FakeYdl.info = {
        "id": VIDEO_ID, "title": "Une vidéo", "uploader": "Une chaîne",
        "duration": 695, "thumbnail": "https://img/x.jpg",
        "subtitles": {"fr": [{"ext": "vtt"}]}, "automatic_captions": {},
    }

    def _factory(options):
        FakeYdl.subtitle_dir = tmp_path / "subs"
        outtmpl = options.get("outtmpl")
        if outtmpl:
            FakeYdl.subtitle_dir = type(tmp_path)(outtmpl).parent
        return FakeYdl(options)

    return _factory


def test_published_subtitles_are_preferred_over_a_local_transcription(fake_ydl):
    report = services.run_analysis(
        services.AnalysisRequest(url=f"https://youtu.be/{VIDEO_ID}"), ydl_factory=fake_ydl)

    assert report.ok
    assert report.used_source == SOURCE_SUBTITLES
    assert report.project.title == "Une vidéo"
    assert [s.text for s in report.project.transcript.segments][0] == "Donc en gros, bah,"
    # Aucun telechargement de media n'a ete demande.
    assert all(c.get("format") is None for c in FakeYdl.calls)


def test_automatic_captions_are_used_and_signalled(fake_ydl):
    FakeYdl.info["subtitles"] = {}
    FakeYdl.info["automatic_captions"] = {"fr": [{"ext": "vtt"}]}

    report = services.run_analysis(
        services.AnalysisRequest(url=f"https://youtu.be/{VIDEO_ID}"), ydl_factory=fake_ydl)

    assert report.used_source == SOURCE_AUTO_CAPTIONS
    assert any("automatiquement" in note for note in report.notes)


def test_without_captions_and_without_consent_nothing_is_downloaded(fake_ydl):
    FakeYdl.info["subtitles"] = {}
    FakeYdl.info["automatic_captions"] = {}

    with pytest.raises(RightsNotConfirmedError) as excinfo:
        services.run_analysis(
            services.AnalysisRequest(url=f"https://youtu.be/{VIDEO_ID}"), ydl_factory=fake_ydl)

    assert "piste audio" in str(excinfo.value)


def test_with_consent_the_audio_path_is_used(fake_ydl, monkeypatch, tmp_path):
    FakeYdl.info["subtitles"] = {}
    FakeYdl.info["automatic_captions"] = {}
    spoken = _transcript((0.0, 690.0, "Le texte complet, mot pour mot."))
    monkeypatch.setattr(services.transcription, "download_audio",
                        lambda *a, **k: str(tmp_path / "audio.m4a"))
    monkeypatch.setattr(services.transcription, "transcribe_media", lambda *a, **k: spoken)

    report = services.run_analysis(
        services.AnalysisRequest(url=f"https://youtu.be/{VIDEO_ID}", allow_audio_download=True),
        ydl_factory=fake_ydl)

    assert report.used_source == SOURCE_WHISPER
    assert report.project.whisper_model == "small"


def test_a_transcription_that_misses_the_end_is_signalled(fake_ydl):
    # Les sous-titres s'arretent a 9 secondes, la video dure 11 min 35.
    report = services.run_analysis(
        services.AnalysisRequest(url=f"https://youtu.be/{VIDEO_ID}"), ydl_factory=fake_ydl)

    assert any("s'arrête" in note for note in report.notes)


def test_an_existing_project_is_found_before_anything_is_redone(fake_ydl, tmp_path):
    services.run_analysis(services.AnalysisRequest(url=f"https://youtu.be/{VIDEO_ID}"),
                          ydl_factory=fake_ydl)

    assert services.existing_project(f"https://youtu.be/{VIDEO_ID}") is not None
    assert services.existing_project("https://vimeo.com/1") is None


def test_cancelling_saves_nothing(fake_ydl, monkeypatch, tmp_path):
    from core.cancellation import CancelToken

    FakeYdl.info["subtitles"] = {}
    FakeYdl.info["automatic_captions"] = {}
    token = CancelToken()
    token.cancel()
    monkeypatch.setattr(services.transcription, "download_audio",
                        lambda *a, **k: str(tmp_path / "audio.m4a"))

    with pytest.raises(CancelledError):
        services.run_analysis(
            services.AnalysisRequest(url=f"https://youtu.be/{VIDEO_ID}",
                                     allow_audio_download=True),
            cancel_token=token, ydl_factory=fake_ydl)

    assert store.load(VIDEO_ID) is None


def test_the_steps_are_reported_in_order(fake_ydl):
    seen = []
    reporter = services.Reporter(on_step=lambda index, label: seen.append(label))

    services.run_analysis(services.AnalysisRequest(url=f"https://youtu.be/{VIDEO_ID}"),
                          reporter=reporter, ydl_factory=fake_ydl)

    assert seen == [services.STEP_URL, services.STEP_VIDEO, services.STEP_SUBTITLES,
                    services.STEP_FINALISE]


# -------------------------------------------------------------------- voix

def test_cutting_into_sentences_never_loses_a_word():
    text = "Première phrase. Deuxième phrase ! Et une troisième ?"

    assert T.same_words(text, " ".join(tts.split_sentences(text)))


def test_the_voice_settings_are_kept_within_reason():
    assert tts.clamp(9.0, tts.MIN_RATE, tts.MAX_RATE) == tts.MAX_RATE
    assert tts.clamp(-1.0, tts.MIN_VOLUME, tts.MAX_VOLUME) == tts.MIN_VOLUME


def test_the_silence_between_sentences_is_added_not_written(tmp_path):
    # Le silence est colle ENTRE les morceaux : le texte lu, lui, ne change pas.
    def _wav(path, seconds):
        with wave.open(str(path), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(22050)
            handle.writeframes(b"\x00\x01" * int(22050 * seconds))
        return str(path)

    pieces = [_wav(tmp_path / "a.wav", 1.0), _wav(tmp_path / "b.wav", 1.0)]
    out = tts.concat_wavs(pieces, str(tmp_path / "out.wav"), silence_s=0.5)

    with wave.open(out) as handle:
        assert handle.getnframes() / handle.getframerate() == pytest.approx(2.5, abs=0.05)


def test_a_voice_list_that_is_empty_is_a_normal_answer(monkeypatch):
    # Une machine sans voix installee ne doit pas faire echouer l'application :
    # l'interface annonce l'absence et desactive la generation.
    monkeypatch.setattr(tts, "engines", lambda: [])

    assert tts.available_voices() == []
    with pytest.raises(tts.TtsError):
        tts.synthesize("texte", "/tmp/inutile.wav")


def test_generating_without_text_is_refused():
    with pytest.raises(tts.TtsError):
        tts.synthesize("   ", "/tmp/inutile.wav")


@pytest.mark.skipif(not tts.available_voices(), reason="aucune voix sur cette machine")
def test_a_real_voice_produces_a_real_wav(tmp_path):
    voice = tts.available_voices()[0]
    out = tts.synthesize("Bonjour, ceci est un test.", str(tmp_path / "voix.wav"), voice=voice)

    with wave.open(out) as handle:
        assert handle.getnframes() > 0


# ------------------------------------------------- reglages de transcription

def test_the_local_transcription_runs_without_the_voice_detector(monkeypatch, tmp_path):
    """VERBATIM : le detecteur de voix ecarte des zones AVANT reconnaissance.

    Il fait gagner du temps quand on cherche des passages a clipper ; sur une
    retranscription integrale, un mot prononce dans une zone mal jugee
    disparaitrait sans que rien ne le signale. Voice Studio le desactive, le
    pipeline video le garde.
    """
    from voice_studio import transcription as vs_transcription

    seen = {}

    def _fake_transcribe(wav_path, model, language, device, **kwargs):
        seen.update(kwargs)
        return _transcript((0.0, 2.0, "texte"))

    monkeypatch.setattr(vs_transcription, "whisper_transcribe", _fake_transcribe)
    monkeypatch.setattr(vs_transcription, "extract_audio", lambda *a, **k: None)

    vs_transcription.transcribe_media(str(tmp_path / "a.m4a"), "small", "fr", use_cache=False)

    assert seen["vad_filter"] is False


def test_the_clip_pipeline_still_uses_the_voice_detector():
    # Le defaut du moteur partage ne change pas : le reste du logiciel garde
    # exactement le comportement qu'il avait.
    import inspect

    from transcription.whisper_engine import transcribe

    assert inspect.signature(transcribe).parameters["vad_filter"].default is True


def test_downloading_audio_without_consent_is_refused(tmp_path):
    from voice_studio import transcription as vs_transcription

    with pytest.raises(RightsNotConfirmedError):
        vs_transcription.download_audio(VIDEO_ID, str(tmp_path), consent_confirmed=False)


def test_a_cached_transcription_is_reused_rather_than_recomputed(monkeypatch, tmp_path):
    from transcription import cache as transcript_cache
    from voice_studio import transcription as vs_transcription

    ready = _transcript((0.0, 5.0, "déjà transcrit"))
    monkeypatch.setattr(transcript_cache, "load", lambda *a, **k: ready)

    def _never(*args, **kwargs):
        raise AssertionError("Whisper ne devait pas être relancé")

    monkeypatch.setattr(vs_transcription, "whisper_transcribe", _never)

    assert vs_transcription.transcribe_media(str(tmp_path / "a.m4a"), "small", "fr") is ready
