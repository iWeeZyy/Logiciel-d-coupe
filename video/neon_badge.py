"""Badge neon « REPLAY » + « <STREAMER> EN LIVE », anime, dessine en code.

Demande de l'utilisateur (2026-11-01) : a la place de la pastille au point
rouge, une animation de badge neon REPLAY a gauche, le nom du streamer « EN
LIVE » a droite, sur un fond rouge pastel. Il avait d'abord envoye un apercu
Shutterstock filigrane ; ce badge-ci est un dessin ORIGINAL (rien n'est
repris de l'animation du stock), libre d'utilisation.

Une periode d'animation (FPS images, bouclees sur le clip) :
- le neon s'allume en gresillant au debut de chaque boucle, puis respire ;
- une lumiere fait le tour du cadre (comete) ;
- tube neon : halo large + halo serre + coeur clair, sur une plaque sombre
  (sur le pastel seul, le rouge se fondait dans le fond).
"""
from __future__ import annotations

import math

FPS = 30
PERIOD_S = 2.4
RED = (255, 30, 45)
CORE = (255, 215, 220)
PASTEL = (242, 118, 118)
PLATE = (22, 4, 10, 240)
FLICKER = (0.15, 0.9, 0.2, 1.0, 0.4, 1.0, 0.85, 1.0, 1.0, 1.0, 1.0)   # allumage, 1 valeur / image
FLICKER_S = 0.35
SUPERSAMPLE = 2


def _fonts(scale: float):
    from PIL import ImageFont

    from core.paths import app_base_dir

    path = app_base_dir() / "assets" / "fonts" / "BarlowCondensed-ExtraBoldItalic.ttf"
    size = lambda s: max(8, round(s * scale * SUPERSAMPLE))  # noqa: E731
    try:
        return ImageFont.truetype(str(path), size(34)), ImageFont.truetype(str(path), size(40))
    except OSError:
        from news_story.post_composer import _font

        return _font(size(34)), _font(size(40))


def intensity(t: float, period: float = PERIOD_S) -> float:
    """Eclat du neon a l'instant t (fonction pure) : gresillement d'allumage
    puis respiration douce, sans saut d'une boucle a l'autre."""
    t = t % period
    if t < FLICKER_S:
        return FLICKER[min(len(FLICKER) - 1, int(t * FPS))]
    return 0.85 + 0.15 * math.cos(2 * math.pi * (t - FLICKER_S) / (period - FLICKER_S))


def perimeter_point(x0, y0, x1, y1, u: float):
    """Point a la fraction `u` du tour d'un rectangle (sens horaire depuis le
    coin haut gauche). Fonction pure."""
    w, h = x1 - x0, y1 - y0
    d = (u % 1) * 2 * (w + h)
    if d < w:
        return x0 + d, y0
    d -= w
    if d < h:
        return x1, y0 + d
    d -= h
    if d < w:
        return x1 - d, y1
    return x0, y1 - (d - w)


def _neon(mask, level: float):
    from PIL import Image, ImageFilter

    s = SUPERSAMPLE
    w, h = mask.size
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    for radius, color in ((14 * s, RED), (5 * s, RED), (1.5 * s, (255, 90, 100))):
        alpha = mask.filter(ImageFilter.GaussianBlur(radius)).point(
            lambda v: min(255, int(v * level * 2.2)))
        layer = Image.new("RGBA", (w, h), color + (0,))
        layer.putalpha(alpha)
        out = Image.alpha_composite(out, layer)
    core = Image.new("RGBA", (w, h), CORE + (0,))
    core.putalpha(mask.point(lambda v: int(v * min(1.0, level))))
    return Image.alpha_composite(out, core)


def frames(tag: str, scale: float = 1.0, word: str = "REPLAY", fps: int = FPS,
           period: float = PERIOD_S, *, label: str | None = None, icon: str = "ring"):
    """Une periode d'animation : liste d'images RGBA de meme taille.
    `label` : texte a droite du neon (par defaut « <tag> EN LIVE ») ; vide =
    neon seul. `icon` : « ring » (le rond de REPLAY) ou « play » (triangle,
    bandes-annonces ActuaFilms)."""
    from PIL import Image, ImageChops, ImageDraw, ImageFilter

    s = SUPERSAMPLE
    k = scale * s
    title, name = _fonts(scale)
    probe = ImageDraw.Draw(Image.new("L", (1, 1)))
    word_w = probe.textlength(word, font=title)
    label = (f"{tag} EN LIVE" if label is None else label).upper().strip()
    label_w = probe.textlength(label, font=name) if label else 0
    ring = round(13 * k)
    bx0, by0 = round(26 * k), round(18 * k)
    bx1 = round(bx0 + 16 * k + 2 * ring + 12 * k + word_w + 18 * k)
    by1 = round(by0 + 46 * k)
    right = bx1 + 28 * k + label_w + 28 * k if label else bx1 + 22 * k
    width = int(right) // (2 * s) * (2 * s)
    height = int(by1 + 18 * k) // (2 * s) * (2 * s)
    cx, cy = bx0 + 16 * k + ring, (by0 + by1) / 2

    mask = Image.new("L", (width, height), 0)
    m = ImageDraw.Draw(mask)
    m.rounded_rectangle([bx0, by0, bx1, by1], round(8 * k), outline=255, width=max(1, round(3 * k)))
    if icon == "play":
        m.polygon([(cx - ring * 0.7, cy - ring), (cx - ring * 0.7, cy + ring), (cx + ring, cy)],
                  outline=255, width=max(1, round(3 * k)))
    else:
        m.ellipse([cx - ring, cy - ring, cx + ring, cy + ring], outline=255,
                  width=max(1, round(3 * k)))
    tb = probe.textbbox((0, 0), word, font=title)
    m.text((cx + ring + 12 * k, cy - (tb[1] + tb[3]) / 2), word, font=title, fill=0,
           stroke_width=max(1, round(2 * k)), stroke_fill=255)
    track = mask.filter(ImageFilter.MaxFilter(9))

    background = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    d = ImageDraw.Draw(background)
    d.rounded_rectangle([0, 0, width - 1, height - 1], round(18 * k), fill=PASTEL + (255,))
    d.rounded_rectangle([bx0 - 12 * k, by0 - 10 * k, bx1 + 12 * k, by1 + 10 * k], round(12 * k),
                        fill=PLATE)
    if label:
        nb = probe.textbbox((0, 0), label, font=name)
        d.text((bx1 + 28 * k, cy - (nb[1] + nb[3]) / 2), label, font=name, fill=(255, 255, 255))

    out = []
    for n in range(max(2, round(period * fps))):
        t = n / fps
        image = Image.alpha_composite(background, _neon(mask, intensity(t, period)))
        comet = Image.new("L", (width, height), 0)
        c = ImageDraw.Draw(comet)
        for j in range(14):
            px, py = perimeter_point(bx0, by0, bx1, by1, t / period - j * 0.008)
            r = (6 - j * 0.35) * k
            c.ellipse([px - r, py - r, px + r, py + r], fill=int(255 * (1 - j / 14)))
        image = Image.alpha_composite(image, _neon(ImageChops.multiply(comet, track), 1.3))
        out.append(image.resize((width // s, height // s), Image.LANCZOS))
    return out
