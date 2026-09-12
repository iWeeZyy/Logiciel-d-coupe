"""Styles de montage : un choix qui positionne format, sous-titres, zooms, delire.

CE QUE CES TESTS DEFENDENT, dans l'ordre d'importance :
  * qu'un style ne nomme JAMAIS un reglage qui n'existe pas -- un style de
    sous-titres inconnu fait echouer le traitement en fin de chaine, apres la
    transcription, c'est-a-dire au moment le plus couteux ;
  * que « Personnalise » n'applique rien du tout, donc que l'utilisateur qui
    n'ouvre pas ce menu obtienne exactement le rendu d'avant ;
  * que le menu ne mente pas : toucher un reglage a la main doit le faire
    repasser sur « Personnalise » ;
  * que surcharger l'ampleur d'un zoom ne fasse pas perdre son attaque, sa
    tenue et son relachement -- la fusion doit DESCENDRE dans les sous-blocs.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from editing import delire, presets
from video.cropper import ASPECT_LANDSCAPE, ASPECT_PORTRAIT
from video.filter_graph import FIT_CROP, FIT_WHOLE

REPO = Path(__file__).resolve().parents[1]


def _json(name: str) -> dict:
    return json.loads((REPO / "config" / name).read_text(encoding="utf-8"))


def _reels() -> list[presets.Preset]:
    """Les styles qui appliquent vraiment quelque chose."""
    return [p for p in presets.PRESETS if not p.is_free]


class TestLeCatalogue:
    def test_personnalise_est_le_premier_et_le_defaut(self):
        """C'est l'etat de depart : n'importe quel autre ordre changerait le
        comportement de l'accueil au simple ajout de ce menu."""
        assert presets.PRESETS[0].key == presets.FREE_KEY
        assert presets.DEFAULT_KEY == presets.FREE_KEY

    def test_les_cles_et_les_libelles_sont_uniques(self):
        cles = [p.key for p in presets.PRESETS]
        libelles = [p.label for p in presets.PRESETS]
        assert len(set(cles)) == len(cles)
        assert len(set(libelles)) == len(libelles)

    def test_chaque_style_est_decrit(self):
        for preset in presets.PRESETS:
            assert preset.description.strip(), preset.key

    def test_plusieurs_styles_sont_proposes(self):
        assert len(_reels()) >= 5

    def test_une_cle_inconnue_ne_leve_pas(self):
        """Un reglage enregistre par une version plus recente ne doit pas
        empecher la page de s'ouvrir."""
        assert presets.get("style_venu_d_ailleurs").is_free
        assert presets.get("").is_free
        assert presets.get(None).is_free


class TestAucunReglageInvente:
    """LE TEST LE PLUS IMPORTANT DU FICHIER.

    Un style qui nomme un style de sous-titres absent de config/subtitles.json
    fait echouer le traitement APRES la transcription : plusieurs minutes de
    calcul perdues pour une faute de frappe.
    """

    def test_les_styles_de_sous_titres_existent(self):
        connus = set(_json("subtitles.json")["styles"])
        for preset in _reels():
            assert preset.subtitle_style in connus, preset.key

    def test_les_crans_de_delire_existent(self):
        for preset in _reels():
            if preset.delire is not None:
                assert preset.delire in delire.LEVELS, preset.key

    def test_les_formats_et_cadrages_existent(self):
        for preset in presets.PRESETS:
            assert preset.aspect in (None, ASPECT_PORTRAIT, ASPECT_LANDSCAPE), preset.key
            assert preset.fit_mode in (None, FIT_CROP, FIT_WHOLE), preset.key

    def test_les_cles_de_zoom_existent_deja_dans_la_configuration(self):
        """On surcharge des reglages, on n'en cree pas : une cle inconnue de
        editing/montage.py serait lue par personne et ne ferait rien."""
        connues = set(_json("editing.json")["montage"]["dynamic_zoom"])
        for preset in _reels():
            for cle in preset.zoom:
                assert cle in connues, f"{preset.key} -> {cle}"


