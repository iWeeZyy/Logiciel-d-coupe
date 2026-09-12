"""Choisir la chaine qui signe la video : ClipsOfStreams ou LandsCapesFR.

DEUX LOGOS, UN SEUL MECANISME. Le filigrane existait deja, avec sa position,
sa taille en pourcentage et son opacite. On ajoute un CATALOGUE lu dans
config/editing.json, pas un second systeme : ajouter une troisieme chaine
demande une entree dans ce fichier et un PNG dans assets/branding/, sans
toucher au code.

CE QUE CES TESTS DEFENDENT, dans l'ordre d'importance :
  * qu'une faute de frappe dans le choix ne publie JAMAIS le logo d'une autre
    chaine -- c'est la faute la plus couteuse ici, elle signe une video du
    mauvais nom sans rien dire ;
  * qu'un logo absent du disque ne soit pas propose ;
  * que le comportement d'avant, un seul logo, soit intact.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from video import watermark

REPO = Path(__file__).resolve().parents[1]


def _config() -> dict:
    data = json.loads((REPO / "config" / "editing.json").read_text(encoding="utf-8"))
    return data["watermark"]


class TestLesDeuxLogosSontLivres:
    def test_les_deux_fichiers_existent(self):
        for entry in _config()["choices"]:
            assert (REPO / "assets" / entry["image"]).is_file(), entry["image"]

    def test_ils_sont_ronds_et_detoures(self):
        """Un coin opaque signifierait un fond carre : le logo se poserait en
        vignette rectangulaire sur la video."""
        Image = pytest.importorskip("PIL.Image", reason="Pillow requis")

        for entry in _config()["choices"]:
            image = Image.open(REPO / "assets" / entry["image"]).convert("RGBA")
            largeur, hauteur = image.size
            assert image.getpixel((0, 0))[3] == 0, entry["image"]
            assert image.getpixel((largeur - 1, hauteur - 1))[3] == 0, entry["image"]
            assert image.getpixel((largeur // 2, hauteur // 2))[3] == 255, entry["image"]

    def test_ils_ont_le_meme_diametre(self):
        """`size_percent` est un pourcentage de la largeur de sortie : deux
        logos de diametres differents ne pesaient pas pareil a l'oeil."""
        Image = pytest.importorskip("PIL.Image", reason="Pillow requis")

        tailles = {Image.open(REPO / "assets" / entry["image"]).size
                   for entry in _config()["choices"]}
        assert len(tailles) == 1, tailles

    def test_ils_sont_carres(self):
        """Un disque dans un cadre non carre serait un ovale."""
        Image = pytest.importorskip("PIL.Image", reason="Pillow requis")

        for entry in _config()["choices"]:
            largeur, hauteur = Image.open(REPO / "assets" / entry["image"]).size
            assert largeur == hauteur, entry["image"]


class TestCatalogue:
    def test_les_deux_chaines_sont_proposees(self):
        cles = [entry["key"] for entry in watermark.choices(_config())]
        assert cles == ["clipsofstreams", "landscapesfr"]

    def test_chaque_entree_porte_un_libelle_lisible(self):
        for entry in watermark.choices(_config()):
            assert entry["label"] and entry["label"] != entry["key"]

    def test_une_entree_dont_le_png_manque_est_ecartee(self):
        """Proposer un logo introuvable donnerait une video sans filigrane,
        sans rien dire a personne."""
        bancal = {"choices": [{"key": "fantome", "label": "Fantôme",
                               "image": "branding/absent.png"}]}
        assert watermark.choices(bancal) == []

    def test_une_entree_incomplete_est_ecartee(self):
        bancal = {"choices": [{"label": "Sans clé ni image"}, "pas un objet"]}
        assert watermark.choices(bancal) == []

    def test_sans_catalogue_on_retombe_sur_le_logo_historique(self):
        """L'ancien comportement, un seul logo, doit rester joignable."""
        replis = watermark.choices({})
        assert len(replis) == 1
        assert replis[0]["image"] == watermark.DEFAULT_IMAGE


class TestResolutionDuChoix:
    def test_chaque_cle_donne_son_propre_fichier(self):
        config = _config()
        clips = watermark.image_for_choice("clipsofstreams", config)
        lands = watermark.image_for_choice("landscapesfr", config)
        assert clips.endswith("watermark.png")
        assert lands.endswith("watermark-landscapesfr-badge.png")
        assert clips != lands

    def test_une_cle_inconnue_ne_donne_PAS_le_premier_logo(self):
        """LE TEST QUI COMPTE LE PLUS. Retomber silencieusement sur le premier
        de la liste signerait la video du mauvais nom. On rend "" et c'est
        l'appelant qui decide."""
        assert watermark.image_for_choice("chaine-inexistante", _config()) == ""

    def test_une_cle_vide_ne_donne_rien(self):
        assert watermark.image_for_choice("", _config()) == ""
        assert watermark.image_for_choice("   ", _config()) == ""


