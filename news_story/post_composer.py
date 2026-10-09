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
# Deux mises en page, choisies selon la longueur du titre :
# - news courte : titre dans le bas de l'image, 78 px max (rendu valide par
#   l'utilisateur : « parfait pour les petites news ») ; il peut descendre
#   jusqu'a _TITLE_COMFORT_SIZE, jamais plus petit ;
# - titre trop long pour ca : il prend toute l'image (« le texte doit prendre
#   toute l'image s'il a besoin de beaucoup de lignes ») au lieu de
#   rapetisser dans la bande du bas -- l'ecueil des premieres Stories gaming.
#   L'image est alors assombrie pour rester lisible.
_TITLE_SIZES = (78, 44)          # zone basse : taille max ; taille min absolue
_TITLE_MAX_LINES = 5             # zone basse
_TITLE_TOP_FRAC = 0.40           # zone basse : le titre reste sous la photo
_TITLE_COMFORT_SIZE = 64
_FULL_TITLE_MAX_SIZE = 110       # pleine image : plus grand si la place le permet
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


def _subtitle_font_path() -> Path:
    from core.paths import app_base_dir

    return app_base_dir() / "assets" / "fonts" / "BarlowCondensed-SemiBold.ttf"


def _subtitle_font(size: int):
    """Police du chapo : meme famille que le titre, droite et moins grasse,
    en minuscules -- il se lit comme une phrase, pas comme une accroche."""
    from PIL import ImageFont

    path = _subtitle_font_path()
    try:
        if path.is_file():
            return ImageFont.truetype(str(path), size)
    except OSError:
        pass
    return _font(size)


# Le chapo (texte sous le titre de l'article) porte souvent l'info que le
# titre tait pour faire cliquer (« cet acteur americain » -> Jeremy Allen
# White) : il est pose sous le titre, plus petit (retour utilisateur).
_SUBTITLE_RATIO = 0.52
_SUBTITLE_MIN = 30
_SUBTITLE_LINE_HEIGHT = 1.18
_GAP_TITLE_SUBTITLE = 18


def _subtitle_size(title_size: int) -> int:
    return max(_SUBTITLE_MIN, int(title_size * _SUBTITLE_RATIO))


def _fit_block(draw, title: str, subtitle: str, scale: float, max_width: int,
               max_height: int, *, min_size: int, max_size: int, max_lines: int):
    """Comme _fit_title, pour le bloc titre + chapo : plus grande taille de
    titre (le chapo suit, a ~52 %) ou tout tient en largeur et en hauteur.
    Renvoie (police, lignes, taille, police_chapo, lignes_chapo, taille_chapo,
    hauteur, tient)."""
    max_px, min_px = int(max_size * scale), int(min_size * scale)

    def measure(size):
        font = _font(size)
        lines = _wrap(draw, title, font, max_width) if title else []
        sub_size = _subtitle_size(size)
        sub_font = _subtitle_font(sub_size)
        sub_lines = _wrap(draw, subtitle, sub_font, max_width) if subtitle else []
        height = len(lines) * int(size * _TITLE_LINE_HEIGHT)
        if sub_lines:
            height += (_GAP_TITLE_SUBTITLE if lines else 0) + len(sub_lines) * int(
                sub_size * _SUBTITLE_LINE_HEIGHT)
        return font, lines, size, sub_font, sub_lines, sub_size, height

    size = max_px
    while size >= min_px:
        block = measure(size)
        if len(block[1]) + len(block[4]) <= max_lines and block[6] <= max_height:
            return (*block, True)
        size -= 2
    font, lines, size, sub_font, sub_lines, sub_size, height = measure(min_px)
    if len(lines) + len(sub_lines) > max_lines and sub_lines:
        keep = max(1, max_lines - len(lines))
        sub_lines = sub_lines[:keep]
        sub_lines[-1] = sub_lines[-1].rstrip(" ,;:") + "…"
    return font, lines, size, sub_font, sub_lines, sub_size, height, False


