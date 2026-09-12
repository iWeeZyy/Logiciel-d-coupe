"""Que fait-on de ce qui ne rentre pas dans le cadre : recadrer, ou tout garder
et remplir les bords.

DEFAUT REEL A L'ORIGINE DE CE FICHIER. Le fond flou existait, mais uniquement
en 16:9 -- or une source deja en 16:9 remplit exactement un cadre 16:9 : il n'y
avait rien a remplir, donc jamais de flou visible. Sur un clip Twitch (1920x1080)
l'option ne pouvait rien faire, et c'est bien ce qui a ete constate en usage.
Le flou n'a de sens que la ou des bandes existent vraiment : un clip horizontal
poste en vertical, sur Instagram ou TikTok.

Tests PURS : la chaine de filtres construite, les reglages lus, l'etat des
commandes. Aucun encodage.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.config_loader import load_settings
from editing.timeline import EditList
from video.cropper import LANDSCAPE_SIZE, PORTRAIT_SIZE
from video.filter_graph import (
    FILL_BLACK,
    FILL_BLUR,
    FIT_CROP,
    FIT_WHOLE,
    build_ffmpeg_args,
    build_video_chain,
)


def chain(**kwargs) -> str:
    params = dict(edit_list=EditList.identity(0.0, 10.0), framing_plan=None,
                  zoom_track=None, src_w=1920, src_h=1080, fps=25.0)
    params.update(kwargs)
    return build_video_chain(**params)


def _cli(**overrides) -> SimpleNamespace:
    base = dict(input="x.mp4", clip_duration=30, nb_clips=1, language=None,
                pre_roll=None, post_roll=None, min_gap=None, subtitle_style=None,
                model="small", device=None, no_cache=False, debug_scores=False)
    base.update(overrides)
    return SimpleNamespace(**base)


class TestCadrageVertical:
    def test_le_recadrage_reste_le_defaut(self):
        """Le comportement historique ne change pas : sans rien demander, le
        9:16 recadre comme il l'a toujours fait."""
        produced = chain(target_size=PORTRAIT_SIZE)
        assert "crop=" in produced
        assert "gblur" not in produced

    def test_l_image_entiere_remplace_le_recadrage_par_un_remplissage(self):
        produced = chain(target_size=PORTRAIT_SIZE, fit=FIT_WHOLE, fill=FILL_BLUR)
        assert "crop=1080:1920" in produced, "le fond doit couvrir tout le cadre"
        assert "gblur" in produced
        assert "force_original_aspect_ratio=decrease" in produced, \
            "l'image nette doit tenir en entier"

    def test_l_image_entiere_avec_bandes_noires(self):
        produced = chain(target_size=PORTRAIT_SIZE, fit=FIT_WHOLE, fill=FILL_BLACK)
        assert "pad=1080:1920" in produced
        assert "gblur" not in produced

    def test_rien_n_est_deforme_dans_aucun_des_deux_cas(self):
        """Ni etirement ni ecrasement : la proportion d'origine est toujours
        preservee, on choisit seulement ce qu'on perd ou ce qu'on ajoute."""
        for fit in (FIT_CROP, FIT_WHOLE):
            produced = chain(target_size=PORTRAIT_SIZE, fit=fit, fill=FILL_BLUR)
            assert "setsar" not in produced
            assert "scale=1080:1920," not in produced.replace(
                "scale=1080:1920:force_original_aspect_ratio", "")

    def test_les_sous_titres_sont_incrustes_dans_les_deux_cas(self):
        for fit in (FIT_CROP, FIT_WHOLE):
            produced = chain(target_size=PORTRAIT_SIZE, fit=fit, fill=FILL_BLUR,
                             ass_path="/tmp/x.ass")
            assert "subtitles=" in produced

    def test_le_suivi_du_sujet_est_ignore_quand_tout_est_garde(self):
        """Il n'y a plus de fenetre a deplacer : suivre un visage n'aurait
        aucun effet sur l'image produite."""
        from editing.framing import FramingKeyframe, FramingPlan

        plan = FramingPlan(
            keyframes=(FramingKeyframe(t=0.0, cx=0.2, cy=0.5),
                       FramingKeyframe(t=5.0, cx=0.8, cy=0.5)),
            mode="track", confidence=0.9)
        produced = chain(target_size=PORTRAIT_SIZE, fit=FIT_WHOLE, fill=FILL_BLUR,
                         framing_plan=plan)
        assert "crop=1080:1920" in produced
        assert ":x='" not in produced, "aucune trajectoire ne doit rester"


