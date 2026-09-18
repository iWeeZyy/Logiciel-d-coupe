"""Video d'intro posee EN INCRUSTATION sur le debut du clip.

Remplace une premiere version (video/intro_concat.py, supprime) qui
concatenait l'intro comme une SCENE SEPAREE, jouee en entier avant le clip :
la demande etait de la superposer, en petit dans un coin, PENDANT que le clip
joue -- le clip reste visible en dessous et autour, seulement les toutes
premieres secondes.

Le fond de la video source est un vrai noir plein cadre, pas une
transparence : `colorkey` le retire au rendu (le noir devient transparent),
ce qui est ce qui permet une incrustation "en medaillon" plutot qu'un carre
noir plaque sur le clip. Le contenu utile (l'anneau, le "+ Follow", le halo,
les etincelles) bouge et deborde du centre au fil de l'animation -- CROP_*
est un cadre fixe, mesure une fois sur l'asset livre (assets/branding/
intro_follow.mp4, 1080x1920) en analysant les pixels non-noirs sur plusieurs
images de l'animation, assez large pour contenir le mouvement entier sans
jamais le rogner. A retuner si l'asset change de composition.

Module PUR comme watermark.py et filter_graph.py : construit des morceaux de
filtre ffmpeg, ne lit ni ne decode aucune image. Les dimensions/duree reelles
de l'asset sont lues par l'appelant (clip_builder.py, via ffmpeg_utils) et
passees toutes faites dans IntroOverlay -- pas d'appel ffmpeg ici.
"""
from __future__ import annotations

from dataclasses import dataclass

# Cadre de rognage, mesure sur l'asset de reference (1080x1920) : mis a
# l'echelle proportionnellement si l'asset livre change un jour de definition.
_REF_W, _REF_H = 1080, 1920
_CROP_X, _CROP_Y, _CROP_W, _CROP_H = 40, 330, 1000, 1140

# Poser le noir en transparent. La similarite (0.15) mange le halo neon qui
# s'attenue vers le noir sans laisser de liseré sombre ; le blend (0.10)
# adoucit la coupure -- verifie au rendu sur fond uni ET sur mire colorée,
# aucun des deux ne laisse de frange visible.
_COLORKEY = "colorkey=0x000000:0.15:0.10"

DEFAULT_SIZE_PERCENT = 35.0
DEFAULT_MARGIN_PERCENT = 4.0
# En haut a droite par defaut : le filigrane reste en bas au centre (voir
# watermark.py), donc rien ne se superpose entre les deux tant que l'un des
# deux ne change pas de coin explicitement.
DEFAULT_POSITION = "haut-droite"

_POSITIONS = ("haut-gauche", "haut-centre", "haut-droite",
              "bas-gauche", "bas-centre", "bas-droite")


@dataclass(frozen=True)
class IntroOverlay:
    """Une video d'intro prete a etre posee en incrustation, avec ses
    dimensions et sa duree reelles deja lues (voir docstring du module)."""

    path: str
    src_w: int
    src_h: int
    duration: float
    size_percent: float = DEFAULT_SIZE_PERCENT
    margin_percent: float = DEFAULT_MARGIN_PERCENT
    position: str = DEFAULT_POSITION


def crop_rect(src_w: int, src_h: int) -> tuple[int, int, int, int]:
    """Le cadre a rogner dans l'asset intro, adapte a sa vraie definition."""
    x = round(_CROP_X * src_w / _REF_W)
    y = round(_CROP_Y * src_h / _REF_H)
    w = round(_CROP_W * src_w / _REF_W)
    h = round(_CROP_H * src_h / _REF_H)
    return x, y, w, h


def prepare_filter(intro: IntroOverlay, out_w: int, out_h: int) -> str:
    """Filtre applique a la video d'intro avant de la poser : recadrage sur
    le contenu utile, mise a l'echelle (pourcentage du plus petit cote de
    sortie, meme raisonnement que le filigrane), puis le noir devient
    transparent."""
    x, y, w, h = crop_rect(intro.src_w, intro.src_h)
    width = max(2, int(round(min(out_w, out_h) * intro.size_percent / 100.0)))
    width -= width % 2
    return f"crop={w}:{h}:{x}:{y},scale={width}:-1,format=yuva420p,{_COLORKEY}"


def overlay_position(intro: IntroOverlay, out_w: int, out_h: int) -> str:
    """Coordonnees de l'incrustation. Meme calcul que watermark.overlay_position
    (marge sur le plus petit cote de sortie) -- duplique plutot que reutilise :
    c'est huit lignes, et emprunter le type Watermark pour un objet qui n'en
    est pas un aurait ete plus trompeur que la duplication."""
    reference = min(out_w, out_h)
    margin = max(0, int(round(reference * intro.margin_percent / 100.0)))
    horizontal = {"gauche": f"{margin}", "centre": "(W-w)/2", "droite": f"W-w-{margin}"}
    vertical = {"haut": f"{margin}", "bas": f"H-h-{margin}"}
    position = intro.position if intro.position in _POSITIONS else DEFAULT_POSITION
    band, side = position.split("-")
    return f"{horizontal[side]}:{vertical[band]}"


def overlay_spec(intro: IntroOverlay, out_w: int, out_h: int) -> tuple[str, str, str]:
    """(filtre de preparation, position, condition d'activation) -- pret a
    entrer dans le compositeur a N calques de build_ffmpeg_args.

    La condition limite l'incrustation aux `intro.duration` premieres
    secondes de SORTIE : passe cette duree, la video d'intro n'a plus
    d'image reelle a montrer (overlay figerait sa derniere image sinon), et
    le clip doit redevenir entierement visible.
    """
    filt = prepare_filter(intro, out_w, out_h)
    position = overlay_position(intro, out_w, out_h)
    enable = f"between(t,0,{max(0.0, intro.duration):.3f})"
    return filt, position, enable