def draw_block(draw, top: int, center_x: float, block, fill=(255, 255, 255, 255),
               shadow_fill=(0, 0, 0, 255), shadow_draw=None) -> int:
    """Dessine titre puis chapo a partir de `top`, centres sur center_x.
    Renvoie le bas du bloc."""
    font, lines, size, sub_font, sub_lines, sub_size = block[:6]
    shadow_draw = shadow_draw or draw
    y = top
    line_h = int(size * _TITLE_LINE_HEIGHT)
    offset = max(2, size // 28)
    for line in lines:
        x = center_x - draw.textlength(line, font=font) / 2
        shadow_draw.text((x + offset, y + offset), line, font=font, fill=shadow_fill)
        draw.text((x, y), line, font=font, fill=fill)
        y += line_h
    if sub_lines:
        y += _GAP_TITLE_SUBTITLE if lines else 0
        sub_h = int(sub_size * _SUBTITLE_LINE_HEIGHT)
        sub_offset = max(1, sub_size // 24)
        for line in sub_lines:
            x = center_x - draw.textlength(line, font=sub_font) / 2
            shadow_draw.text((x + sub_offset, y + sub_offset), line, font=sub_font, fill=shadow_fill)
            draw.text((x, y), line, font=sub_font, fill=fill)
            y += sub_h
    return y


def clean_subtitle(text: str, max_chars: int = 450) -> str:
    """Chapo a afficher : sans balisage ni lignes de liens (descriptions
    YouTube), sur une ligne, coupe sur une fin de phrase (ou un mot) au-dela
    de `max_chars`. Jamais reformule."""
    from news_story.caption import drop_promo_lines
    from news_story.title_shortener import _strip_html

    text = _strip_html(drop_promo_lines(text or ""))
    text = " ".join(text.split())
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    if end >= max_chars // 3:
        return cut[:end + 1]
    return cut.rsplit(" ", 1)[0].rstrip(" ,;:") + "…"


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
               max_lines: int = _TITLE_MAX_LINES, max_size: int | None = None):
    """Plus grande taille ou le titre tient en largeur ET en hauteur.
    `title_scale` (menu Taille du dialogue) reste un multiplicateur.
    Renvoie (police, lignes, taille, tient) : `tient` est faux quand meme la
    taille minimale ne suffit pas (le texte est alors coupé par « … »)."""
    wrap_text = _wrap
    max_size = int((max_size if max_size is not None else _TITLE_SIZES[0]) * scale)
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
                 vertical: bool = False, subtitle: str = "") -> Path:
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
    subtitle = _french_spacing(" ".join((subtitle or "").split()))
    scale = max(0.6, min(1.6, title_scale))
    max_w = width - margin_l - margin_r
    full_image = False
    if title or subtitle:
        available_h = bottom - label_h - int(height * title_top)
        fitted = _fit_block(draw, title, subtitle, scale, max_w, max(available_h, 0),
                            min_size=_TITLE_COMFORT_SIZE, max_size=_TITLE_SIZES[0],
                            max_lines=_TITLE_MAX_LINES + (4 if subtitle else 0))
        if not fitted[7]:
            full_image = True
            fitted = _fit_block(draw, title, subtitle, scale, max_w,
                                max(bottom - label_h - int(height * full_top), 0),
                                min_size=_TITLE_SIZES[1], max_size=_FULL_TITLE_MAX_SIZE,
                                max_lines=_FULL_MAX_LINES + (6 if subtitle else 0))

    if full_image:
        veil = Image.new("RGB", (width, height), _GRADIENT_COLOR)
        canvas = Image.blend(canvas, veil, _FULL_DIM_ALPHA / 255)
        draw = ImageDraw.Draw(canvas)
    _apply_gradient(canvas, gradient_start)
    if logo is not None:
        canvas.paste(logo, (int(center_x - logo.width / 2), logo_y), logo)

    if title or subtitle:
        top = bottom - fitted[6]
        draw_block(draw, top, center_x, fitted, fill=(255, 255, 255), shadow_fill=(0, 0, 0))
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
