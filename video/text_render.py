"""Rendu de texte Pillow partage entre les miniatures (thumbnailer.py) et les
Stories d'actualites (news_story/story_composer.py) : chargement de police
avec repli, retour a la ligne par mot, texte avec contour lisible sur fond
clair ou sombre.

Extrait ici pour eviter une troisieme copie quasi identique : thumbnailer.py
en avait deja une, story_composer.py en avait besoin d'une comportementalement
identique (meme police, meme reduction progressive de taille jusqu'a tenir en
N lignes, meme contour proportionnel a la taille).
"""
from __future__ import annotations

from pathlib import Path

from core.paths import app_base_dir

FONT_CANDIDATES = (
    app_base_dir() / "assets" / "fonts" / "JetBrainsMono-Bold.ttf",
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    Path("C:/Windows/Fonts/arialbd.ttf"),
)


def load_font(size: int):
    from PIL import ImageFont

    for path in FONT_CANDIDATES:
        try:
            if path.exists():
                return ImageFont.truetype(str(path), size)
        except OSError:
            continue
    return ImageFont.load_default()


def wrap_text(draw, text: str, font, max_width: int) -> list[str]:
    words, lines, current = text.split(), [], ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and draw.textlength(candidate, font=font) > max_width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def fit_font_for_lines(draw, text: str, max_width: int, max_size: int, min_size: int,
                       max_lines: int, step: int = 6):
    """Reduit la taille de police jusqu'a ce que le texte tienne dans
    `max_lines` lignes de largeur `max_width`. Renvoie (font, lines, size) --
    size est renvoye separement plutot que lu sur `font.size`, car
    ImageFont.load_default() (repli sans aucune police TrueType disponible)
    n'expose pas toujours cet attribut selon la version de Pillow. Si meme la
    taille minimale ne suffit pas, les lignes en trop sont coupees plutot que
    de deborder du cadre."""
    size = max_size
    while size >= min_size:
        font = load_font(size)
        lines = wrap_text(draw, text, font, max_width)
        if len(lines) <= max_lines and all(draw.textlength(line, font=font) <= max_width for line in lines):
            return font, lines, size
        size -= step
    font = load_font(min_size)
    return font, wrap_text(draw, text, font, max_width)[:max_lines], min_size


def draw_outlined_text(draw, x_center: float, y_center: float, lines: list[str], font, size: int,
                       fill: tuple, stroke_fill: tuple, line_height_factor: float = 1.18) -> None:
    """Dessine des lignes centrees horizontalement sur `x_center`, le bloc
    entier centre verticalement sur `y_center`, avec un contour dont
    l'epaisseur suit la taille de police -- un contour fixe disparaitrait a
    grande taille et bavurait a petite taille."""
    stroke_width = max(3, size // 12)
    line_height = int(size * line_height_factor)
    total_height = line_height * len(lines)
    y = int(y_center - total_height / 2)
    for line in lines:
        width = draw.textlength(line, font=font)
        draw.text((x_center - width / 2, y), line, font=font, fill=fill,
                  stroke_width=stroke_width, stroke_fill=stroke_fill)
        y += line_height
