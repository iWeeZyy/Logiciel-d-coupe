"""Themes du montage delire : ambiance continue, choisie a la main.

Couvre trois couches, chacune verifiable sans encoder une image :

- editing/delire.py : THEMES, Plan.theme, build_theme_plan() -- le plan est
  pur, un theme ou rien.
- video/delire_theme.py : le catalogue des calques (fichier, defilement,
  opacite) et la compensation de cadence.
- video/delire_theme_filters.py : la traduction en filtres ffmpeg
  (vignette+desaturation pour "mystere", calques prets a brancher pour les
  quatre autres) et le branchement reel dans build_ffmpeg_args
  (video/filter_graph.py), verifie par construction des arguments -- jamais
  par un encodage reel.
"""
from __future__ import annotations

import pytest

from editing.delire import (
    DEFAULT_LEVEL,
    THEME_BRAISES,
    THEME_CONFETTIS,
    THEME_ETOILES,
    THEME_MYSTERE,
    THEME_PLUIE,
    THEMES,
    Plan,
    build_plan,
    build_theme_plan,
)
from editing.timeline import EditList
from video.delire_theme import asset_path, layers_for, scroll_vertical_for_fps
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
        plan = build_theme_plan(THEME_ETOILES)
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
        assert build_theme_plan(THEME_BRAISES).to_dict()["theme"] == THEME_BRAISES
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

    def test_pluie_defile_vers_le_bas(self):
        (couche,) = layers_for(THEME_PLUIE)
        assert couche.scroll_vertical > 0

    def test_braises_defilent_vers_le_haut(self):
        (couche,) = layers_for(THEME_BRAISES)
        assert couche.scroll_vertical < 0

    def test_etoiles_porte_deux_calques_dont_un_statique(self):
        """Le ciel scintille (defilement tres lent), l'arc-en-ciel ne bouge
        pas du tout -- c'est la seule des cinq textures sans defilement."""
        couches = layers_for(THEME_ETOILES)
        assert len(couches) == 2
        vitesses = [c.scroll_vertical for c in couches]
        assert any(v == 0.0 for v in vitesses)
        assert any(v != 0.0 for v in vitesses)

    def test_chaque_calque_a_un_fichier_reel_sur_le_disque(self):
        """Les PNG sont generes une fois par tools/generate_delire_assets.py
        et COMMITES -- ce test verifie qu'ils n'ont pas ete oublies."""
        for theme in THEMES:
            for couche in layers_for(theme):
                chemin = asset_path(couche.filename)
                assert chemin.is_file(), f"{chemin} absent (relancer tools/generate_delire_assets.py ?)"


class TestCompensationDeCadence:
    def test_a_la_cadence_de_reference_rien_ne_change(self):
        assert scroll_vertical_for_fps(0.012, 30.0) == pytest.approx(0.012)

    def test_une_cadence_double_divise_la_vitesse_par_deux(self):
        """60 im/s produit deux fois plus d'increments par seconde qu'a 30 :
        diviser la vitesse par deux compense exactement."""
        assert scroll_vertical_for_fps(0.012, 60.0) == pytest.approx(0.006)

    def test_une_cadence_nulle_ou_absente_retombe_sur_la_reference(self):
        assert scroll_vertical_for_fps(0.012, 0.0) == pytest.approx(0.012)
        assert scroll_vertical_for_fps(0.012, None) == pytest.approx(0.012)


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

    def test_pluie_produit_un_calque_boucle_qui_defile(self):
        (couche,) = overlay_layers(THEME_PLUIE, 1080, 1920, 30.0)
        assert couche.needs_loop is True
        assert "scale=1080:1920" in couche.prep_filter
        assert "scroll=vertical=" in couche.prep_filter
        assert couche.position == "0:0"

    def test_etoiles_le_calque_statique_n_a_pas_besoin_de_boucle(self):
        couches = overlay_layers(THEME_ETOILES, 1080, 1920, 30.0)
        assert len(couches) == 2
        boucles = [c.needs_loop for c in couches]
        assert boucles.count(True) == 1
        assert boucles.count(False) == 1
        statique = next(c for c in couches if not c.needs_loop)
        assert "scroll=" not in statique.prep_filter

    def test_la_mise_a_l_echelle_precede_toujours_le_defilement(self):
        """`scroll` deplace le calque d'une fraction de SA PROPRE hauteur :
        la mise a l'echelle doit avoir deja eu lieu, sinon la fraction ne
        correspond pas au cadre de sortie."""
        (couche,) = overlay_layers(THEME_BRAISES, 1080, 1920, 30.0)
        assert couche.prep_filter.index("scale=") < couche.prep_filter.index("scroll=")


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
        donc il ne force PAS filter_complex, contrairement aux quatre autres."""
        args = _args(delire_plan=build_theme_plan(THEME_MYSTERE))
        assert "-vf" in args and "-filter_complex" not in args
        vf = args[args.index("-vf") + 1]
        assert "vignette" in vf

    def test_un_theme_particulaire_force_filter_complex(self):
        args = _args(delire_plan=build_theme_plan(THEME_PLUIE))
        assert "-vf" not in args and "-filter_complex" in args
        assert "-loop" in args and "1" in args[args.index("-loop") + 1:args.index("-loop") + 2]
        graph = args[args.index("-filter_complex") + 1]
        assert "scroll=vertical=" in graph
        assert "[base][ov0]overlay=0:0[vout]" in graph

    def test_le_calque_supplementaire_est_une_entree_avant_le_t(self):
        args = _args(delire_plan=build_theme_plan(THEME_PLUIE))
        assert args[0:2] == ["-ss", "10.000"]
        assert "-t" in args
        loop_index = args.index("-loop")
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

    def test_etoiles_pose_deux_calques_dans_l_ordre(self):
        args = _args(delire_plan=build_theme_plan(THEME_ETOILES))
        graph = args[args.index("-filter_complex") + 1]
        assert graph.count("overlay=") == 2
        assert "[base][ov0]overlay=0:0[stage0]" in graph
        assert "[stage0][ov1]overlay=0:0[vout]" in graph

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
        from editing.delire import THEME_BRAISES

        options.set_theme(THEME_BRAISES)
        assert options.theme() == THEME_BRAISES

    def test_choisir_un_theme_porte_dans_editing_overrides(self, options):
        from editing.delire import THEME_ETOILES

        options.boxes["delire"].setChecked(True)
        options.set_theme(THEME_ETOILES)
        bloc = options.editing_overrides()["delire"]
        assert bloc == {"enabled": True, "level": options.delire_level(), "theme": THEME_ETOILES}

    def test_choisir_un_theme_grise_le_cran_d_intensite(self, options):
        """Le cran n'a plus d'effet des qu'un theme remplace les rafales --
        le grisage le dit, pour ne pas laisser croire le contraire."""
        from editing.delire import THEME_PLUIE

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
