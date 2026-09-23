"""Themes du montage delire : ambiance continue, choisie a la main.

Couvre trois couches, chacune verifiable sans encoder une image :

- editing/delire.py : THEMES, Plan.theme, build_theme_plan() -- le plan est
  pur, un theme ou rien.
- video/delire_theme.py : le catalogue des calques (fichier, ajustement de
  cadre cover/contain, opacite, parametres de chromakey PAR CLIP).
- video/delire_theme_filters.py : la traduction en filtres ffmpeg
  (vignette+desaturation pour "mystere", un calque chromakey pret a brancher
  pour chacun des neuf autres) et le branchement reel dans build_ffmpeg_args
  (video/filter_graph.py), verifie par construction des arguments -- jamais
  par un encodage reel.
"""
from __future__ import annotations

import pytest

from editing.delire import (
    DEFAULT_LEVEL,
    THEME_CHIMPANZEE,
    THEME_CONFETTIS,
    THEME_FLAMMES,
    THEME_INFOS,
    THEME_MANGA,
    THEME_MYSTERE,
    THEME_PLUIE,
    THEMES,
    Plan,
    build_plan,
    build_theme_plan,
)
from editing.timeline import EditList
from video.delire_theme import FIT_CONTAIN, FIT_COVER, asset_path, layers_for
from video.delire_theme_filters import mystere_chain_filters, overlay_layers
from video.filter_graph import build_ffmpeg_args


# ---------------------------------------------------- editing/delire.py

class TestBuildThemePlan:
    def test_un_theme_connu_produit_un_plan_non_vide(self):
        plan = build_theme_plan(THEME_PLUIE)
        assert plan.theme == THEME_PLUIE
        assert not plan.is_empty

    def test_aucun_evenement_n_est_pose_pour_un_theme(self):
        """Un theme est une ambiance, pas des rafales : `events` reste vide,
        c'est `theme` qui porte l'information."""
        plan = build_theme_plan(THEME_CHIMPANZEE)
        assert plan.events == ()

    @pytest.mark.parametrize("valeur", ["", "hiver", "PLUIE", "  pluie  ", None])
    def test_un_theme_inconnu_ou_vide_renvoie_un_plan_ordinaire_vide(self, valeur):
        """Mieux vaut un clip sobre qu'un theme invente -- meme principe que
        build_plan() pour un clip sans moment marquant."""
        plan = build_theme_plan(valeur)
        assert plan == Plan()
        assert plan.is_empty

    def test_tous_les_themes_catalogues_fonctionnent(self):
        for theme in THEMES:
            plan = build_theme_plan(theme)
            assert plan.theme == theme

    def test_to_dict_porte_le_theme(self):
        assert build_theme_plan(THEME_MANGA).to_dict()["theme"] == THEME_MANGA
        assert build_plan([], 0.0, 10.0).to_dict()["theme"] == ""


class TestUnThemeEstDistinctDesRafales:
    def test_build_plan_ne_renseigne_jamais_theme(self):
        """`build_plan` (les rafales) et `build_theme_plan` (les themes) sont
        deux portes d'entree distinctes vers le meme `Plan` -- seule la
        seconde peut renseigner `theme`."""
        from editing.delire import Moment

        plan = build_plan([Moment(t=5.0, text="quoi")], 0.0, 10.0, level=DEFAULT_LEVEL)
        assert plan.theme == ""


# --------------------------------------------------- video/delire_theme.py

