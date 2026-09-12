"""Les styles de sous-titres ajoutes : animations, marqueur, machine a ecrire.

CE QUE CES TESTS DEFENDENT, dans l'ordre d'importance :
  * que les huit styles d'origine rendent EXACTEMENT comme avant -- c'est la
    seule chose qu'un utilisateur remarquerait immediatement ;
  * que chaque style du catalogue soit rendable, et rendu avec l'effet qu'il
    annonce ;
  * le piege verifie au rendu : le surlignage au marqueur ne passe PAS par
    BorderStyle 3 (libass peint alors la LIGNE ENTIERE, avec la couleur de
    CONTOUR et non la couleur de fond) mais par un contour epais pose sur le
    seul mot mis en evidence.
"""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from core.models import Word
from editing.captions import build_captions
from video import subtitle_renderer
from video.subtitle_renderer import ANIMATIONS, render_ass_file

REPO = Path(__file__).resolve().parents[1]

# Les styles livres AVANT cette serie d'ajouts. Leur rendu ne doit pas bouger.
STYLES_HISTORIQUES = ("bold", "dynamic", "minimal", "podcast", "gaming",
                      "progressive", "classic", "big_text")
STYLES_AJOUTES = ("karaoke", "marqueur", "neon", "ressort", "secousse", "machine")


def _catalogue() -> dict:
    return json.loads((REPO / "config" / "subtitles.json").read_text(encoding="utf-8"))


def _mots(textes, pas=0.4):
    return [Word(text=t, start=i * pas, end=(i + 1) * pas)
            for i, t in enumerate(textes)]


def _groupes_avec_dernier_mot_accentue(mots):
    groupes = build_captions(mots, clip_start=0.0, max_words_per_group=len(mots))
    groupe = groupes[0]
    liste = list(groupe.words)
    liste[-1] = dataclasses.replace(liste[-1], emphasized=True)
    return [dataclasses.replace(groupe, words=liste)]


def _rendre(style, tmp_path, textes=("tout", "va", "changer")):
    mots = _mots(list(textes))
    chemin = tmp_path / "clip.ass"
    render_ass_file(mots, 0.0, style, str(chemin),
                    caption_groups=_groupes_avec_dernier_mot_accentue(mots))
    return chemin.read_text(encoding="utf-8")


def _dialogues(ass: str) -> list[str]:
    return [l for l in ass.splitlines() if l.startswith("Dialogue:")]


class TestLeCatalogue:
    def test_les_styles_historiques_sont_toujours_la(self):
        """Retirer un style casse silencieusement un reglage enregistre."""
        connus = _catalogue()["styles"]
        for nom in STYLES_HISTORIQUES:
            assert nom in connus, nom

    def test_le_style_par_defaut_n_a_pas_change(self):
        assert _catalogue()["default_style"] == "dynamic"

    def test_les_nouveaux_styles_sont_livres(self):
        connus = _catalogue()["styles"]
        for nom in STYLES_AJOUTES:
            assert nom in connus, nom

    def test_chaque_style_est_decrit_et_a_un_mode_connu(self):
        for nom, style in _catalogue()["styles"].items():
            assert style.get("description", "").strip(), nom
            assert style.get("mode") in ("smart", "progressive", "classic",
                                         "typewriter"), nom

    def test_chaque_style_annonce_une_animation_connue(self):
        """Une animation mal orthographiee ne leverait pas : le mot serait
        simplement rendu sans effet, donc sans que rien ne le signale."""
        for nom, style in _catalogue()["styles"].items():
            assert style.get("animation", "none") in ANIMATIONS, nom

    def test_chaque_style_du_catalogue_se_rend(self, tmp_path):
        for nom, style in _catalogue()["styles"].items():
            ass = _rendre(style, tmp_path)
            assert _dialogues(ass), nom
            assert "PlayResX: 1080" in ass, nom


