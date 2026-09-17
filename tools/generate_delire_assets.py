#!/usr/bin/env python3
"""Genere les textures des themes du montage delire (assets/delire/*.png).

RUN A LA MAIN, PAS AU BUILD : contrairement aux tuiles dessinees d'autres
projets, ceci n'est pas rejoue par la CI. Les PNG produits sont COMMITES,
exactement comme assets/branding/watermark.png -- une texture aleatoire mais
a graine FIXE change une fois pour toutes, elle n'a pas a etre regeneree a
chaque build.

POURQUOI GENERE ET NON TELECHARGE. Des packs de calques "pluie"/"confettis"
gratuits existent en ligne, mais leur licence autorise typiquement de les
UTILISER dans une video qu'on produit, pas de les REDISTRIBUER a l'interieur
d'un logiciel installable -- exactement la distinction deja appliquee dans ce
projet pour les photos de recettes (credit Pexels obligatoire) et pour le
telechargement YouTube/Twitch (RIGHTS_WARNING). Un fichier genere ici est
possede en entier, sans ambiguite de licence -- et pese quelques Ko au lieu
de plusieurs Mo pour une video.

LE TUILAGE VERTICAL SANS COUTURE est ce qui rend `scroll` (video/delire_theme.py)
credible : chaque particule DESSINEE PRES D'UN BORD est aussi dessinee
DECALEE D'UNE HAUTEUR DE CANEVAS (`_wrapped`), de sorte qu'au moment ou le
defilement fait sortir une particule par le bas, sa copie entre deja par le
haut -- sans quoi le raccord du bouclage serait visible a l'oeil.

Usage :
    .venv/bin/python tools/generate_delire_assets.py
"""
from __future__ import annotations

import random
from pathlib import Path

from PIL import Image, ImageDraw

# Taille de generation : le format vertical, seul format ou l'application
# genere un clip par defaut (voir video/cropper.TARGET_W/TARGET_H). Les autres
# formats sont obtenus par mise a l'echelle au moment du rendu
# (video/delire_theme_filters.py), une texture de particules n'a pas besoin
# d'etre generee pixel-parfait pour chaque format de sortie.
W, H = 1080, 1920

OUT_DIR = Path(__file__).resolve().parent.parent / "assets" / "delire"


def _wrapped(draw_one, canvas_h: int) -> None:
    """Appelle `draw_one(offset)` pour offset in (0, +canvas_h, -canvas_h).

    `draw_one` dessine sa forme decalee verticalement de `offset` ; les trois
    appels couvrent le canevas et ses deux voisins immediats, de sorte
    qu'une forme proche d'un bord apparaisse AUSSI juste de l'autre cote --
    c'est ce qui rend le tuilage vertical sans couture."""
    for offset in (0, canvas_h, -canvas_h):
        draw_one(offset)


def _blank() -> Image.Image:
    return Image.new("RGBA", (W, H), (0, 0, 0, 0))


def make_pluie(seed: int = 1) -> Image.Image:
    """Traits obliques, fins, de longueur et d'opacite variables."""
    img = _blank()
    draw = ImageDraw.Draw(img)
    rnd = random.Random(seed)
    for _ in range(170):
        x = rnd.uniform(0, W)
        y = rnd.uniform(0, H)
        length = rnd.uniform(45, 100)
        alpha = rnd.randint(70, 170)
        width = rnd.choice([1, 1, 2])
        dx = -14  # inclinaison constante : une pluie poussee par le vent

        def _one(offset, x=x, y=y, length=length, alpha=alpha, width=width, dx=dx):
            yy = y + offset
            if yy + length < -60 or yy > H + 60:
                return
            draw.line([(x, yy), (x + dx, yy + length)],
                     fill=(205, 222, 255, alpha), width=width)

        _wrapped(_one, H)
    return img