class TestCatalogueDesCalques:
    def test_mystere_n_a_aucun_calque(self):
        assert layers_for(THEME_MYSTERE) == ()

    def test_un_theme_inconnu_n_a_aucun_calque(self):
        assert layers_for("hiver") == ()

    def test_chaque_theme_particulaire_porte_exactement_un_calque(self):
        """Contrairement a l'ancienne generation (certains themes empilaient
        deux textures generees), chaque theme video n'incruste qu'UN rush
        fond vert -- un second calque n'aurait rien a apporter."""
        for theme in THEMES:
            if theme == THEME_MYSTERE:
                continue
            assert len(layers_for(theme)) == 1

    def test_chaque_calque_porte_une_couleur_mesuree_sur_son_propre_clip(self):
        """Les neuf rushes sont des tournages reels, pas des captures
        calibrees : leur vert differe legerement d'un fichier a l'autre,
        donc AUCUN calque ne doit retomber sur la valeur par defaut du
        dataclass (0x00FF00, un vert pur qu'aucun des neuf rushes n'a).
        Deux clips peuvent malgre tout partager la MEME couleur mesuree par
        coincidence (chimpanzee et intelligence, tournes sur le meme fond) --
        ce test ne l'exclut donc pas."""
        for theme in THEMES:
            for couche in layers_for(theme):
                assert couche.chroma_color != "0x00FF00"

    def test_le_degrade_de_transparence_reste_bas_partout(self):
        """REGRESSION A EVITER : un `chroma_blend` genereux (0.05 et plus)
        rend translucide tout pixel dont la teinte est SEULEMENT PROCHE du
        vert cle, pas seulement les bords du sujet decoupe -- constate sur
        "chimpanzee" compose contre un fond de test bariole (son pelage
        sombre a une composante chromatique legerement verdatre). Chaque
        calque doit donc rester sous ce plafond."""
        for theme in THEMES:
            for couche in layers_for(theme):
                assert couche.chroma_blend <= 0.05

    def test_flammes_est_ancre_en_bas(self):
        """Un feu doit toucher le bas du cadre, jamais flotter centre --
        constate en testant l'ancrage "center" dessus avant de corriger."""
        from video.delire_theme import ANCHOR_BOTTOM

        (couche,) = layers_for(THEME_FLAMMES)
        assert couche.fit == FIT_CONTAIN
        assert couche.anchor == ANCHOR_BOTTOM

    def test_infos_ne_rogne_jamais_le_cadre(self):
        """Le bandeau "LIVE" colle au bord gauche du rush source : un
        rognage (fit="cover") l'aurait coupe -- constate en testant "cover"
        dessus avant de choisir "contain"."""
        (couche,) = layers_for(THEME_INFOS)
        assert couche.fit == FIT_CONTAIN

    def test_chaque_calque_a_un_fichier_reel_sur_le_disque(self):
        """Les neuf videos sont livrees et COMMITEES -- ce test verifie
        qu'elles n'ont pas ete oubliees."""
        for theme in THEMES:
            for couche in layers_for(theme):
                chemin = asset_path(couche.filename)
                assert chemin.is_file(), f"{chemin} absent"


# ----------------------------------------- video/delire_theme_filters.py

class TestFiltresMystere:
    def test_deux_filtres_natifs_sans_intervalle(self):
        """Un theme est une ambiance continue : aucune option `enable`,
        contrairement aux rafales qui bornent leur intervalle."""
        filtres = mystere_chain_filters()
        assert any("vignette" in f for f in filtres)
        assert any("eq=" in f and "saturation=" in f for f in filtres)
        assert not any("enable=" in f for f in filtres)


class TestCalquesPrepares:
    def test_mystere_ne_produit_aucun_calque(self):
        assert overlay_layers(THEME_MYSTERE, 1080, 1920, 30.0) == []

    def test_un_theme_inconnu_ne_produit_aucun_calque(self):
        assert overlay_layers("", 1080, 1920, 30.0) == []

    def test_un_theme_particulaire_produit_un_calque_video_boucle(self):
        (couche,) = overlay_layers(THEME_PLUIE, 1080, 1920, 30.0)
        assert couche.is_video is True
        assert "chromakey=color=" in couche.prep_filter
        assert couche.position == "0:0"

    def test_le_chromakey_precede_toujours_le_format_rgba(self):
        """`format=rgba` doit voir le resultat du detourage, pas l'inverse --
        sinon le calque n'aurait jamais de canal alpha a composer."""
        (couche,) = overlay_layers(THEME_CONFETTIS, 1080, 1920, 30.0)
        assert couche.prep_filter.index("chromakey=") < couche.prep_filter.index("format=rgba")

    def test_la_mise_a_l_echelle_precede_toujours_le_chromakey(self):
        (couche,) = overlay_layers(THEME_MANGA, 1080, 1920, 30.0)
        assert couche.prep_filter.index("scale=") < couche.prep_filter.index("chromakey=")

    def test_fit_cover_rogne_apres_la_mise_a_l_echelle(self):
        (couche,) = overlay_layers(THEME_CHIMPANZEE, 1080, 1920, 30.0)
        assert "crop=1080:1920" in couche.prep_filter

    def test_fit_contain_ne_rogne_jamais_et_complete_par_un_bandeau_transparent(self):
        (couche,) = overlay_layers(THEME_INFOS, 1080, 1920, 30.0)
        assert "crop=" not in couche.prep_filter
        assert "pad=1080:1920" in couche.prep_filter
        assert "color=black@0.0" in couche.prep_filter


# --------------------------------------------- integration filter_graph.py

def _args(delire_plan=None, watermark=None, edit_list=None, **kwargs):
    return build_ffmpeg_args(
        video_path="in.mp4",
        edit_list=edit_list or EditList.identity(10.0, 40.0),
        framing_plan=None, zoom_track=None, src_w=1080, src_h=1920, fps=30.0,
        face_hint=None, ass_path=None, audio_cfg=None,
        export_settings={}, out_mp4_path="out.mp4",
        watermark=watermark, delire_plan=delire_plan, **kwargs,
    )