class TestPersonnaliseNApppliqueRien:
    def test_aucune_surcharge(self):
        assert presets.editing_overrides(presets.FREE_KEY) == {}

    def test_aucun_style_de_sous_titres(self):
        """Vide veut dire « garde celui des Parametres »."""
        assert not presets.get(presets.FREE_KEY).subtitle_style

    def test_aucun_format_impose(self):
        libre = presets.get(presets.FREE_KEY)
        assert libre.aspect is None and libre.fit_mode is None


class TestLesSurcharges:
    def test_le_delire_est_toujours_tranche(self):
        """Chaque style a un avis sur le delire : c'est ce qui les separe le
        plus. Un style muet laisserait le cran du run precedent."""
        for preset in _reels():
            bloc = presets.editing_overrides(preset.key)["delire"]
            assert bloc["enabled"] is (preset.delire is not None)
            if preset.delire is not None:
                assert bloc["level"] == preset.delire

    def test_le_zoom_passe_par_son_sous_bloc(self):
        surcharges = presets.editing_overrides("chaos")
        assert "dynamic_zoom" in surcharges["montage"]
        assert surcharges["montage"]["dynamic_zoom"]["max_zoom"] > 1.0

    def test_sobre_coupe_le_zoom_sans_couper_le_montage(self):
        """Couper le montage automatique retirerait aussi la coupe des
        silences et la normalisation du son, qui ne sont pas esthetiques."""
        surcharges = presets.editing_overrides("sobre")
        assert surcharges["montage"]["dynamic_zoom"]["enabled"] is False
        assert "enabled" not in surcharges["montage"]

    def test_aucun_style_ne_coupe_un_mecanisme(self):
        """Un style de montage est un parti pris esthetique : il n'a pas a
        eteindre les sous-titres, le cadrage ou les miniatures."""
        for preset in _reels():
            touches = set(presets.editing_overrides(preset.key))
            assert touches <= {"delire", "montage"}, preset.key

    def test_une_surcharge_ne_partage_rien_avec_le_catalogue(self):
        """Deux appels doivent etre independants : sinon un run modifierait le
        style pour tous les suivants."""
        premier = presets.editing_overrides("chaos")
        premier["montage"]["dynamic_zoom"]["max_zoom"] = 99.0
        assert presets.editing_overrides("chaos")["montage"]["dynamic_zoom"]["max_zoom"] < 2.0

    def test_les_styles_sont_reellement_differents(self):
        signatures = {(p.subtitle_style, p.delire, tuple(sorted(p.zoom.items())))
                      for p in _reels()}
        assert len(signatures) == len(_reels())


class TestLaFusionDescendDansLesSousBlocs:
    """Un `dict.update` remplacerait dynamic_zoom en entier : demander un zoom
    plus ample ferait perdre son attaque et sa tenue."""

    @pytest.fixture
    def outils(self):
        pytest.importorskip("PySide6")
        from gui.controller import _deep_copy_block, _deep_update

        return _deep_copy_block, _deep_update

    BLOC = {"enabled": True,
            "dynamic_zoom": {"enabled": True, "max_zoom": 1.08, "attack_s": 0.25,
                             "hold_s": 0.5, "max_events": 4}}

    def test_les_cles_non_fournies_survivent(self, outils):
        copier, fusionner = outils
        bloc = copier(self.BLOC)
        fusionner(bloc, {"dynamic_zoom": {"max_zoom": 1.18}})
        assert bloc["dynamic_zoom"]["attack_s"] == 0.25
        assert bloc["dynamic_zoom"]["hold_s"] == 0.5
        assert bloc["dynamic_zoom"]["max_zoom"] == 1.18

    def test_la_configuration_chargee_n_est_pas_modifiee(self, outils):
        copier, fusionner = outils
        bloc = copier(self.BLOC)
        fusionner(bloc, {"dynamic_zoom": {"max_zoom": 1.18}})
        assert self.BLOC["dynamic_zoom"]["max_zoom"] == 1.08

    def test_une_feuille_reste_remplacee(self, outils):
        copier, fusionner = outils
        bloc = copier(self.BLOC)
        fusionner(bloc, {"enabled": False})
        assert bloc["enabled"] is False


