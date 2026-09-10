"""Creation d'une video narree dans Voice Studio, et telechargement complet
depuis Recherche.

Tests PURS : aucun encodage, aucune synthese vocale, aucun modele Whisper,
aucune requete reseau. Ils portent sur les decisions (duree, son, cadre,
echelle des sous-titres) et sur les commandes construites -- c'est justement
pour cela que ces decisions vivent dans des modules sans effet de bord.

Ce que ces tests NE couvrent PAS, et qui a ete verifie autrement (rendus reels
inspectes image par image, voir le rapport de la fonctionnalite) : l'encodage
ffmpeg lui-meme, la synthese Piper/Windows, et la transcription par
Faster-Whisper de la voix generee.
"""
from __future__ import annotations

import sys
import types

import pytest

from core.models import Segment, Transcript, Word
from voice_studio import narration, store, video_edit, video_service
from voice_studio.models import VideoSettings, VoiceStudioProject


def _words(count: int = 8, step: float = 0.9) -> list[Word]:
    return [Word(text=f"mot{i}", start=i * step, end=i * step + step * 0.8,
                 probability=0.9) for i in range(count)]


def _transcript(count: int = 8) -> Transcript:
    words = _words(count)
    segment = Segment(id=0, start=0.0, end=words[-1].end,
                      text=" ".join(w.text for w in words), words=words)
    return Transcript(language="fr", language_probability=0.99,
                      duration=words[-1].end, full_text=segment.text,
                      segments=[segment])


class TestScript:
    def test_les_espaces_sont_normalises_mais_le_texte_ne_change_pas(self):
        assert narration.clean_script("  Bonjour   a   tous  ") == "Bonjour a tous"

    def test_les_lignes_vides_en_serie_sont_ramenees_a_une(self):
        assert narration.clean_script("a\n\n\n\n\nb") == "a\n\nb"

    def test_rien_n_est_corrige_ni_reformule(self):
        """Le script est celui de l'utilisateur : pas de ponctuation ajoutee,
        pas de majuscule imposee, pas de correction."""
        brut = "alors euh je disais que  bref"
        assert narration.clean_script(brut) == "alors euh je disais que bref"

    def test_le_compte_de_mots_ignore_les_espaces_multiples(self):
        assert narration.word_count("un  deux\ttrois\nquatre") == 4
        assert narration.word_count("   ") == 0

    def test_la_duree_estimee_diminue_quand_la_vitesse_augmente(self):
        lent = narration.estimate_duration_s("un deux trois quatre cinq", 1.0)
        rapide = narration.estimate_duration_s("un deux trois quatre cinq", 1.5)
        assert rapide < lent

    def test_un_script_vide_ne_dure_rien(self):
        assert narration.estimate_duration_s("", 1.0) == 0.0
        assert narration.stats("").is_empty

    def test_import_d_un_fichier_texte(self, tmp_path):
        path = tmp_path / "script.txt"
        path.write_text("Première ligne.\n\nSeconde ligne.", encoding="utf-8")
        assert narration.read_script_file(str(path)).startswith("Première ligne.")

    def test_un_fichier_windows_accentue_reste_lisible(self, tmp_path):
        """Un script ecrit au Bloc-notes n'est pas toujours en UTF-8 : le
        refuser pour un accent serait absurde."""
        path = tmp_path / "script.txt"
        path.write_bytes("Déjà prêt".encode("cp1252"))
        assert "Déjà" in narration.read_script_file(str(path))

    def test_un_format_non_gere_est_refuse_avec_une_explication(self, tmp_path):
        path = tmp_path / "script.pdf"
        path.write_bytes(b"%PDF-1.4")
        with pytest.raises(narration.ScriptError) as error:
            narration.read_script_file(str(path))
        assert "texte" in str(error.value).lower()

    def test_un_fichier_absent_le_dit(self, tmp_path):
        with pytest.raises(narration.ScriptError):
            narration.read_script_file(str(tmp_path / "absent.txt"))

    def test_un_fichier_vide_le_dit(self, tmp_path):
        path = tmp_path / "vide.txt"
        path.write_text("   \n\n", encoding="utf-8")
        with pytest.raises(narration.ScriptError):
            narration.read_script_file(str(path))