class TestLesAnimations:
    """Chaque animation doit poser une balise DIFFERENTE, sinon deux entrees du
    menu produiraient la meme video."""

    BASE = {"mode": "smart", "words_per_group": 3, "font_size": 90,
            "emphasis_color": "&H004CA2E0", "emphasis_scale": 1.2}

    def _balises(self, animation):
        return subtitle_renderer._emphasis_tags(dict(self.BASE, animation=animation))[0]

    def test_aucune_animation_ne_pose_aucune_transformation(self):
        assert "\\t(" not in self._balises("none")

    def test_le_rebond_depasse_puis_revient(self):
        """« pop » grossit et s'arrete : l'oeil n'y lit pas un choc. Un rebond
        est un DEPASSEMENT suivi d'un retour, donc au moins deux etapes."""
        balises = self._balises("bounce")
        assert balises.count("\\t(") >= 2
        assert "\\fscx118" in balises

    def test_la_secousse_tourne_dans_les_deux_sens(self):
        balises = self._balises("shake")
        assert "\\frz2" in balises and "\\frz-2" in balises

    def test_la_lueur_floute_au_lieu_d_epaissir(self):
        """Un contour epais reste net et durcit le texte ; c'est le flou qui
        donne la lueur."""
        balises = self._balises("glow")
        assert "\\blur" in balises
        assert "\\bord" not in balises

    def test_la_lueur_porte_aussi_sur_la_ligne_a_demi_force(self):
        ligne = subtitle_renderer._line_prefix(dict(self.BASE, animation="glow",
                                                    glow_blur=4))
        assert "\\blur2" in ligne

    def test_chaque_animation_a_sa_signature(self):
        """Deux entrees du menu qui produisent la meme video seraient un choix
        pour rien. La signature est le couple (balises du mot, balises de la
        ligne) : « fade » ne touche que la ligne, « bounce » que le mot."""
        signatures = {}
        for animation in ANIMATIONS:
            style = dict(self.BASE, animation=animation)
            signatures[animation] = (subtitle_renderer._emphasis_tags(style)[0],
                                     subtitle_renderer._line_prefix(style))
        assert len(set(signatures.values())) == len(ANIMATIONS), signatures

    def test_l_animation_porte_sur_le_mot_pas_sur_la_ligne(self, tmp_path):
        """Faire trembler tout un bloc rend la lecture penible."""
        style = dict(self.BASE, animation="shake", uppercase=True)
        ligne = _dialogues(_rendre(style, tmp_path))[0]
        assert ligne.count("\\frz2") == 1
        assert ligne.split("{\\frz")[0].endswith("VA ") or "TOUT VA" in ligne


class TestLeSurlignageAuMarqueur:
    """LE PIEGE, verifie au rendu reel avec ffmpeg/libass.

    Avec BorderStyle 3, libass peint le rectangle avec la couleur de CONTOUR et
    le fait couvrir la LIGNE ENTIERE. Un marqueur sur un seul mot ne peut donc
    pas passer par la ; il passe par un contour tres epais pose sur ce mot.
    """

    def _style(self, **extra):
        style = dict(_catalogue()["styles"]["marqueur"])
        style.update(extra)
        return style

    def test_le_style_livre_n_utilise_pas_le_rectangle_opaque(self):
        style = self._style()
        assert style.get("border_style", 1) != 3
        assert style.get("emphasis_marker") is True

    def test_le_marqueur_est_un_contour_epais_sur_le_mot(self, tmp_path):
        ligne = _dialogues(_rendre(self._style(), tmp_path))[0]
        assert "\\bord14" in ligne
        assert "\\3c&H0000D7FF" in ligne

    def test_le_marqueur_ne_touche_qu_un_seul_mot(self, tmp_path):
        ligne = _dialogues(_rendre(self._style(), tmp_path))[0]
        assert ligne.count("\\bord") == 1
        assert "{\\r}" in ligne, "le reste de la ligne doit revenir au style"

    def test_le_texte_du_mot_surligne_passe_en_sombre(self):
        """Du blanc sur du jaune ne se lit pas."""
        assert self._style()["emphasis_color"] == "&H00000000"

    def test_le_marqueur_ne_grossit_pas_le_mot(self):
        """Grossir le mot ferait deborder la pastille de la ligne."""
        assert self._style()["emphasis_scale"] == 1.0

    def test_l_ombre_est_coupee_sous_la_pastille(self, tmp_path):
        """Une ombre portee sous une pastille pleine dessine un halo sale."""
        assert "\\shad0" in _dialogues(_rendre(self._style(), tmp_path))[0]

    def test_sans_l_option_aucun_contour_n_est_pose(self):
        balises = subtitle_renderer._emphasis_tags(
            {"font_size": 90, "emphasis_marker": False})[0]
        assert "\\bord" not in balises

    def test_la_largeur_du_marqueur_est_reglable(self):
        balises = subtitle_renderer._emphasis_tags(
            {"font_size": 90, "emphasis_marker": True, "marker_width": 22})[0]
        assert "\\bord22" in balises