class TestPaysageInchange:
    def test_le_16_9_ne_depend_pas_du_choix_de_cadrage(self):
        """En paysage l'image etait deja gardee entiere : le nouveau reglage ne
        doit rien y changer, dans un sens comme dans l'autre."""
        for fill in (FILL_BLUR, FILL_BLACK):
            assert (chain(target_size=LANDSCAPE_SIZE, fill=fill, fit=FIT_CROP)
                    == chain(target_size=LANDSCAPE_SIZE, fill=fill, fit=FIT_WHOLE))

    def test_une_source_deja_au_format_ne_calcule_aucun_fond(self):
        """Une source qui a DEJA la forme du cadre n'a rien a remplir.

        Le graphe passait quand meme par le fond flou : un flou gaussien
        calcule sur chaque image, puis integralement recouvert par l'image
        nette. Mesure sur une source 1080x1920, l'encodage passait de 0,9 s a
        1,9 s pour un rendu dont l'ecart de luminance avec la simple mise a
        l'echelle est exactement 0.
        """
        for src_w, src_h, taille in ((1920, 1080, LANDSCAPE_SIZE),
                                     (1280, 720, LANDSCAPE_SIZE),
                                     (1080, 1920, PORTRAIT_SIZE),
                                     (720, 1280, PORTRAIT_SIZE)):
            produced = chain(src_w=src_w, src_h=src_h, target_size=taille,
                             fill=FILL_BLUR, fit=FIT_WHOLE)
            assert "gblur" not in produced, (src_w, src_h)
            assert f"scale={taille[0]}:{taille[1]}" in produced

    def test_un_format_seulement_PROCHE_garde_le_fond(self):
        """La condition est une egalite entiere des formats, volontairement
        stricte : c'est ce qui garantit qu'aucune bande d'un pixel ne peut
        apparaitre par arrondi du redimensionnement."""
        produced = chain(src_w=1080, src_h=1918, target_size=PORTRAIT_SIZE,
                         fill=FILL_BLUR, fit=FIT_WHOLE)
        assert "gblur" in produced

    def test_une_source_verticale_en_16_9_garde_son_fond_flou(self):
        """Le cas ou le fond sert vraiment : l'image ne couvre pas le cadre."""
        produced = chain(src_w=1080, src_h=1920, target_size=LANDSCAPE_SIZE,
                         fill=FILL_BLUR)
        assert "gblur" in produced
        assert "force_original_aspect_ratio=increase" in produced


class TestCommandeComplete:
    def _args(self, **overrides):
        params = dict(
            video_path="s.mp4", edit_list=EditList.identity(0.0, 6.0),
            framing_plan=None, zoom_track=None, src_w=1920, src_h=1080, fps=24.0,
            face_hint=None, ass_path=None, audio_cfg=None,
            export_settings={"video_preset": "medium", "video_bitrate_crf": 18},
            out_mp4_path="o.mp4", target_size=PORTRAIT_SIZE,
        )
        params.update(overrides)
        return build_ffmpeg_args(**params)

    def test_le_choix_arrive_jusqu_a_ffmpeg(self):
        args = self._args(fit=FIT_WHOLE, fill=FILL_BLUR)
        assert "gblur" in " ".join(args)

    def test_le_defaut_reste_le_recadrage(self):
        assert "gblur" not in " ".join(self._args())

    def test_un_seul_encodage_dans_les_deux_cas(self):
        for fit in (FIT_CROP, FIT_WHOLE):
            assert self._args(fit=fit, fill=FILL_BLUR).count("-c:v") == 1

    def test_le_filigrane_se_pose_par_dessus_le_remplissage(self):
        from video.watermark import Watermark

        args = self._args(fit=FIT_WHOLE, fill=FILL_BLUR,
                          watermark=Watermark(image="logo.png"))
        graph = args[args.index("-filter_complex") + 1]
        assert graph.index("gblur") < graph.index("overlay=(W-w)/2:H-h")