class TestDuree:
    def test_couper_prend_le_plus_court(self):
        assert video_edit.resolve_duration(30, 42, video_edit.DURATION_CUT).output_s == 30
        assert video_edit.resolve_duration(50, 42, video_edit.DURATION_CUT).output_s == 42

    def test_garder_la_video_impose_sa_duree(self):
        plan = video_edit.resolve_duration(30, 42, video_edit.DURATION_VIDEO)
        assert plan.output_s == 30
        assert not plan.freezes

    def test_figer_prolonge_l_image_sans_toucher_a_la_voix(self):
        plan = video_edit.resolve_duration(30, 42, video_edit.DURATION_FREEZE)
        assert plan.output_s == 42
        assert plan.freeze_s == pytest.approx(12.0)

    def test_figer_ne_fige_rien_si_la_narration_est_plus_courte(self):
        plan = video_edit.resolve_duration(60, 42, video_edit.DURATION_FREEZE)
        assert plan.output_s == 60
        assert not plan.freezes

    def test_l_ecart_est_toujours_annonce(self):
        assert video_edit.resolve_duration(30, 42, video_edit.DURATION_CUT).notes
        assert video_edit.resolve_duration(42, 30, video_edit.DURATION_CUT).notes

    def test_sans_narration_la_video_reste_entiere(self):
        plan = video_edit.resolve_duration(30, 0, video_edit.DURATION_CUT)
        assert plan.output_s == 30
        assert not plan.notes

    def test_une_duree_video_inconnue_ne_produit_aucune_invention(self):
        plan = video_edit.resolve_duration(0, 42, video_edit.DURATION_CUT)
        assert plan.output_s == 42
        assert plan.notes, "l'utilisateur doit savoir que la durée n'a pas été mesurée"

    def test_rien_n_est_jamais_accelere_ni_ralenti(self):
        """Aucune politique ne produit un facteur de vitesse : la seule facon
        de faire coincider deux durees ici est de couper ou de figer."""
        for policy in video_edit.DURATION_POLICIES:
            plan = video_edit.resolve_duration(30, 42, policy)
            assert plan.output_s in (30, 42)


class TestSon:
    def test_le_melange_ne_normalise_pas_les_niveaux(self):
        """amix divise par defaut par le nombre d'entrees : la narration
        perdrait la moitie de son niveau sans que rien ne l'explique."""
        graph = video_edit.build_audio_graph(video_edit.AUDIO_MIX, 1)
        assert "normalize=0" in graph
        assert "amix=inputs=2" in graph

    def test_le_son_d_origine_est_baisse_et_non_supprime_en_melange(self):
        graph = video_edit.build_audio_graph(video_edit.AUDIO_MIX, 1,
                                             original_volume=0.2)
        assert "[0:a]volume=0.200" in graph

    def test_remplacer_n_utilise_que_la_narration(self):
        graph = video_edit.build_audio_graph(video_edit.AUDIO_REPLACE, 1)
        assert "[1:a]" in graph and "[0:a]" not in graph

    def test_garder_n_utilise_que_le_son_d_origine(self):
        graph = video_edit.build_audio_graph(video_edit.AUDIO_KEEP, 1)
        assert "[0:a]" in graph and "[1:a]" not in graph

    def test_chaque_branche_est_rallongee_pour_ne_pas_finir_avant_l_image(self):
        for mode in video_edit.AUDIO_MODES:
            assert "apad" in video_edit.build_audio_graph(mode, 1)

    def test_une_video_muette_ne_fait_pas_semblant_de_melanger(self):
        mode, notes = video_edit.resolve_audio_mode(
            video_edit.AUDIO_MIX, has_original_audio=False, has_narration=True)
        assert mode == video_edit.AUDIO_REPLACE
        assert notes, "le changement doit être annoncé"

    def test_sans_narration_le_son_d_origine_est_conserve(self):
        mode, notes = video_edit.resolve_audio_mode(
            video_edit.AUDIO_REPLACE, has_original_audio=True, has_narration=False)
        assert mode == video_edit.AUDIO_KEEP
        assert notes

    def test_muette_et_sans_narration_est_annonce_comme_muet(self):
        mode, notes = video_edit.resolve_audio_mode(
            video_edit.AUDIO_KEEP, has_original_audio=False, has_narration=False)
        assert mode == video_edit.AUDIO_KEEP
        assert any("muet" in n for n in notes)