class TestLeKaraoke:
    """LE SENS DU REMPLISSAGE, qui se lit a l'envers si on se trompe.

    Le tag ASS \\k fait passer le texte de la couleur SECONDAIRE a la couleur
    PRIMAIRE. L'en-tete met `primary_color` en primaire et `highlight_color` en
    secondaire : donc `highlight_color` habille le texte PAS ENCORE DIT.
    """

    def _style(self):
        return dict(_catalogue()["styles"]["karaoke"])

    def test_chaque_mot_porte_sa_propre_duree(self, tmp_path):
        ass = _rendre(self._style(), tmp_path)
        ligne = _dialogues(ass)[0]
        assert ligne.count("\\k") == 3, "un \\k par mot, sinon le remplissage saute"

    def test_le_mot_deja_dit_prend_la_couleur_d_accent(self):
        """Si les deux couleurs etaient inversees, le texte partirait dore et
        deviendrait blanc : le remplissage se lirait a l'envers."""
        style = self._style()
        assert style["primary_color"] != "&H00FFFFFF", "le deja-dit doit etre l'accent"
        assert style["highlight_color"] == "&H00FFFFFF", "le pas-encore-dit reste neutre"

    def test_la_phrase_entiere_reste_visible(self, tmp_path):
        """C'est ce qui distingue le karaoke des sous-titres progressifs."""
        ligne = _dialogues(_rendre(self._style(), tmp_path))[0]
        for mot in ("TOUT", "VA", "CHANGER"):
            assert mot in ligne

    def test_classic_garde_son_reglage_historique(self):
        """Il est livre dans l'autre sens depuis toujours : le corriger
        changerait le rendu d'un style deja utilise."""
        classic = _catalogue()["styles"]["classic"]
        assert classic["primary_color"] == "&H00FFFFFF"


