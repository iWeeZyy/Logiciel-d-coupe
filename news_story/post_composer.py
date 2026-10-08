"""Composition du POST 4:5 (1080x1350) -- le format des comptes d'actualite
cinema/series du fil Instagram (demande explicite, captures a l'appui) :

    photo plein cadre, recadree sur les visages
    degrade sombre qui monte du bas
    ─────────  ACTUALITÉ  ─────────      <- etiquette entre deux filets
    TITRE COURT EN MAJUSCULES            <- capitales italiques condensees
    ITALIQUES, CENTRE, BLANC
                 [logo]

Meme principe que story_composer.py : aucune IA generative, l'image est celle
de l'article (deja en cache), on ne fait que la recadrer et poser du texte
dessus avec Pillow. Le titre est le titre de l'article, jamais reformule ; le
detail de la news va dans la legende du post (news_story/caption.py).

Police : Barlow Condensed ExtraBold Italic (assets/fonts, licence OFL --
BarlowCondensed-LICENSE.txt), choisie pour son allure de titraille de presse
condensee et italique. Repli sur la police generale de l'appli si le fichier
manque, jamais une erreur.
"""
from __future__ import annotations

import re
from pathlib import Path

from core.logging_setup import get_logger

logger = get_logger()

POST_W, POST_H = 1080, 1350
VERTICAL_W, VERTICAL_H = 1080, 1920

_MARGIN_X = 44
_TITLE_MAX_WIDTH = POST_W - 2 * _MARGIN_X
# Le titre prend la plus grande taille qui tient dans la zone basse (voir
# _fit_title) : un titre court s'affiche en tres gros, un long descend
# jusqu'a la taille min. Meme regle que les Stories News (« prendre le plus
# de place possible sur l'image ») -- avec un plafond fixe de 78 px, le texte
# restait petit alors que l'image avait la place (retour utilisateur).
_TITLE_SIZES = (150, 44)         # taille max, taille min
_TITLE_MAX_LINES = 8             # garde-fou contre un mur de texte (zone basse)
_TITLE_TOP_FRAC = 0.40           # zone basse : le titre reste sous la photo
# Un titre qui a besoin de beaucoup de lignes ne rapetisse pas en dessous de
# cette taille dans la zone basse : il monte plutot jusqu'en haut de l'image
# (retour utilisateur : « le texte doit prendre toute l'image s'il a besoin
# de beaucoup de lignes »). L'image est alors assombrie pour rester lisible.
_TITLE_COMFORT_SIZE = 76
_FULL_TITLE_TOP_FRAC = 0.05
_FULL_MAX_LINES = 16
_FULL_DIM_ALPHA = 120            # voile sombre sur toute l'image dans ce cas
_TITLE_LINE_HEIGHT = 1.08
_LABEL_SIZE = 46
_LABEL_GAP = 26                  # espace entre l'etiquette et chaque filet
_RULE_THICKNESS = 2
_LOGO_MAX_H = 104
_LOGO_MAX_W = 300
_BOTTOM_MARGIN = 54
_GAP_TITLE_LOGO = 34
_GAP_LABEL_TITLE = 26
_GRADIENT_START_FRAC = 0.40      # le degrade commence a 40 % de la hauteur
_GRADIENT_MAX_ALPHA = 240
_GRADIENT_COLOR = (10, 10, 14)

# Format 9:16 (TikTok, Reels, Story) : l'interface de l'application recouvre
# le bas de l'image (pseudo, description, musique : ~420 px sur TikTok et
# Reels) et la colonne de boutons a droite (~130 px). Le bloc de texte est
# donc remonte au-dessus de cette zone, avec une marge droite plus large que
# la gauche (zones sures de TikTok) plutot que deux grandes marges qui
# rapetissaient le titre.
_VERTICAL_MARGIN_LEFT = 64
_VERTICAL_MARGIN_RIGHT = 140
_VERTICAL_BOTTOM_MARGIN = 440
_VERTICAL_GRADIENT_START_FRAC = 0.26
_VERTICAL_TITLE_TOP_FRAC = 0.30
_VERTICAL_FULL_TITLE_TOP_FRAC = 0.10   # sous les onglets du haut de TikTok

DEFAULT_LABEL = "ACTUALITÉ"

# Typographie francaise : l'espace avant ? ! : ; » est insecable -- sans ca,
# le retour a la ligne laissait un « ? » seul sur la derniere ligne.
_NBSP = "\u00a0"
_SPACE_BEFORE_HIGH_PUNCT = re.compile(r" +([?!:;»])")
_SPACE_AFTER_OPEN_QUOTE = re.compile(r"(«) +")