class TestCadre:
    def test_le_9_16_recadre(self):
        graph = video_edit.build_video_graph(
            src_w=1920, src_h=1080, target_size=(1080, 1920), fill="flou",
            ass_path=None)
        assert "crop=" in graph and "scale=1080:1920" in graph

    def test_le_16_9_garde_l_image_entiere_et_remplit_les_bords(self):
        graph = video_edit.build_video_graph(
            src_w=1280, src_h=720, target_size=(1920, 1080), fill="flou",
            ass_path=None)
        assert "gblur" in graph
        assert "crop=1920:1080" in graph, "le fond flou couvre tout le cadre"

    def test_le_gel_est_pose_sur_la_source_avant_le_recadrage(self):
        """Pose apres, la derniere image figee n'aurait pas de sous-titres."""
        graph = video_edit.build_video_graph(
            src_w=1920, src_h=1080, target_size=(1080, 1920), fill="flou",
            ass_path=None, freeze_s=4.0)
        assert graph.index("tpad") < graph.index("crop=")
        assert "stop_duration=4.000" in graph

    def test_aucun_gel_quand_il_n_y_a_rien_a_prolonger(self):
        graph = video_edit.build_video_graph(
            src_w=1920, src_h=1080, target_size=(1920, 1080), fill="noir",
            ass_path=None, freeze_s=0.0)
        assert "tpad" not in graph


class TestCommandeDeRendu:
    def _args(self, **overrides):
        params = dict(
            video_path="source.mp4", narration_path="voix.wav", ass_path=None,
            out_path="sortie.mp4", src_w=1920, src_h=1080,
            target_size=(1080, 1920), fill="flou",
            audio_mode=video_edit.AUDIO_REPLACE,
            duration_plan=video_edit.DurationPlan(output_s=20.0),
            export_settings={"video_preset": "medium", "video_bitrate_crf": 18,
                             "audio_bitrate": "160k"},
        )
        params.update(overrides)
        return video_edit.build_render_args(**params)

    def test_la_video_est_la_premiere_entree_et_la_narration_la_seconde(self):
        args = self._args()
        assert args[:4] == ["-i", "source.mp4", "-i", "voix.wav"]
        assert "[1:a]" in args[args.index("-filter_complex") + 1]

    def test_la_duree_de_sortie_est_imposee_explicitement(self):
        args = self._args()
        assert args[args.index("-t") + 1] == "20.000"

    def test_le_format_de_pixel_est_impose(self):
        """Une video YouTube peut arriver en VP9 10 bits : sans -pix_fmt, le
        fichier produit serait refuse par la moitie des lecteurs."""
        assert "yuv420p" in self._args()

    def test_le_crf_et_le_preset_viennent_de_la_configuration(self):
        args = self._args()
        assert args[args.index("-crf") + 1] == "18"
        assert args[args.index("-preset") + 1] == "medium"

    def test_sans_son_du_tout_la_piste_audio_est_explicitement_absente(self):
        args = self._args(narration_path=None, audio_mode=video_edit.AUDIO_REPLACE)
        assert "-an" in args
        assert "-map" in args and "[aout]" not in args

    def test_les_sous_titres_sont_incrustes_quand_un_ass_est_fourni(self):
        args = self._args(ass_path="/tmp/n.ass")
        assert "subtitles=" in args[args.index("-filter_complex") + 1]

    def test_le_filigrane_est_une_entree_supplementaire_apres_la_narration(self):
        from video.watermark import Watermark

        watermark = Watermark(image="logo.png")
        args = self._args(watermark=watermark)
        assert args.index("logo.png") > args.index("voix.wav")
        graph = args[args.index("-filter_complex") + 1]
        assert "[2:v]" in graph and "overlay=" in graph

    def test_un_seul_encodage(self):
        args = self._args()
        assert args.count("-c:v") == 1