class TestBrancheDansLaChaineFfmpeg:
    def test_sans_theme_rien_ne_change(self):
        """REGRESSION A EVITER : un plan de rafales ordinaire (ou aucun plan)
        continue de produire exactement le chemin -vf d'avant, sans
        filter_complex."""
        args = _args(delire_plan=None)
        assert "-vf" in args and "-filter_complex" not in args

    def test_mystere_reste_sur_vf_lui_aussi(self):
        """"mystere" ne pose aucun calque -- ses filtres sont DANS video_chain
        (voir tests/test_delire.py::TestPlaceDansLaChaine pour ce niveau-la),
        donc il ne force PAS filter_complex, contrairement aux neuf autres."""
        args = _args(delire_plan=build_theme_plan(THEME_MYSTERE))
        assert "-vf" in args and "-filter_complex" not in args
        vf = args[args.index("-vf") + 1]
        assert "vignette" in vf

    def test_un_theme_particulaire_force_filter_complex(self):
        args = _args(delire_plan=build_theme_plan(THEME_PLUIE))
        assert "-vf" not in args and "-filter_complex" in args
        assert "-stream_loop" in args
        assert args[args.index("-stream_loop") + 1] == "-1"
        graph = args[args.index("-filter_complex") + 1]
        assert "chromakey=color=" in graph
        assert "[base][ov0]overlay=0:0[vout]" in graph

    def test_le_calque_supplementaire_est_une_entree_avant_le_t(self):
        args = _args(delire_plan=build_theme_plan(THEME_PLUIE))
        assert args[0:2] == ["-ss", "10.000"]
        assert "-t" in args
        loop_index = args.index("-stream_loop")
        t_index = args.index("-t")
        assert loop_index < t_index, "l'entree du calque doit precede -t"

    def test_theme_et_filigrane_se_composent_les_deux(self):
        from video.watermark import Watermark

        wm = Watermark(image="logo.png")
        args = _args(delire_plan=build_theme_plan(THEME_PLUIE), watermark=wm)
        graph = args[args.index("-filter_complex") + 1]
        # Deux calques : le theme (ov0) compose sur "base", puis le
        # filigrane (ov1) compose sur le resultat -- jamais l'inverse, le
        # logo doit rester visible par-dessus une texture qui couvre tout
        # le cadre.
        assert "[base][ov0]overlay=0:0[stage0]" in graph
        assert "[stage0][ov1]overlay=" in graph
        assert graph.count("overlay=") == 2
        # Trois entrees en tout : la video, le calque theme, le logo.
        assert args.count("-i") == 3

    def test_un_theme_avec_montage_coupe_compose_toujours(self):
        """Le meme calque doit se brancher aussi quand des silences sont
        retires (edit_list non identite) -- l'autre moitie de
        build_ffmpeg_args, deja testee pour le filigrane dans
        tests/test_watermark.py."""
        edl = EditList.keeping(10.0, 40.0, removed=[(20.0, 22.0)])
        args = _args(delire_plan=build_theme_plan(THEME_PLUIE), edit_list=edl)
        graph = args[args.index("-filter_complex") + 1]
        assert "concat=n=2:v=1:a=1" in graph
        assert "[base][ov0]overlay=0:0[vout]" in graph
        assert graph.count("overlay=") == 1


# ------------------------------------------------------- interface Qt

class TestLeMenuThemeDansLAccueil:
    @pytest.fixture
    def options(self):
        pytest.importorskip("PySide6")
        import os

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        from gui.widgets.production_options import ProductionOptionsBox

        QApplication.instance() or QApplication([])
        return ProductionOptionsBox()

    def test_aucun_theme_par_defaut(self, options):
        assert options.theme() == ""

    def test_tous_les_themes_catalogues_sont_proposes(self, options):
        from editing.delire import THEMES

        cles = {options.theme_combo.itemData(i) for i in range(options.theme_combo.count())}
        assert cles == {""} | set(THEMES)

    def test_set_theme_puis_theme_font_un_aller_retour(self, options):
        options.set_theme(THEME_MANGA)
        assert options.theme() == THEME_MANGA

    def test_choisir_un_theme_porte_dans_editing_overrides(self, options):
        options.boxes["delire"].setChecked(True)
        options.set_theme(THEME_CONFETTIS)
        bloc = options.editing_overrides()["delire"]
        assert bloc == {"enabled": True, "level": options.delire_level(), "theme": THEME_CONFETTIS}

    def test_choisir_un_theme_grise_le_cran_d_intensite(self, options):
        """Le cran n'a plus d'effet des qu'un theme remplace les rafales --
        le grisage le dit, pour ne pas laisser croire le contraire."""
        options.boxes["delire"].setChecked(True)
        assert options.delire_combo.isEnabled()
        options.set_theme(THEME_PLUIE)
        assert not options.delire_combo.isEnabled()
        options.set_theme("")
        assert options.delire_combo.isEnabled()

    def test_decocher_delire_grise_theme_et_intensite_ensemble(self, options):
        box = options.boxes["delire"]
        box.setChecked(True)
        box.setChecked(False)
        assert not options.theme_combo.isEnabled()
        assert not options.delire_combo.isEnabled()