def _wrap(draw, text: str, font, max_width: int) -> list[str]:
    """Retour a la ligne sur les espaces ORDINAIRES seulement (str.split()
    sans argument couperait aussi sur l'espace insecable)."""
    lines, current = [], ""
    for word in text.split(" "):
        if not word:
            continue
        candidate = f"{current} {word}" if current else word
        if current and draw.textlength(candidate, font=font) > max_width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def _french_spacing(text: str) -> str:
    text = _SPACE_BEFORE_HIGH_PUNCT.sub(_NBSP + r"\1", text)
    return _SPACE_AFTER_OPEN_QUOTE.sub(r"\1" + _NBSP, text)


def _title_font_path() -> Path:
    from core.paths import app_base_dir

    return app_base_dir() / "assets" / "fonts" / "BarlowCondensed-ExtraBoldItalic.ttf"


def _font(size: int):
    from PIL import ImageFont

    from video.text_render import load_font

    path = _title_font_path()
    try:
        if path.is_file():
            return ImageFont.truetype(str(path), size)
    except OSError:
        pass
    return load_font(size)


def _crop_to_aspect(image, aspect: float):
    """Recadre `image` au ratio `aspect` (largeur/hauteur) en visant les
    visages (meme detecteur que la Story, story_composer._smart_crop_hint) ;
    sans visage, crop centre. Jamais de deformation."""
    from news_story.story_composer import _smart_crop_hint

    w, h = image.size
    hint = _smart_crop_hint(image)
    if w / h > aspect:
        crop_w, crop_h = round(h * aspect), h
        cx = hint.x_center_frac * w if hint else w / 2
        x = int(max(0, min(cx - crop_w / 2, w - crop_w)))
        return image.crop((x, 0, x + crop_w, crop_h))
    crop_w, crop_h = w, round(w / aspect)
    # Un visage en haut de cadre (portrait) : on garde un peu d'air au-dessus
    # de la tete plutot que de la centrer pile, le bas de l'image etant de
    # toute facon couvert par le degrade et le titre.
    cy = hint.y_center_frac * h + crop_h * 0.12 if hint else h / 2
    y = int(max(0, min(cy - crop_h / 2, h - crop_h)))
    return image.crop((0, y, crop_w, y + crop_h))


def _apply_gradient(canvas, start_frac: float = _GRADIENT_START_FRAC) -> None:
    from PIL import Image

    width, full_h = canvas.size
    start = int(full_h * start_frac)
    height = full_h - start
    mask = Image.linear_gradient("L").resize((1, height))  # 0 en haut -> 255 en bas
    # Courbe adoucie : le degrade reste discret haut et fonce vite pres du
    # texte, comme sur les posts de reference.
    mask = mask.point(lambda v: int(_GRADIENT_MAX_ALPHA * (v / 255) ** 1.35))
    mask = mask.resize((width, height))
    shade = Image.new("RGB", (width, height), _GRADIENT_COLOR)
    canvas.paste(shade, (0, start), mask)


def _fit_title(draw, text: str, scale: float, max_width: int = _TITLE_MAX_WIDTH,
               max_height: int | None = None, min_size: int | None = None,
               max_lines: int = _TITLE_MAX_LINES):
    """Plus grande taille ou le titre tient en largeur ET en hauteur.
    `title_scale` (menu Taille du dialogue) reste un multiplicateur.
    Renvoie (police, lignes, taille, tient) : `tient` est faux quand meme la
    taille minimale ne suffit pas (le texte est alors coupé par « … »)."""
    wrap_text = _wrap
    max_size = int(_TITLE_SIZES[0] * scale)
    min_size = int((min_size if min_size is not None else _TITLE_SIZES[1]) * scale)
    size = max_size
    while size >= min_size:
        font = _font(size)
        lines = wrap_text(draw, text, font, max_width)
        fits_height = max_height is None or len(lines) * int(size * _TITLE_LINE_HEIGHT) <= max_height
        if len(lines) <= max_lines and fits_height and all(
                draw.textlength(line, font=font) <= max_width for line in lines):
            return font, lines, size, True
        size -= 2
    font = _font(min_size)
    lines = wrap_text(draw, text, font, max_width)
    if len(lines) > max_lines:
        kept = lines[:max_lines]
        kept[-1] = kept[-1].rstrip(" ,;:") + "…"
        lines = kept
    return font, lines, min_size, False


def _load_logo(path: Path | None):
    from PIL import Image

    if path is None or not path.is_file():
        return None
    try:
        with Image.open(path) as raw:
            logo = raw.convert("RGBA")
    except Exception as e:  # noqa: BLE001 -- un logo illisible ne fait pas echouer le post
        logger.warning(f"Logo illisible, post genere sans : {e}")
        return None
    scale = min(_LOGO_MAX_H / logo.height, _LOGO_MAX_W / logo.width)
    return logo.resize((max(1, int(logo.width * scale)), max(1, int(logo.height * scale))), Image.LANCZOS)