class TestSousTitresDeLaNarration:
    def test_les_blocs_viennent_des_mots_reels_de_la_voix(self):
        _key, style = video_service.subtitle_style_params("dynamic")
        groups = video_service.build_caption_groups(_words(6), style)
        assert groups
        premiers = [w.text for g in groups for w in g.words]
        assert premiers == [w.text for w in _words(6)], "aucun mot ajouté ni perdu"

    def test_les_instants_sont_ceux_des_mots(self):
        _key, style = video_service.subtitle_style_params("dynamic")
        words = _words(4)
        groups = video_service.build_caption_groups(words, style)
        assert groups[0].start == pytest.approx(words[0].start, abs=0.01)

    def test_aucun_bloc_sans_mot(self):
        _key, style = video_service.subtitle_style_params("dynamic")
        assert video_service.build_caption_groups([], style) == []

    def test_un_style_inconnu_retombe_sur_le_style_par_defaut(self):
        key, style = video_service.subtitle_style_params("style-qui-n-existe-pas")
        assert style, "un style inconnu ne doit pas empêcher le rendu"
        assert key == video_service.subtitle_style_params("")[0]

    def test_le_style_est_mis_a_l_echelle_du_cadre_paysage(self):
        """Un style calibre pour 1080x1920 aurait, en 16:9, un texte deux fois
        trop haut par rapport a l'image."""
        style = {"font_size": 112, "margin_v": 400, "outline_width": 7, "shadow": 2}
        scaled, margin = video_service.scale_style(style, 1920, 1080)
        assert scaled["font_size"] < style["font_size"]
        assert margin < style["margin_v"]

    def test_le_style_9_16_n_est_pas_touche(self):
        style = {"font_size": 112, "margin_v": 400, "outline_width": 7, "shadow": 2}
        scaled, margin = video_service.scale_style(style, 1080, 1920)
        assert scaled == style
        assert margin == 400

    def test_le_filigrane_impose_un_plancher_aux_sous_titres(self):
        """Defaut reel : au premier rendu 16:9, le texte passait derriere le
        logo."""
        style = {"font_size": 112, "margin_v": 400}
        _scaled, margin = video_service.scale_style(style, 1920, 1080,
                                                    floor_margin_v=400)
        assert margin >= 400


class TestParcours:
    def _settings(self, **overrides):
        params = dict(aspect_ratio="16:9", audio_mode=video_edit.AUDIO_REPLACE)
        params.update(overrides)
        return VideoSettings(**params)

    def test_une_video_source_absente_est_dite_clairement(self, tmp_path):
        request = video_service.VideoRequest(
            source_video=str(tmp_path / "absente.mp4"), script="Bonjour",
            out_path=str(tmp_path / "out.mp4"), settings=self._settings())
        with pytest.raises(video_service.VideoCreationError) as error:
            video_service.create_video(request)
        assert "trouvée" in str(error.value)

    def test_un_script_vide_est_refuse_sauf_si_on_garde_le_son(self, tmp_path):
        source = tmp_path / "source.mp4"
        source.write_bytes(b"pas une vraie video")
        request = video_service.VideoRequest(
            source_video=str(source), script="   ",
            out_path=str(tmp_path / "out.mp4"), settings=self._settings())
        with pytest.raises(video_service.VideoCreationError) as error:
            video_service.create_video(request)
        assert "narration" in str(error.value).lower()

    def test_le_chemin_de_la_narration_est_stable_pour_le_meme_texte(self):
        """Le cache de transcription est indexe sur le chemin du fichier : un
        nom different a chaque generation ferait retranscrire la meme voix."""
        request = video_service.VideoRequest(
            source_video="s.mp4", script="Bonjour a tous", out_path="o.mp4",
            settings=self._settings())
        assert (video_service.narration_path_for(request)
                == video_service.narration_path_for(request))

    def test_deux_textes_differents_donnent_deux_fichiers_differents(self):
        base = dict(source_video="s.mp4", out_path="o.mp4", settings=self._settings())
        a = video_service.VideoRequest(script="Bonjour", **base)
        b = video_service.VideoRequest(script="Bonsoir", **base)
        assert video_service.narration_path_for(a) != video_service.narration_path_for(b)

    def test_une_vitesse_differente_donne_un_autre_fichier(self):
        base = dict(source_video="s.mp4", out_path="o.mp4", script="Bonjour",
                    settings=self._settings())
        assert (video_service.narration_path_for(video_service.VideoRequest(rate=1.0, **base))
                != video_service.narration_path_for(video_service.VideoRequest(rate=1.25, **base)))


