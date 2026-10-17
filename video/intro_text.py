"""Phrase d'accroche posee sous le medaillon de l'intro « Follow »
(ClipsOfStreams) : « Les clips que tu n'as pas vus en live ».

Dessinee UNE fois en PNG transparent plein cadre (Pillow), puis posee par
build_ffmpeg_args comme un calque ordinaire, en fondu sur la duree de
l'intro (video/intro_overlay.text_overlay_spec). Meme rendu que la phrase de
l'intro popcorn des bandes-annonces (news_story/video_composer.py) : police
des sous-titres des posts, ombre, cartouche sombre translucide -- lisible
sur n'importe quelle image du clip.

Le medaillon occupe le centre de son cadre (bas du dessin a ~84 % de sa
hauteur, mesure sur assets/branding/intro_follow.mp4) : la phrase va juste
dessous, ou juste au-dessus quand l'intro est placee en bas de l'ecran.
"""
from __future__ import annotations

from pathlib import Path

_TEXT_SIZE_RATIO = 0.032      # taille de police / plus petit cote de sortie (34 px en 1080)
_CONTENT_TOP = 0.14           # haut du dessin dans le cadre du medaillon
_CONTENT_BOTTOM = 0.86        # bas du dessin (+ marge)
_PAD = 14


def layout(rect: tuple[int, int, int, int], text_h: int, above: bool = False) -> tuple[float, int]:
    """(centre x, haut du bloc de texte) : sous le medaillon, ou au-dessus
    quand l'intro est en bas de l'ecran (`above`). Fonction pure."""
    x, y, w, h = rect
    if above:
        return x + w / 2, y + int(h * _CONTENT_TOP) - text_h - _PAD
    return x + w / 2, y + int(h * _CONTENT_BOTTOM)


def render_intro_text(intro, text: str, out_w: int, out_h: int, out_path) -> Path:
    from PIL import Image, ImageDraw

    from news_story.post_composer import _french_spacing, _subtitle_font
    from news_story.video_composer import _balanced_lines, _draw_rich, _rich_width
    from video.intro_overlay import overlay_rect

    size = max(18, round(min(out_w, out_h) * _TEXT_SIZE_RATIO))
    layer = Image.new("RGBA", (out_w, out_h), (0, 0, 0, 0))
    text_layer = Image.new("RGBA", (out_w, out_h), (0, 0, 0, 0))
    shadow_layer = Image.new("RGBA", (out_w, out_h), (0, 0, 0, 0))
    draw, shadow_draw = ImageDraw.Draw(text_layer), ImageDraw.Draw(shadow_layer)
    font = _subtitle_font(size)
    rect = overlay_rect(intro, out_w, out_h)
    text = _french_spacing(" ".join((text or "").split()))
    lines = _balanced_lines(draw, text, font, size, max(rect[2], int(size * 11)))
    line_h = int(size * 1.2)
    center_x, top = layout(rect, len(lines) * line_h, above=intro.position.startswith("bas"))
    widths = [_rich_width(draw, line, font, size) for line in lines]
    half = min(max(widths) / 2 + _PAD, center_x, out_w - center_x)   # jamais hors cadre
    ImageDraw.Draw(layer).rounded_rectangle(
        [center_x - half, top - _PAD / 2, center_x + half, top + len(lines) * line_h + _PAD / 2],
        radius=18, fill=(0, 0, 0, 150))
    for line, width in zip(lines, widths):
        _draw_rich(text_layer, draw, shadow_draw, (center_x - width / 2, top), line, font, size,
                   (255, 255, 255, 255), (0, 0, 0, 255))
        top += line_h
    image = Image.alpha_composite(layer, Image.alpha_composite(shadow_layer, text_layer))
    out_path = Path(out_path)
    image.save(out_path)
    return out_path