def compose_post(image_path: str | Path, out_path: str | Path, *, title: str,
                 label: str = DEFAULT_LABEL, logo_path: Path | None = None,
                 title_scale: float = 1.0, output_format: str = "PNG",
                 vertical: bool = False) -> Path:
    """Compose le post et l'ecrit a `out_path` : 4:5 pour le fil Instagram,
    ou 9:16 (`vertical`) pour TikTok, Reels et Story -- meme mise en page,
    bloc de texte remonte hors de l'interface de ces applications. Propage
    une erreur de lecture de l'image source, comme compose_story."""
    from PIL import Image, ImageDraw

    out_path = Path(out_path)
    if vertical:
        width, height = VERTICAL_W, VERTICAL_H
        margin_l, margin_r, bottom_margin, gradient_start, title_top, full_top = (
            _VERTICAL_MARGIN_LEFT, _VERTICAL_MARGIN_RIGHT, _VERTICAL_BOTTOM_MARGIN,
            _VERTICAL_GRADIENT_START_FRAC, _VERTICAL_TITLE_TOP_FRAC, _VERTICAL_FULL_TITLE_TOP_FRAC)
    else:
        width, height = POST_W, POST_H
        margin_l, margin_r, bottom_margin, gradient_start, title_top, full_top = (
            _MARGIN_X, _MARGIN_X, _BOTTOM_MARGIN, _GRADIENT_START_FRAC, _TITLE_TOP_FRAC,
            _FULL_TITLE_TOP_FRAC)
    center_x = (margin_l + width - margin_r) / 2
    with Image.open(image_path) as opened:
        canvas = _crop_to_aspect(opened.convert("RGB"), width / height).resize(
            (width, height), Image.LANCZOS)

    draw = ImageDraw.Draw(canvas)

    # Mise en page du bas vers le haut : logo, titre, etiquette. Le titre est
    # mesure AVANT de poser le degrade : c'est sa hauteur qui decide s'il
    # reste dans la zone basse ou s'il prend toute l'image.
    bottom = height - bottom_margin
    logo = _load_logo(logo_path)
    logo_y = bottom - logo.height if logo is not None else bottom
    if logo is not None:
        bottom -= logo.height + _GAP_TITLE_LOGO

    label = " ".join(label.split()).upper()
    label_font = _font(_LABEL_SIZE + 10 if vertical else _LABEL_SIZE)
    label_h = (sum(label_font.getmetrics()) + _GAP_LABEL_TITLE) if label else 0

    title = _french_spacing(" ".join(title.split()).upper())
    scale = max(0.6, min(1.6, title_scale))
    max_w = width - margin_l - margin_r
    full_image = False
    if title:
        available_h = bottom - label_h - int(height * title_top)
        fitted = _fit_title(draw, title, scale, max_w, max(available_h, 0),
                            min_size=_TITLE_COMFORT_SIZE)
        if not fitted[3]:
            full_image = True
            fitted = _fit_title(draw, title, scale, max_w,
                                max(bottom - label_h - int(height * full_top), 0),
                                max_lines=_FULL_MAX_LINES)

    if full_image:
        veil = Image.new("RGB", (width, height), _GRADIENT_COLOR)
        canvas = Image.blend(canvas, veil, _FULL_DIM_ALPHA / 255)
        draw = ImageDraw.Draw(canvas)
    _apply_gradient(canvas, gradient_start)
    if logo is not None:
        canvas.paste(logo, (int(center_x - logo.width / 2), logo_y), logo)

    if title:
        font, lines, size, _ = fitted
        line_h = int(size * _TITLE_LINE_HEIGHT)
        top = bottom - line_h * len(lines)
        for i, line in enumerate(lines):
            line_w = draw.textlength(line, font=font)
            x, y = center_x - line_w / 2, top + i * line_h
            shadow = max(2, size // 28)
            draw.text((x + shadow, y + shadow), line, font=font, fill=(0, 0, 0))  # ombre portee discrete
            draw.text((x, y), line, font=font, fill=(255, 255, 255))
        bottom = top - _GAP_LABEL_TITLE

    if label:
        font = label_font
        text_w = draw.textlength(label, font=font)
        ascent, descent = font.getmetrics()
        y_text = bottom - ascent - descent
        y_rule = y_text + (ascent + descent) // 2 + 2
        x_text = center_x - text_w / 2
        draw.text((x_text, y_text), label, font=font, fill=(255, 255, 255))
        left_end = x_text - _LABEL_GAP
        right_start = x_text + text_w + _LABEL_GAP
        if left_end > margin_l:
            draw.rectangle([margin_l, y_rule, left_end, y_rule + _RULE_THICKNESS - 1], fill=(235, 235, 235))
        if right_start < width - margin_r:
            draw.rectangle([right_start, y_rule, width - margin_r, y_rule + _RULE_THICKNESS - 1],
                           fill=(235, 235, 235))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    if output_format.upper() == "JPEG":
        canvas.save(out_path, format="JPEG", quality=93)
    else:
        canvas.save(out_path, format="PNG")
    return out_path