class TestLeMenuDansLInterface:
    @pytest.fixture
    def options(self):
        pytest.importorskip("PySide6")
        import os

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        from gui.widgets.production_options import ProductionOptionsBox

        QApplication.instance() or QApplication([])
        return ProductionOptionsBox()

    def test_tous_les_styles_sont_proposes(self, options):
        proposes = [options.preset_combo.itemData(i)
                    for i in range(options.preset_combo.count())]
        assert proposes == presets.keys()

    def test_personnalise_au_demarrage(self, options):
        assert options.preset() == presets.FREE_KEY
        assert options.subtitle_style() == ""

    def test_un_style_regle_les_commandes_visibles(self, options):
        """Les commandes sont reglees POUR DE VRAI : l'utilisateur voit ce qui
        va se passer et peut corriger."""
        options.set_preset("chaos")
        assert options.aspect() == ASPECT_PORTRAIT
        assert options.fit_mode() == FIT_CROP
        assert options.boxes["delire"].isChecked()
        assert options.delire_level() == "maximum"
        assert options.subtitle_style() == "secousse"

    def test_un_style_sans_delire_decoche_la_case(self, options):
        options.set_preset("chaos")
        options.set_preset("recit")
        assert not options.boxes["delire"].isChecked()
        assert options.editing_overrides()["delire"]["enabled"] is False

    def test_recit_garde_l_image_entiere(self, options):
        """Un recit perd son decor si on le rogne."""
        options.set_preset("recit")
        assert options.fit_mode() == FIT_WHOLE

    def test_sobre_ne_touche_pas_au_format(self, options):
        options.set_preset("recit")
        cadrage = options.fit_mode()
        options.set_preset("sobre")
        assert options.fit_mode() == cadrage

    def test_toucher_un_reglage_repasse_sur_personnalise(self, options):
        """Afficher « Chaos » au-dessus de reglages qui ne sont plus ceux de
        Chaos serait un mensonge de l'interface."""
        options.set_preset("chaos")
        options.set_delire_level("doux")
        assert options.preset() == presets.FREE_KEY

    def test_decocher_un_module_repasse_sur_personnalise(self, options):
        options.set_preset("punchline")
        options.boxes["captions"].setChecked(False)
        assert options.preset() == presets.FREE_KEY

    def test_changer_le_format_repasse_sur_personnalise(self, options):
        options.set_preset("punchline")
        index = options.aspect_combo.findData(ASPECT_LANDSCAPE)
        options.aspect_combo.setCurrentIndex(index)
        assert options.preset() == presets.FREE_KEY

    def test_appliquer_un_style_ne_le_perd_pas_lui_meme(self, options):
        """Le style regle les commandes, et ces reglages declenchent les memes
        signaux qu'une action manuelle : sans garde, le menu s'annulerait
        lui-meme aussitot."""
        options.set_preset("punchline")
        assert options.preset() == "punchline"

    def test_le_filigrane_n_annule_pas_le_style(self, options):
        """La chaine qui signe la video n'est pas un parti pris de montage."""
        options.set_preset("punchline")
        options.boxes["watermark"].setChecked(not options.boxes["watermark"].isChecked())
        assert options.preset() == "punchline"

    def test_les_surcharges_portent_le_zoom_du_style(self, options):
        options.set_preset("punchline")
        surcharges = options.editing_overrides()
        assert surcharges["montage"]["dynamic_zoom"]["max_events"] == 6

    def test_les_cases_restent_la_verite_sur_le_delire(self, options):
        """Decocher « Delire » apres avoir choisi Chaos doit couper le delire,
        pas le rallumer parce que le style le demandait."""
        options.set_preset("chaos")
        options.boxes["delire"].setChecked(False)
        assert options.editing_overrides()["delire"]["enabled"] is False

    def test_la_description_suit_le_choix(self, options):
        options.set_preset("neon")
        assert options.preset_hint.text() == presets.get("neon").description

    def test_personnalise_ne_surcharge_que_ce_que_les_cases_disent(self, options):
        """Sans style, « montage » ne porte que son interrupteur -- la forme
        exacte d'avant ce menu."""
        assert isinstance(options.editing_overrides()["montage"], bool)


