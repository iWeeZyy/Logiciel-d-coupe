"""Catalogue des themes du montage delire : quel fichier, quel mouvement.

MODULE PUR : aucune resolution de chemin dependante du disque n'est faite ici
au-dela de `asset_path()`, qui suit exactement le meme mecanisme que
`video/watermark.py` (`app_base_dir()`, pas un chemin relatif -- une fois
compile, le dossier courant n'est pas celui de l'executable).

Chaque theme particulaire (tous sauf "mystere", voir editing/delire.py) est
une texture PNG generee UNE FOIS par tools/generate_delire_assets.py et
livree dans assets/delire/, jamais dessinee a la volee : la variete visuelle
vient de la texture elle-meme (positions aleatoires mais fixees), pas d'un
calcul a chaque rendu.

LE MECANISME, en une phrase : l'image est chargee en boucle (`-loop 1`) pour
en faire un flux video continu, puis le filtre `scroll` de ffmpeg la fait
defiler a vitesse constante -- c'est la meme texture qui glisse sous une
fenetre fixe, pas une animation image par image. Une image fixe (sans
`-loop 1`) ne produirait qu'UNE SEULE image que `overlay` repeterait
telle quelle pour tout le clip (c'est exactement le comportement du
filigrane, qui n'a besoin de rien d'autre) ; `scroll` a besoin de plusieurs
images DIFFERENTES a faire glisser, d'ou la boucle.

LA VITESSE EST UNE FRACTION DE LA HAUTEUR PAR IMAGE (parametre natif du
filtre `scroll`), donc plus une source a d'images par seconde, plus le calque
semblerait defiler vite a duree egale. `scroll_vertical_for_fps()` compense en
ramenant la vitesse a une reference de 30 im/s : la pluie tombe au meme rythme
qu'une source soit filmee a 24, 30 ou 60 im/s.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from editing.delire import THEME_BRAISES, THEME_CONFETTIS, THEME_ETOILES, THEME_PLUIE

ASSETS_SUBDIR = "delire"

# Reference de cadence pour laquelle les vitesses ci-dessous ont ete reglees a
# l'oeil (voir scroll_vertical_for_fps).
_REFERENCE_FPS = 30.0


@dataclass(frozen=True)
class ThemeLayer:
    """Un calque a superposer : un fichier, un defilement, une opacite.

    `scroll_vertical` est SIGNE : positif = defile vers le bas (pluie, neige,
    confettis -- tout ce qui tombe), negatif = vers le haut (braises -- ce qui
    monte). Une valeur nulle signifie « pas de defilement du tout », c'est le
    cas de l'arc-en-ciel : le ciel scintille, l'arc-en-ciel ne bouge pas.
    """

    filename: str
    scroll_vertical: float
    opacity: float


# Le calque animee de chaque theme. Un theme peut en porter PLUSIEURS -- voir
# "etoiles", qui pose un ciel scintillant ET un arc-en-ciel statique, deux
# fichiers distincts composes l'un sur l'autre.
#
# Vitesses choisies a l'oeil (voir tools/generate_delire_assets.py --apercu
# pour rejouer un rendu de verification) : la pluie et les confettis tombent
# vite (0.010-0.016), les braises montent lentement (-0.004 : la fumee monte
# doucement, une brindille qui vole vite ferait davantage penser a des
# etincelles de feu d'artifice), le ciel etoile derive a peine (0.0015 : un
# defilement note comme du scintillement, pas comme une chute).
_LAYERS = {
    THEME_PLUIE: (ThemeLayer("pluie.png", scroll_vertical=0.012, opacity=0.55),),
    THEME_ETOILES: (
        ThemeLayer("etoiles.png", scroll_vertical=0.0015, opacity=0.65),
        ThemeLayer("arcenciel.png", scroll_vertical=0.0, opacity=0.55),
    ),
    THEME_CONFETTIS: (ThemeLayer("confettis.png", scroll_vertical=0.016, opacity=0.75),),
    THEME_BRAISES: (ThemeLayer("braises.png", scroll_vertical=-0.004, opacity=0.60),),
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
    """Chemin d'une texture de theme livree avec l'application.

    Meme mecanisme que `video/watermark.asset_path()` : passe par
    `app_base_dir()`, jamais par un chemin relatif au depot -- une fois
    compile, le dossier courant n'est pas celui de l'executable."""
    from core.paths import app_base_dir

    return app_base_dir() / "assets" / ASSETS_SUBDIR / filename


def scroll_vertical_for_fps(base_vertical: float, fps: float) -> float:
    """Vitesse de defilement compensee de la cadence source.

    `scroll` deplace le calque d'une FRACTION FIXE de sa hauteur A CHAQUE
    IMAGE, jamais par seconde -- documente par `ffmpeg -h filter=scroll`
    (aucune duree en parametre, seulement `vertical`/`horizontal`). Une source
    a 60 im/s produit donc deux fois plus d'increments par seconde qu'une
    source a 30 im/s, et la pluie semblerait tomber deux fois plus vite pour
    la meme valeur -- ce que cette fonction corrige en ramenant tout a la
    cadence de reference sur laquelle les vitesses de `_LAYERS` ont ete
    reglees.
    """
    fps = float(fps) if fps and fps > 0 else _REFERENCE_FPS
    return base_vertical * (_REFERENCE_FPS / fps)