class TestReglages:
    def test_le_recadrage_est_le_defaut_sans_option(self):
        assert load_settings(_cli()).fit_mode == FIT_CROP

    def test_l_option_de_ligne_de_commande_est_lue(self):
        assert load_settings(_cli(fit_mode="entier")).fit_mode == FIT_WHOLE

    def test_le_remplissage_reste_independant_du_cadrage(self):
        settings = load_settings(_cli(fit_mode="entier", black_bars=True))
        assert settings.fit_mode == FIT_WHOLE
        assert settings.fill_mode == FILL_BLACK


class TestInterfaceDeProduction:
    """L'etat des commandes doit dire la verite : une commande sans effet est
    grisee, jamais laissee active."""

    @pytest.fixture
    def box(self):
        pytest.importorskip("PySide6")
        import os

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        from gui.widgets.production_options import ProductionOptionsBox

        app = QApplication.instance() or QApplication([])
        assert app is not None
        return ProductionOptionsBox(columns=3)

    def _select_fit(self, box, value):
        box.fit_combo.setCurrentIndex(box.fit_combo.findData(value))

    def test_le_recadrage_est_coche_au_depart(self, box):
        assert box.fit_mode() == FIT_CROP
        assert not box.keeps_whole_image()

    def test_le_remplissage_est_grise_quand_on_recadre(self, box):
        assert not box.blur_check.isEnabled(), "rien a remplir en recadrant"

    def test_le_remplissage_devient_utile_avec_l_image_entiere(self, box):
        self._select_fit(box, FIT_WHOLE)
        assert box.blur_check.isEnabled()
        assert box.fill_mode() == FILL_BLUR

    def test_le_cadrage_intelligent_est_coupe_quand_tout_est_garde(self, box):
        self._select_fit(box, FIT_WHOLE)
        assert box.editing_overrides()["framing"] is False
        assert not box.boxes["framing"].isEnabled()

    def test_le_choix_de_cadrage_ne_sert_qu_en_vertical(self, box):
        box.aspect_combo.setCurrentIndex(1)          # 16:9
        assert not box.fit_combo.isEnabled()

    def test_revenir_au_recadrage_rend_le_cadrage_intelligent(self, box):
        self._select_fit(box, FIT_WHOLE)
        self._select_fit(box, FIT_CROP)
        assert box.boxes["framing"].isEnabled()
        assert box.editing_overrides()["framing"] is True


class TestVoiceStudio:
    """Le meme choix dans la creation video, avec le meme vocabulaire."""

    def test_les_valeurs_sont_celles_du_pipeline_video(self):
        from voice_studio import video_edit

        assert video_edit.FIT_CROP == FIT_CROP
        assert video_edit.FIT_WHOLE == FIT_WHOLE

    def test_l_image_entiere_remplit_le_cadre_vertical(self):
        from voice_studio import video_edit

        graph = video_edit.build_video_graph(
            src_w=1920, src_h=1080, target_size=(1080, 1920), fill="flou",
            ass_path=None, fit=video_edit.FIT_WHOLE)
        assert "gblur" in graph

    def test_le_recadrage_reste_le_defaut(self):
        from voice_studio import video_edit

        graph = video_edit.build_video_graph(
            src_w=1920, src_h=1080, target_size=(1080, 1920), fill="flou",
            ass_path=None)
        assert "gblur" not in graph
        assert "crop=" in graph

    def test_le_reglage_survit_a_un_aller_retour(self):
        from voice_studio.models import VideoSettings

        settings = VideoSettings(aspect_ratio="9:16", fit=FIT_WHOLE)
        assert VideoSettings.from_dict(settings.to_dict()).fit == FIT_WHOLE

    def test_aucune_detection_de_visage_quand_tout_est_garde(self):
        """Une passe de detection sur une video entiere coute des minutes pour
        un resultat que le rendu ignorerait."""
        from voice_studio import video_edit, video_service
        from voice_studio.models import VideoSettings

        request = video_service.VideoRequest(
            source_video="s.mp4", script="x", out_path="o.mp4",
            settings=VideoSettings(aspect_ratio="9:16", fit=FIT_WHOLE,
                                   framing=video_edit.FRAMING_SUBJECT))
        hint, plan, notes = video_service._framing(
            request, 60.0, video_service.Reporter(), None)
        assert hint is None and plan is None and notes == []