class TestLeMemeMenuDepuisLeRadar:
    """Le menu doit etre la AUSSI quand on part d'un clip du Radar.

    Les deux chemins de production -- l'accueil et la fenetre d'analyse d'un
    clip -- partagent le meme composant d'options, precisement pour qu'une
    commande presente d'un cote ne manque pas de l'autre. Ces tests defendent
    ce partage : c'est le genre d'oubli qui ne se voit qu'a l'usage.
    """

    @pytest.fixture
    def fenetre(self, tmp_path):
        pytest.importorskip("PySide6")
        import os

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        from gui.radar.analysis_dialog import ClipAnalysisDialog
        from radar.models import Opportunity
        from radar.store import RadarStore

        QApplication.instance() or QApplication([])
        clip = Opportunity(platform="twitch", content_id="abc", kind="clip",
                           creator_key="twitch:qui", title="Un clip",
                           duration_s=32)
        # Base dans un dossier temporaire : RadarStore accepte un chemin pour
        # cela, et sans lui le test ecrirait dans la base reelle.
        return ClipAnalysisDialog([clip], RadarStore(tmp_path / "radar.sqlite3"))

    def test_le_menu_est_present(self, fenetre):
        options = fenetre.production_options
        proposes = [options.preset_combo.itemData(i)
                    for i in range(options.preset_combo.count())]
        assert proposes == presets.keys()

    def test_il_part_sur_personnalise_comme_a_l_accueil(self, fenetre):
        assert fenetre.production_options.preset() == presets.FREE_KEY

    def test_un_style_regle_les_commandes_de_cette_fenetre(self, fenetre):
        options = fenetre.production_options
        options.set_preset("chaos")
        assert options.subtitle_style() == "secousse"
        assert options.delire_level() == "maximum"
        assert options.editing_overrides()["montage"]["dynamic_zoom"]["max_events"] == 8

    def test_le_style_part_avec_la_production(self, fenetre):
        """LE POINT QUI COMPTE : le style choisi doit arriver dans les
        arguments passes au moteur, pas seulement s'afficher dans la fenetre.
        """
        recu = {}

        class ControleurFactice:
            def start_analysis(self, cli_args, **kwargs):
                recu["cli_args"] = cli_args
                recu["kwargs"] = kwargs

        fenetre.controller = ControleurFactice()
        fenetre.production_options.set_preset("neon")
        fenetre._on_media_ready("/tmp/un-clip.mp4")

        assert recu["cli_args"].subtitle_style == "neon"
        assert recu["cli_args"].input == "/tmp/un-clip.mp4"
        surcharges = recu["kwargs"]["editing_overrides"]
        assert surcharges["montage"]["dynamic_zoom"]["max_events"] == 5
        assert surcharges["delire"] == {"enabled": True, "level": "doux"}

    def test_sans_style_le_reglage_des_parametres_reprend_la_main(self, fenetre,
                                                                  monkeypatch):
        """« Personnalise » ne doit rien imposer : c'est ce qui garantit que
        cette fenetre se comporte comme avant ce menu."""
        recu = {}

        class ControleurFactice:
            def start_analysis(self, cli_args, **kwargs):
                recu["cli_args"] = cli_args

        monkeypatch.setattr("gui.radar.analysis_dialog.settings_store.get",
                            lambda cle, *a: "gaming" if "subtitle" in cle else None)
        fenetre.controller = ControleurFactice()
        fenetre._on_media_ready("/tmp/un-clip.mp4")
        assert recu["cli_args"].subtitle_style == "gaming"
