"""Catalogue des themes du montage delire : quel fichier, comment le poser.

MODULE PUR : aucune resolution de chemin dependante du disque n'est faite ici
au-dela de `asset_path()`, qui suit exactement le meme mecanisme que
`video/watermark.py` (`app_base_dir()`, pas un chemin relatif -- une fois
compile, le dossier courant n'est pas celui de l'executable).

Chaque theme particulaire (tous sauf "mystere", voir editing/delire.py) est
une VIDEO fond vert REELLE, livree dans assets/delire/<nom>.mp4 -- pas une
texture generee (l'ancienne version de ce module posait des PNG en boucle
avec `scroll` ; ce mecanisme a ete entierement remplace, voir la section
correction de cadrage de video/delire_theme_filters.py pour le detourage par
chromakey). Le nom du fichier EST la cle du theme (editing.delire.THEMES) --
pas de mapping indirect, les deux doivent rester synchronises.

LE PROBLEME QUE `fit`/`anchor` RESOLVENT : toutes les videos fournies sont en
FORMAT PAYSAGE (16:9 ou proche), le clip cible est VERTICAL (9:16). Deux
strategies, choisies A LA MAIN par theme apres avoir REGARDE chaque video
(aucune ne convient a toutes) :

- "cover" : mise a l'echelle sur la HAUTEUR puis rognage horizontal centre --
  remplit tout le cadre, au prix des bords lateraux. Convient a un sujet
  centre (chimpanzee) ou une texture qui doit couvrir tout l'ecran sans trou
  (pluie, intelligence, manga) : perdre quelques bords y est anodin.
- "contain" : mise a l'echelle sur la LARGEUR, jamais de rognage, complete
  par un bandeau transparent -- rien n'est jamais perdu. Necessaire des qu'un
  element FIXE proche d'un bord importe (le bandeau "LIVE" d'infos, colle au
  bord gauche de sa video source : un rognage l'aurait coupe -- constate en
  testant "cover" dessus avant de choisir "contain").

`anchor` ne vaut que pour "contain" : ou va le bandeau transparent restant.
"center" convient a un contenu disperse sur toute la hauteur (confettis,
euros, fleurs, infos). "bottom" est reserve a "flammes" : un feu doit toucher
le bas du cadre, un centrage le ferait flotter au milieu de l'ecran sans
toucher aucun bord -- constate en testant "center" dessus avant de corriger.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from editing.delire import (
    THEME_CHIMPANZEE,
    THEME_CONFETTIS,
    THEME_EUROS,
    THEME_FLAMMES,
    THEME_FLEURS,
    THEME_INFOS,
    THEME_INTELLIGENCE,
    THEME_MANGA,
    THEME_PLUIE,
)

ASSETS_SUBDIR = "delire"

FIT_COVER = "cover"
FIT_CONTAIN = "contain"

ANCHOR_CENTER = "center"
ANCHOR_TOP = "top"
ANCHOR_BOTTOM = "bottom"


@dataclass(frozen=True)
class ThemeLayer:
    """Un calque video a superposer : un fichier, un ajustement de cadre, une
    opacite, et SES PROPRES parametres de chromakey.

    `anchor` est ignore quand `fit` vaut "cover" (rien n'est jamais en trop a
    positionner, tout le cadre est rempli par construction).

    `chroma_color`/`chroma_similarity`/`chroma_blend` : voir le docstring de
    video/delire_theme_filters.py pour pourquoi ce sont des valeurs PAR CLIP
    et pas un reglage partage -- une couleur moyenne, ou un `blend` non nul,
    produisait une transparence parasite sur les tons sombres du sujet
    (constate sur "chimpanzee")."""

    filename: str
    fit: str = FIT_COVER
    anchor: str = ANCHOR_CENTER
    opacity: float = 1.0
    chroma_color: str = "0x00FF00"
    chroma_similarity: float = 0.14
    chroma_blend: float = 0.02


# Le calque video de chaque theme, avec son ajustement de cadre choisi a
# l'oeil (voir les raisons dans le docstring du module) -- toutes les
# opacites restent hautes (0.85-1.0) : ce sont des incrustations REELLES
# (un chimpanze, des flammes...), pas des textures decoratives discretes
# comme l'etaient les anciens PNG -- les rendre trop transparentes leur
# ferait perdre leur lisibilite.
#
# chroma_color mesure PAR CLIP (echantillonnage PIL sur une frame extraite en
# PNG sans perte, moyenne/mode sur l'image entiere -- pas un JPEG compresse,
# ni seulement les bords, voir video/delire_theme_filters.py). chroma_blend
# reste proche de 0 partout : un blend genereux (0.05-0.08, la valeur
# partagee d'origine) rend translucide tout pixel dont la teinte est
# SEULEMENT PROCHE du vert cle, pas seulement les bords du sujet -- invisible
# sur un fond de test uni, flagrant sur un fond bariole (constate en
# isolant le bug sur "chimpanzee").
_LAYERS = {
    THEME_CHIMPANZEE: (
        ThemeLayer("chimpanzee.mp4", fit=FIT_COVER, opacity=1.0,
                   chroma_color="0x0ECA42", chroma_similarity=0.14, chroma_blend=0.02),
    ),
    THEME_CONFETTIS: (
        ThemeLayer("confettis.mp4", fit=FIT_CONTAIN, anchor=ANCHOR_CENTER, opacity=0.90,
                   chroma_color="0x00FF01", chroma_similarity=0.14, chroma_blend=0.02),
    ),
    THEME_EUROS: (
        ThemeLayer("euros.mp4", fit=FIT_CONTAIN, anchor=ANCHOR_CENTER, opacity=0.90,
                   chroma_color="0x0FFA05", chroma_similarity=0.14, chroma_blend=0.02),
    ),
    THEME_FLAMMES: (
        ThemeLayer("flammes.mp4", fit=FIT_CONTAIN, anchor=ANCHOR_BOTTOM, opacity=0.90,
                   chroma_color="0x12850F", chroma_similarity=0.17, chroma_blend=0.02),
    ),
    THEME_FLEURS: (
        ThemeLayer("fleurs.mp4", fit=FIT_CONTAIN, anchor=ANCHOR_CENTER, opacity=0.90,
                   chroma_color="0x00CA00", chroma_similarity=0.14, chroma_blend=0.02),
    ),
    THEME_PLUIE: (
        # Fond le plus texturise des neuf rushes (gouttes translucides sur
        # toute la surface, voir la mesure dans le docstring du module) --
        # similarity plus genereuse pour capter les variations du fond, mais
        # blend reste bas pour ne pas rendre les gouttes elles-memes
        # translucides jusqu'a disparaitre.
        ThemeLayer("pluie.mp4", fit=FIT_COVER, opacity=0.85,
                   chroma_color="0x00FD16", chroma_similarity=0.26, chroma_blend=0.04),
    ),
    THEME_INFOS: (
        ThemeLayer("infos.mp4", fit=FIT_CONTAIN, anchor=ANCHOR_CENTER, opacity=1.0,
                   chroma_color="0x1DCC0A", chroma_similarity=0.14, chroma_blend=0.02),
    ),
    THEME_INTELLIGENCE: (
        ThemeLayer("intelligence.mp4", fit=FIT_COVER, opacity=0.85,
                   chroma_color="0x0ECA42", chroma_similarity=0.18, chroma_blend=0.02),
    ),
    THEME_MANGA: (
        ThemeLayer("manga.mp4", fit=FIT_COVER, opacity=0.90,
                   chroma_color="0x12E10C", chroma_similarity=0.14, chroma_blend=0.02),
    ),
    # THEME_MYSTERE est absent DELIBEREMENT : voir video/delire_theme_filters.py,
    # qui le traite par deux filtres ffmpeg natifs (vignette + desaturation),
    # sans aucun fichier.
}


def layers_for(theme: str) -> tuple[ThemeLayer, ...]:
    """Les calques d'un theme, dans l'ordre de superposition (le premier est
    le plus bas). Tuple vide pour un theme inconnu ou sans calque ("mystere",
    ou "")."""
    return _LAYERS.get(theme, ())


def asset_path(filename: str) -> Path:
    """Chemin d'une video de theme livree avec l'application.

    Meme mecanisme que `video/watermark.asset_path()` : passe par
    `app_base_dir()`, jamais par un chemin relatif au depot -- une fois
    compile, le dossier courant n'est pas celui de l'executable."""
    from core.paths import app_base_dir

    return app_base_dir() / "assets" / ASSETS_SUBDIR / filename