def make_etoiles(seed: int = 2) -> Image.Image:
    """Petits points, tailles et eclats varies -- le scintillement vient du
    defilement tres lent (video/delire_theme.py), pas d'une animation propre
    a la texture."""
    img = _blank()
    draw = ImageDraw.Draw(img)
    rnd = random.Random(seed)
    for _ in range(120):
        x = rnd.uniform(0, W)
        y = rnd.uniform(0, H)
        r = rnd.uniform(1.0, 3.2)
        alpha = rnd.randint(90, 220)
        # Une etoile sur cinq porte une croix de diffraction : la variete
        # evite que le ciel entier soit fait de points identiques.
        croix = rnd.random() < 0.2

        def _one(offset, x=x, y=y, r=r, alpha=alpha, croix=croix):
            yy = y + offset
            if yy + r < -20 or yy > H + 20:
                return
            couleur = (255, 250, 230, alpha)
            draw.ellipse([x - r, yy - r, x + r, yy + r], fill=couleur)
            if croix:
                bras = r * 3.2
                draw.line([(x - bras, yy), (x + bras, yy)], fill=couleur, width=1)
                draw.line([(x, yy - bras), (x, yy + bras)], fill=couleur, width=1)

        _wrapped(_one, H)
    return img


def make_arcenciel() -> Image.Image:
    """Un arc statique dans le tiers superieur du cadre -- AUCUN tuilage : ce
    calque ne defile jamais (video/delire_theme.ThemeLayer.scroll_vertical=0),
    inutile de le rendre raccordable."""
    img = _blank()
    draw = ImageDraw.Draw(img)
    center_x, center_y = W / 2, H * 0.62
    couleurs = [
        (237, 28, 36), (255, 140, 0), (255, 221, 0),
        (76, 187, 76), (30, 144, 255), (102, 51, 204),
    ]
    rayon_ext = W * 0.62
    epaisseur = rayon_ext * 0.045
    for i, couleur in enumerate(couleurs):
        r = rayon_ext - i * epaisseur
        boite = [center_x - r, center_y - r, center_x + r, center_y + r]
        # Demi-cercle superieur seulement (180 a 360 degres) : un arc-en-ciel
        # se voit depuis le sol, jamais en cercle complet.
        draw.arc(boite, start=180, end=360, fill=(*couleur, 165), width=int(epaisseur))
    return img


def make_confettis(seed: int = 3) -> Image.Image:
    """Petits rectangles colores, orientations variees -- plus dense et plus
    vif que la pluie, pour une ambiance de fete."""
    img = _blank()
    draw = ImageDraw.Draw(img)
    rnd = random.Random(seed)
    palette = [(255, 90, 95), (255, 200, 60), (100, 200, 255),
              (140, 230, 140), (230, 130, 230), (255, 255, 255)]
    for _ in range(150):
        x = rnd.uniform(0, W)
        y = rnd.uniform(0, H)
        taille = rnd.uniform(6, 14)
        couleur = rnd.choice(palette)
        alpha = rnd.randint(160, 230)

        def _one(offset, x=x, y=y, taille=taille, couleur=couleur, alpha=alpha):
            yy = y + offset
            if yy + taille < -20 or yy > H + 20:
                return
            draw.rectangle([x, yy, x + taille, yy + taille * 0.4],
                          fill=(*couleur, alpha))

        _wrapped(_one, H)
    return img


def make_braises(seed: int = 4) -> Image.Image:
    """Points chauds, orange a rouge, plus rares que la pluie -- des braises
    qui montent, pas une pluie de feu."""
    img = _blank()
    draw = ImageDraw.Draw(img)
    rnd = random.Random(seed)
    for _ in range(70):
        x = rnd.uniform(0, W)
        y = rnd.uniform(0, H)
        r = rnd.uniform(1.5, 4.0)
        chaude = rnd.random() < 0.6
        couleur = (255, 140, 40) if chaude else (220, 60, 30)
        alpha = rnd.randint(120, 220)

        def _one(offset, x=x, y=y, r=r, couleur=couleur, alpha=alpha):
            yy = y + offset
            if yy + r < -20 or yy > H + 20:
                return
            draw.ellipse([x - r, yy - r, x + r, yy + r], fill=(*couleur, alpha))
            # Un halo plus large et plus transparent : la braise semble
            # rayonner plutot que d'etre un point dur.
            halo = r * 2.4
            draw.ellipse([x - halo, yy - halo, x + halo, yy + halo],
                        fill=(*couleur, alpha // 4))

        _wrapped(_one, H)
    return img


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    generateurs = {
        "pluie.png": make_pluie,
        "etoiles.png": make_etoiles,
        "arcenciel.png": make_arcenciel,
        "confettis.png": make_confettis,
        "braises.png": make_braises,
    }
    for nom, fabrique in generateurs.items():
        chemin = OUT_DIR / nom
        fabrique().save(chemin)
        print(f"{chemin} ({chemin.stat().st_size} octets)")


if __name__ == "__main__":
    main()
