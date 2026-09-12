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


class TestAlgorithmeDeRedimensionnement:
    """Quel interpolateur produit l'image finale.

    POURQUOI C'EST LE LEVIER DE QUALITE ICI. Un clip vertical est presque
    toujours un AGRANDISSEMENT : la fenetre 9:16 d'une source 1280x720 ne fait
    que 404x720, soit un facteur 2,67 pour atteindre 1080x1920. C'est la plus
    grosse perte de nettete de toute la chaine, bien avant le zoom.

    Mesure (maitre 3840x2160 a detail fin, energie des hautes frequences de la
    sortie) : bicubique 4,94 -> lanczos 5,28 en source 720p, 4,96 -> 5,33 en
    source 1080p. Sur un encodage complet aux reglages de production, le temps
    est identique et le fichier grossit de 3 %.
    """

    def test_l_image_nette_est_redimensionnee_en_lanczos(self):
        produced = chain(target_size=PORTRAIT_SIZE)
        assert "scale=1080:1920:flags=lanczos" in produced

    def test_le_fond_floute_garde_le_defaut(self):
        """Soigner l'interpolation d'une image qui finit floutee serait du
        temps depense pour rien."""
        produced = chain(src_w=1080, src_h=1920, target_size=LANDSCAPE_SIZE,
                         fill=FILL_BLUR)
        # Decoupe par etiquette de sortie : « [vsbg] » apparait aussi dans la
        # declaration du split, donc chercher la premiere occurrence designe
        # la mauvaise branche.
        segments = produced.split(";")
        fond = next(seg for seg in segments if "gblur" in seg)
        assert "flags=" not in fond
        net = next(seg for seg in segments if seg.startswith("[vsfg]"))
        assert "flags=lanczos" in net

    def test_l_etage_du_zoom_aussi(self):
        """C'est le redimensionnement qui porte le detail avant que zoompan
        choisisse sa fenetre."""
        from editing.zoom import ZoomKeyframe, ZoomTrack

        track = ZoomTrack(keyframes=(ZoomKeyframe(t=1.0, zoom=1.0),
                                     ZoomKeyframe(t=1.5, zoom=1.08),
                                     ZoomKeyframe(t=2.0, zoom=1.0)), events=1)
        produced = chain(target_size=PORTRAIT_SIZE, zoom_track=track)
        etage = produced.split("zoompan")[0]
        assert "flags=lanczos" in etage
        assert "zoompan" in produced

    def test_le_choix_est_relu_a_chaque_appel(self):
        """PIEGE REEL, rencontre en ecrivant ce code : une valeur par defaut
        d'argument est evaluee UNE SEULE FOIS, a la definition de la fonction.
        Tant que SCALE_FLAGS servait de valeur par defaut, le remplacer
        n'avait aucun effet -- et une mesure comparant les deux variantes les
        a trouvees identiques, ce qui etait le bug et non le resultat.
        """
        from video import filter_graph

        garde = filter_graph.SCALE_FLAGS
        try:
            filter_graph.SCALE_FLAGS = "bicubic"
            assert filter_graph._scale(1080, 1920) == "scale=1080:1920:flags=bicubic"
        finally:
            filter_graph.SCALE_FLAGS = garde
        assert filter_graph._scale(1080, 1920).endswith("flags=lanczos")

    def test_none_veut_dire_le_defaut_de_ffmpeg(self):
        """Distinct de « non precise » : c'est ce qui permet au fond floute de
        demander explicitement l'absence d'option."""
        from video.filter_graph import _scale

        assert _scale(1080, 1920, flags=None) == "scale=1080:1920"