class TestLaMachineAEcrire:
    def _style(self, **extra):
        style = dict(_catalogue()["styles"]["machine"])
        style.update(extra)
        return style

    def test_le_texte_s_allonge_a_chaque_etape(self, tmp_path):
        ass = _rendre(self._style(), tmp_path, textes=("tout", "va", "changer"))
        visibles = [l.split(",,0,0,0,,")[1].rstrip("_") for l in _dialogues(ass)]
        assert len(visibles) > 1
        for avant, apres in zip(visibles, visibles[1:]):
            assert apres.startswith(avant), (avant, apres)

    def test_la_derniere_etape_montre_tout_le_texte(self, tmp_path):
        ass = _rendre(self._style(), tmp_path, textes=("tout", "va", "changer"))
        assert _dialogues(ass)[-1].endswith("TOUT VA CHANGER")

    def test_le_curseur_disparait_a_la_fin(self, tmp_path):
        ass = _rendre(self._style(caret="_"), tmp_path)
        lignes = _dialogues(ass)
        assert lignes[0].endswith("_")
        assert not lignes[-1].endswith("_")

    def test_les_etapes_ne_se_chevauchent_pas(self, tmp_path):
        """Deux evenements ASS simultanes afficheraient deux textes empiles."""
        lignes = _dialogues(_rendre(self._style(), tmp_path))
        bornes = [(l.split(",")[1], l.split(",")[2]) for l in lignes]
        for (_, fin), (debut, _suivant) in zip(bornes, bornes[1:]):
            assert debut >= fin

    def test_le_rythme_suit_la_parole(self, tmp_path):
        """Une cadence fixe prendrait de l'avance sur la voix, et le decalage
        se voit immediatement."""
        rapide = _dialogues(_rendre(self._style(), tmp_path,
                                    textes=("tout", "va", "changer")))
        lent = _dialogues(_rendre(self._style(), tmp_path))
        # meme texte, mais les mots ci-dessous sont prononces deux fois plus
        # lentement -> la frappe doit durer plus longtemps
        mots = [Word(text=t, start=i * 0.8, end=(i + 1) * 0.8)
                for i, t in enumerate(["tout", "va", "changer"])]
        chemin = tmp_path / "lent.ass"
        render_ass_file(mots, 0.0, self._style(), str(chemin),
                        caption_groups=_groupes_avec_dernier_mot_accentue(mots))
        lent = _dialogues(chemin.read_text(encoding="utf-8"))
        assert lent[0].split(",")[2] > rapide[0].split(",")[2]

    def test_le_nombre_d_etapes_est_borne(self, tmp_path):
        """Un evenement par caractere sur un texte long produirait des
        centaines de lignes pour un effet que l'oeil ne distingue plus."""
        longs = ["anticonstitutionnellement"] * 3
        ass = _rendre(self._style(typewriter_steps=6), tmp_path, textes=longs)
        assert len(_dialogues(ass)) <= 6

    def test_un_texte_vide_ne_produit_aucune_ligne(self, tmp_path):
        chemin = tmp_path / "vide.ass"
        render_ass_file([], 0.0, self._style(), str(chemin))
        assert not _dialogues(chemin.read_text(encoding="utf-8"))


class TestLaBordureConfigurable:
    @staticmethod
    def _champ(entete: str, nom: str) -> str:
        """Lit un champ du style par son NOM, pas par sa position.

        L'ordre des colonnes est declare juste au-dessus dans le fichier : le
        lire evite qu'un test compte les virgules a la main et se trompe."""
        lignes = entete.splitlines()
        format_line = [l for l in lignes if l.startswith("Format: Name")][0]
        colonnes = [c.strip() for c in format_line[len("Format:"):].split(",")]
        style = [l for l in lignes if l.startswith("Style:")][0]
        valeurs = [v.strip() for v in style[len("Style:"):].split(",")]
        return valeurs[colonnes.index(nom)]

    def test_le_defaut_reste_le_contour_simple(self):
        """La valeur historique etait ecrite en dur : la rendre reglable ne
        doit rien changer a un style qui ne la mentionne pas."""
        assert self._champ(subtitle_renderer._header({}), "BorderStyle") == "1"

    def test_le_rectangle_opaque_reste_atteignable(self):
        """Il ne sert pas au marqueur, mais il reste un look valable pour une
        ligne entiere."""
        entete = subtitle_renderer._header({"border_style": 3})
        assert self._champ(entete, "BorderStyle") == "3"

    def test_aucun_style_livre_ne_compte_sur_back_color_pour_un_fond(self):
        """back_color est la couleur de l'OMBRE, pas celle du rectangle : un
        style qui s'y fierait n'afficherait pas le fond attendu."""
        for nom, style in _catalogue()["styles"].items():
            if style.get("border_style") == 3:
                pytest.fail(f"{nom} : voir l'en-tete de _header")