class TestFromConfig:
    def test_le_choix_est_honore(self):
        mark = watermark.from_config({**_config(), "choice": "landscapesfr"})
        assert mark is not None
        assert mark.image.endswith("watermark-landscapesfr-badge.png")

    def test_sans_choix_le_logo_historique_est_pose(self):
        """La regression a eviter : un utilisateur qui n'a rien choisi doit
        retrouver exactement ce qu'il avait avant."""
        config = dict(_config())
        config["choice"] = ""
        mark = watermark.from_config(config)
        assert mark is not None
        assert mark.image.endswith("watermark.png")

    def test_une_image_designee_a_la_main_reste_prioritaire(self, tmp_path):
        """C'est ce qui permet un logo hors catalogue, sans l'y inscrire."""
        ailleurs = tmp_path / "mon-logo.png"
        ailleurs.write_bytes(b"0")
        mark = watermark.from_config({**_config(), "image": str(ailleurs),
                                      "choice": "landscapesfr"})
        assert mark is not None and mark.image == str(ailleurs)

    def test_un_choix_errone_retombe_sur_le_defaut_et_non_sur_rien(self):
        """Mieux vaut le logo historique qu'aucun filigrane : l'utilisateur
        avait demande un filigrane."""
        mark = watermark.from_config({**_config(), "choice": "faute-de-frappe"})
        assert mark is not None
        assert mark.image.endswith("watermark.png")

    def test_le_filigrane_coupe_reste_coupe_quel_que_soit_le_choix(self):
        assert watermark.from_config({**_config(), "enabled": False,
                                      "choice": "landscapesfr"}) is None

    def test_la_taille_et_l_opacite_ne_dependent_pas_du_choix(self):
        """Seule l'image change : c'est ce qui rend les deux comparables."""
        config = _config()
        premier = watermark.from_config({**config, "choice": "clipsofstreams"})
        second = watermark.from_config({**config, "choice": "landscapesfr"})
        assert premier.size_percent == second.size_percent
        assert premier.opacity == second.opacity
        assert premier.position == second.position


class TestInterfaceDeLAccueil:
    @pytest.fixture
    def box(self):
        pytest.importorskip("PySide6")
        import os

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        from gui.widgets.production_options import ProductionOptionsBox

        QApplication.instance() or QApplication([])
        return ProductionOptionsBox(columns=3)

    def test_les_deux_chaines_sont_dans_le_menu(self, box):
        libelles = [box.watermark_combo.itemText(i)
                    for i in range(box.watermark_combo.count())]
        assert libelles == ["ClipsOfStreams", "LandsCapesFR"]

    def test_le_premier_du_catalogue_est_choisi_par_defaut(self, box):
        assert box.watermark_choice() == "clipsofstreams"

    def test_le_choix_voyage_avec_l_etat_de_la_case(self, box):
        box.set_watermark_choice("landscapesfr")
        assert box.editing_overrides()["watermark"] == {
            "enabled": True, "choice": "landscapesfr"}

    def test_le_menu_est_grise_quand_le_filigrane_est_coupe(self, box):
        """Un menu actif laisserait croire qu'un logo sera pose."""
        box.set_module_enabled("watermark", False)
        assert not box.watermark_combo.isEnabled()

    def test_il_redevient_actif_quand_on_recoche(self, box):
        box.set_module_enabled("watermark", False)
        box.set_module_enabled("watermark", True)
        assert box.watermark_combo.isEnabled()

    def test_un_choix_inconnu_ne_change_rien(self, box):
        avant = box.watermark_choice()
        box.set_watermark_choice("chaine-inexistante")
        assert box.watermark_choice() == avant

    # Les modules qui portent PLUS que leur interrupteur. Le filigrane doit
    # dire quelle chaine signe la video ; le delire, a quel cran il joue. Tous
    # les autres restent un simple booleen, et cette liste existe pour que
    # l'ajout d'un troisieme soit un choix visible et non un glissement.
    RICHES = {"watermark", "delire"}

    def test_seuls_les_modules_connus_portent_plus_qu_un_booleen(self, box):
        overrides = box.editing_overrides()
        for cle, valeur in overrides.items():
            if cle in self.RICHES:
                assert isinstance(valeur, dict), cle
            else:
                assert isinstance(valeur, bool), cle


class TestFusionParLeControleur:
    """Le controleur acceptait un booleen par module. Il doit accepter les deux
    formes, sinon les autres cases cesseraient de fonctionner."""

    def _fusion(self, depart: dict, overrides: dict) -> dict:
        editing = {k: dict(v) if isinstance(v, dict) else v
                   for k, v in depart.items()}
        for module, override in overrides.items():
            if not isinstance(editing.get(module), dict):
                continue
            if isinstance(override, dict):
                editing[module].update(override)
            else:
                editing[module]["enabled"] = bool(override)
        return editing

    def test_un_booleen_ne_touche_que_l_interrupteur(self):
        depart = {"captions": {"enabled": True, "style": "punchy"}}
        fusion = self._fusion(depart, {"captions": False})
        assert fusion["captions"] == {"enabled": False, "style": "punchy"}

    def test_un_dictionnaire_fusionne_ses_cles(self):
        depart = {"watermark": {"enabled": True, "size_percent": 14,
                                "choice": ""}}
        fusion = self._fusion(depart, {"watermark": {"enabled": True,
                                                     "choice": "landscapesfr"}})
        assert fusion["watermark"]["choice"] == "landscapesfr"
        assert fusion["watermark"]["size_percent"] == 14, \
            "les réglages non mentionnés doivent survivre"

    def test_un_module_inconnu_est_ignore(self):
        assert self._fusion({}, {"inexistant": True}) == {}

    def test_le_depart_n_est_pas_modifie(self):
        """Sinon un run contaminerait le suivant."""
        depart = {"watermark": {"enabled": True, "choice": ""}}
        self._fusion(depart, {"watermark": {"choice": "landscapesfr"}})
        assert depart["watermark"]["choice"] == ""