class TestCompensationDeLAgrandissement:
    """Le masque flou qui recupere le detail perdu a l'agrandissement.

    CE QUE LA MESURE A ETABLI, contre un maitre 3840x2160 servant de verite,
    sur deux images differentes et en geometrie a mappage entier :

      agrandissement   force optimale   gain en PSNR
      x1,78            0,20 a 0,35      +0,17 / +0,22 dB
      x2,67            0,50 a 0,80      +0,30 / +0,20 dB
      x3,58            0,80 a 1,00      +0,26 / +0,19 dB

    Deux garde-fous que ces tests defendent : la force doit CROITRE avec
    l'agrandissement (une force forte sur un faible agrandissement coute
    -0,54 dB), et il ne doit y avoir AUCUN filtre quand il n'y a rien a
    recuperer (sur une source deja a la taille de sortie, le masque flou
    mesure -65 dB : l'image n'est plus elle-meme).
    """

    def test_aucun_filtre_sans_agrandissement(self):
        from video.filter_graph import sharpen_amount

        assert sharpen_amount(1.0) == 0.0
        assert sharpen_amount(0.5) == 0.0, "une reduction n'a rien a recuperer"
        assert sharpen_amount(0) == 0.0, "taille inconnue : on ne devine pas"

    def test_la_force_croit_avec_l_agrandissement(self):
        from video.filter_graph import sharpen_amount

        forces = [sharpen_amount(f) for f in (1.2, 1.5, 1.78, 2.2, 2.67)]
        assert forces == sorted(forces)
        assert len(set(forces)) == len(forces), "chaque agrandissement a sa force"

    def test_les_forces_mesurees_tombent_dans_la_plage_optimale(self):
        """Les plages viennent du banc de mesure, pas d'une intuition."""
        from video.filter_graph import sharpen_amount

        assert 0.20 <= sharpen_amount(1.78) <= 0.35
        assert 0.50 <= sharpen_amount(2.67) <= 0.80
        assert 0.80 <= sharpen_amount(3.58) <= 1.00

    def test_la_force_est_plafonnee(self):
        """Au-dela, les halos se voient plus que le detail recupere."""
        from video.filter_graph import SHARPEN_MAX, sharpen_amount

        assert sharpen_amount(20.0) == SHARPEN_MAX

    def test_la_chrominance_reste_intacte(self):
        """Accentuer la couleur d'une image 4:2:0 produit des franges sur les
        contours pour un gain de nettete nul."""
        from video.filter_graph import _sharpen

        assert _sharpen(2.67).endswith(":5:5:0.0")

    def test_une_source_720p_est_compensee(self):
        """Le cas le plus courant : un clip Twitch 1280x720, dont la fenetre
        9:16 ne fait que 404 px de large."""
        produced = chain(src_w=1280, src_h=720, target_size=PORTRAIT_SIZE)
        assert "unsharp=" in produced
        # l'ordre compte : apres la mise a l'echelle, jamais avant
        assert produced.index("scale=") < produced.index("unsharp=")

    def test_une_source_deja_a_la_taille_n_est_pas_touchee(self):
        produced = chain(src_w=1080, src_h=1920, target_size=PORTRAIT_SIZE,
                         fit=FIT_WHOLE)
        assert "unsharp=" not in produced

    def test_une_source_reduite_n_est_pas_touchee(self):
        """Une source verticale dans un cadre horizontal est REDUITE pour
        tenir : il n'y a aucun adoucissement a compenser."""
        produced = chain(src_w=1080, src_h=1920, target_size=LANDSCAPE_SIZE,
                         fill=FILL_BLUR)
        assert "unsharp=" not in produced

    def test_le_fond_floute_n_est_jamais_accentue(self):
        """Accentuer un fond deliberement floute est contradictoire, et sur un
        degrade lisse un masque flou fait apparaitre des bandes."""
        produced = chain(src_w=480, src_h=856, target_size=PORTRAIT_SIZE,
                         fit=FIT_WHOLE, fill=FILL_BLUR)
        segments = produced.split(";")
        fond = next(seg for seg in segments if "gblur" in seg)
        assert "unsharp" not in fond
        net = next(seg for seg in segments if seg.startswith("[vsfg]"))
        assert "unsharp" in net, "l'image nette, elle, est compensee"

    def test_la_fenetre_decide_et_non_l_image_entiere(self):
        """C'est la fenetre 9:16 qui doit remplir le cadre, pas la source : une
        source 1280x720 n'est pas agrandie 0,84 fois mais 2,67 fois."""
        from video.filter_graph import base_crop_size, sharpen_amount

        fenetre = base_crop_size(1280, 720)[0]
        attendue = sharpen_amount(PORTRAIT_SIZE[0] / fenetre)
        produced = chain(src_w=1280, src_h=720, target_size=PORTRAIT_SIZE)
        assert f"unsharp=5:5:{attendue:g}:" in produced

    def test_le_zoom_ne_change_pas_la_force(self):
        """Le zoom augmente l'agrandissement pendant quelques dixiemes de
        seconde. On ne suit pas cette variation : la force resterait dans la
        meme plage, et le filtre ne sait pas s'animer proprement."""
        from editing.zoom import ZoomKeyframe, ZoomTrack

        track = ZoomTrack(keyframes=(ZoomKeyframe(t=1.0, zoom=1.0),
                                     ZoomKeyframe(t=1.5, zoom=1.08),
                                     ZoomKeyframe(t=2.0, zoom=1.0)), events=1)
        sans = chain(src_w=1280, src_h=720, target_size=PORTRAIT_SIZE)
        avec = chain(src_w=1280, src_h=720, target_size=PORTRAIT_SIZE, zoom_track=track)
        force = sans.split("unsharp=")[1]
        assert avec.split("unsharp=")[1] == force

    def test_l_accentuation_precede_les_sous_titres(self):
        """Accentuer un texte deja net lui ajoute des halos : les sous-titres
        sont incrustes APRES."""
        produced = chain(src_w=1280, src_h=720, target_size=PORTRAIT_SIZE,
                         ass_path="/tmp/x.ass")
        assert produced.index("unsharp=") < produced.index("subtitles=")