class TestProjet:
    def test_les_reglages_video_survivent_a_un_aller_retour(self):
        project = VoiceStudioProject(youtube_video_id="abc")
        project.source_video_path = "/videos/abc.mp4"
        project.narration_script = "Bonjour"
        project.narration_transcript = _transcript(3)
        project.video_settings = VideoSettings(aspect_ratio="9:16",
                                               audio_mode=video_edit.AUDIO_MIX)
        restored = VoiceStudioProject.from_dict(project.to_dict())
        assert restored.source_video_path == "/videos/abc.mp4"
        assert restored.narration_script == "Bonjour"
        assert restored.video_settings.aspect_ratio == "9:16"
        assert restored.video_settings.audio_mode == video_edit.AUDIO_MIX

    def test_la_transcription_de_la_narration_est_distincte_de_celle_de_la_video(self):
        """Les confondre ferait afficher les sous-titres de l'une sur l'audio
        de l'autre."""
        project = VoiceStudioProject(youtube_video_id="abc")
        project.transcript = _transcript(2)
        project.narration_transcript = _transcript(5)
        restored = VoiceStudioProject.from_dict(project.to_dict())
        assert len(restored.transcript.words()) == 2
        assert len(restored.narration_transcript.words()) == 5

    def test_un_projet_ancien_se_relit_sans_les_nouveaux_champs(self):
        ancien = {"youtube_video_id": "xyz", "title": "Vieux projet"}
        project = VoiceStudioProject.from_dict(ancien)
        assert project.source_video_path == ""
        assert project.narration_transcript is None
        assert project.video_settings.aspect_ratio == "16:9"

    def test_une_video_locale_a_une_cle_stable(self):
        assert (store.key_for_video_path("/videos/a.mp4")
                == store.key_for_video_path("/videos/a.mp4"))
        assert (store.key_for_video_path("/videos/a.mp4")
                != store.key_for_video_path("/videos/b.mp4"))
        assert store.key_for_video_path("/videos/a.mp4").startswith("local-")


class _FakeYoutubeDL:
    """Faux yt-dlp : il n'ouvre aucune connexion et appelle simplement le
    crochet de progression comme le vrai le ferait."""

    last_options: dict = {}

    def __init__(self, options):
        self.options = options
        _FakeYoutubeDL.last_options = options

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def extract_info(self, url, download=True):
        for hook in self.options.get("progress_hooks", []):
            hook({"status": "downloading", "downloaded_bytes": 5_000_000,
                  "total_bytes": 20_000_000})
        target = self.options["outtmpl"].replace("%(id)s", "vid123").replace("%(ext)s", "mp4")
        from pathlib import Path

        Path(target).write_bytes(b"faux contenu")
        return {"id": "vid123", "height": 1080}


@pytest.fixture
def fake_ytdlp(monkeypatch):
    module = types.ModuleType("yt_dlp")
    module.YoutubeDL = _FakeYoutubeDL
    utils = types.ModuleType("yt_dlp.utils")

    class DownloadError(Exception):
        pass

    utils.DownloadError = DownloadError
    module.utils = utils
    monkeypatch.setitem(sys.modules, "yt_dlp", module)
    monkeypatch.setitem(sys.modules, "yt_dlp.utils", utils)
    return module


class TestTelechargementComplet:
    def test_sans_confirmation_des_droits_rien_n_est_telecharge(self, tmp_path):
        from utils.errors import RightsNotConfirmedError
        from youtube.downloader import download_video

        with pytest.raises(RightsNotConfirmedError):
            download_video("abc", str(tmp_path), consent_confirmed=False)
        assert not list(tmp_path.iterdir())

    def test_la_progression_est_rapportee_en_fraction_et_en_megaoctets(
            self, tmp_path, fake_ytdlp):
        from youtube.downloader import download_video

        seen = []
        path = download_video("abc", str(tmp_path), consent_confirmed=True,
                              on_progress=lambda f, d, t: seen.append((f, d, t)))
        assert path.endswith("vid123.mp4")
        assert seen == [(0.25, 5.0, 20.0)]

    def test_le_plafond_de_qualite_arrive_dans_le_selecteur_de_format(
            self, tmp_path, fake_ytdlp):
        from youtube.downloader import download_video

        download_video("abc", str(tmp_path), consent_confirmed=True, max_height=720)
        assert "[height<=720]" in _FakeYoutubeDL.last_options["format"]

    def test_la_meilleure_qualite_reste_le_defaut(self, tmp_path, fake_ytdlp):
        from youtube.downloader import download_video

        download_video("abc", str(tmp_path), consent_confirmed=True, max_height=0)
        assert "[height<=" not in _FakeYoutubeDL.last_options["format"]

    def test_l_annulation_interrompt_le_transfert(self, tmp_path, fake_ytdlp):
        from core.cancellation import CancelToken
        from utils.errors import CancelledError
        from youtube.downloader import download_video

        token = CancelToken()
        token.cancel()
        with pytest.raises(CancelledError):
            download_video("abc", str(tmp_path), consent_confirmed=True,
                           cancel_token=token)
