"""Composition de la Story 1080x1920 a partir de l'image REELLE d'un article.

Aucune IA generative ici, ni ailleurs dans news_story/ : l'image source est
celle deja telechargee par image_cache.py (og:image/twitter:image/contenu de
la page/flux RSS, voir image_fetcher.py) -- ce module ne fait que la recadrer
intelligemment, la redimensionner et poser du texte/des badges dessus avec
Pillow, exactement comme video/thumbnailer.py compose une miniature de clip.
Aucun modele de vision, aucun appel reseau ici.

Le recadrage 9:16 reutilise LE MEME calcul que le montage video
(video.cropper.compute_crop_rect) et LE MEME detecteur de visage
(video.face_detector.detect_faces_in_image) -- pas un second systeme de
cadrage invente pour les images fixes. Sans visage detecte (illustration,
jaquette de jeu, capture d'ecran), le repli est un crop centre, exactement le
comportement deja etabli pour la video quand aucun visage n'est fiable.

Ne redimensionne jamais en deformant : le format 9:16 vient toujours d'un
recadrage, jamais d'un `resize` qui etirerait l'image. Une image source de
resolution insuffisante EST tout de meme agrandie ici si l'appelant demande la
generation malgre l'avertissement -- ce module compose, il ne juge pas de la
resolution ; c'est le role d'image_cache.CachedImage.is_low_resolution, verifie
en amont par l'appelant (GUI), de decider s'il faut avertir l'utilisateur
avant d'en arriver la.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.logging_setup import get_logger
from news_story.story_templates import TEMPLATE_BREAKING, TEMPLATE_NEWS, get_template
from news_story.title_shortener import build_display_title
from video.cropper import TARGET_H, TARGET_W, CenterHint, compute_crop_rect
from video.face_detector import detect_faces_in_image
from video.text_render import draw_outlined_text, fit_font_for_lines, load_font

logger = get_logger()

CANVAS_W, CANVAS_H = TARGET_W, TARGET_H  # 1080x1920 -- meme constante que le recadrage video

# Bandeau semi-transparent pose derriere tout texte, pour rester lisible quel
# que soit le fond de la photo -- jamais un bandeau opaque, qui masquerait
# l'image que l'utilisateur vient de choisir pour l'illustrer.
_SCRIM_COLOR = (10, 8, 6)
_SCRIM_ALPHA = 150
_SCRIM_PADDING = 28

_TITLE_MAX_WIDTH_FRAC = 0.86
# (taille max, taille min, lignes max) par gabarit -- BREAKING garde un
# style plus imposant que NEWS (taille max plus grande). `lignes max` n'est
# plus qu'un PLANCHER : _draw_title() l'agrandit dynamiquement jusqu'a
# occuper tout l'espace vertical dispo (voir _available_title_lines) --
# demande explicite de l'utilisateur ("prendre le plus de place possible sur
# l'image") apres constat qu'un titre long, meme jamais coupe, finissait
# ecrase a la taille minimale dans une bande etroite pres du bas : le
# plafond fixe forcait fit_font_for_lines() a choisir une PETITE police pour
# tenir en peu de lignes plutot qu'une GRANDE police sur plus de lignes,
# alors que l'image avait la place. Un titre court garde son rendu actuel :
# fit_font_for_lines() part de la taille MAX et ne descend que si necessaire.
_TITLE_SIZES = {
    TEMPLATE_NEWS: (72, 24, 6),
    TEMPLATE_BREAKING: (88, 28, 4),
}
_TITLE_Y_FRAC = {"top": 0.16, "center": 0.50, "bottom": 0.82}
_TITLE_Y_FRAC_AUTO = {TEMPLATE_NEWS: 0.80, TEMPLATE_BREAKING: 0.82}
_TITLE_SCALE_MIN, _TITLE_SCALE_MAX = 0.6, 1.6

# Marges de securite pour l'agrandissement dynamique du bloc de titre : ne
# jamais remonter plus haut que ca (laisse toujours un peu de photo visible
# en haut, et pour BREAKING passe sous le badge, voir _draw_badge) ni
# descendre dans la bande "Source" (_SOURCE_BAND_H, reservee separement
# seulement quand une source sera effectivement dessinee).
_TITLE_TOP_SAFE_FRAC = {TEMPLATE_NEWS: 0.05, TEMPLATE_BREAKING: 0.11}
_TITLE_MAX_LINES_CAP = 20  # garde-fou contre un mur de texte illisible

_SOURCE_BAND_H = 96
_SOURCE_FONT_SIZE = 32
_SOURCE_SCRIM_ALPHA = 170

# Rouge deja utilise par l'appli (gui/theme.py, COLORS["danger"] = "#E1554A")
# -- repris tel quel plutot qu'une couleur inventee pour ce badge.
_BADGE_COLOR = (225, 85, 74)
_BADGE_TEXT_COLOR = (255, 255, 255)
_BADGE_MARGIN = 40
_BADGE_FONT_SIZE = 40
_BADGE_PAD_X = 24
_BADGE_PAD_Y = 14

# Meme raisonnement que video/watermark.py (DEFAULT_SIZE_PERCENT) : la taille
# du logo est un pourcentage du PLUS PETIT cote, jamais de la largeur seule,
# pour qu'il paraisse aussi gros quelle que soit la forme de l'image source.
# Centre sur le canvas (demande explicite) plutot qu'en coin : une marque au
# milieu de l'image lit comme un veritable filigrane, pas comme un accent de
# coin -- d'ou aussi une opacite plus faible qu'un logo de coin, pour rester
# discrete malgre sa position bien plus visible.
_BRANDING_SIZE_FRAC = 0.12
_BRANDING_OPACITY = 0.6


@dataclass(frozen=True)
class StoryOptions:
    """Tout ce qu'un utilisateur peut choisir/editer avant l'export --
    aucun champ ici n'est calcule automatiquement sans pouvoir etre
    ecrase : `title_override`/`source_override` a None retombent sur
    l'auto-raccourcissement / le libelle de la source detectee, une chaine
    (meme vide) est prise telle quelle."""

    template: str = TEMPLATE_NEWS
    title: str = ""
    summary: str = ""
    source_label: str = ""
    branding_enabled: bool = True
    title_override: str | None = None
    source_override: str | None = None
    title_position: str = "auto"  # "auto" | "top" | "center" | "bottom"
    title_scale: float = 1.0
    output_format: str = "PNG"  # "PNG" | "JPEG"


def _smart_crop_hint(image) -> CenterHint | None:
    """Centre de visee pour le recadrage 9:16, choisi pour eviter de couper
    un visage. Avec plusieurs visages, vise leur centre pondere (confiance x
    aire) plutot que le plus grand seul : cadrer sur une seule personne
    risquerait de couper les autres, viser leur ensemble les garde tous dans
    le cadre plus souvent. Sans visage detecte -- illustration, jaquette,
    capture sans personnage -- renvoie None, et compute_crop_rect retombe sur
    un crop centre, exactement comme pour la video."""
    import numpy as np

    frame_bgr = np.asarray(image)[:, :, ::-1]
    faces = detect_faces_in_image(frame_bgr)
    if not faces:
        return None

    weight_sum = sum(f.confidence * max(f.area, 1e-6) for f in faces)
    if weight_sum <= 0:
        return None
    x = sum(f.cx * f.confidence * max(f.area, 1e-6) for f in faces) / weight_sum
    y = sum(f.cy * f.confidence * max(f.area, 1e-6) for f in faces) / weight_sum
    return CenterHint(x_center_frac=x, y_center_frac=y)


def _crop_and_resize(image):
    from PIL import Image

    hint = _smart_crop_hint(image)
    w, h = image.size
    rect = compute_crop_rect(w, h, hint)
    cropped = image.crop((rect.x, rect.y, rect.x + rect.w, rect.y + rect.h))
    return cropped.resize((CANVAS_W, CANVAS_H), Image.LANCZOS)


def _apply_scrim(canvas, top: int, bottom: int, alpha: int = _SCRIM_ALPHA) -> None:
    """Assombrit la bande [top, bottom) par un veritable fondu alpha (pas un
    simple dessin semi-transparent, qui ne se melangerait pas avec la photo
    en-dessous) -- mute `canvas` en place."""
    from PIL import Image, ImageDraw

    top, bottom = max(0, int(top)), min(CANVAS_H, int(bottom))
    height = bottom - top
    if height <= 0:
        return

    overlay = Image.new("RGBA", (CANVAS_W, height), (0, 0, 0, 0))
    ImageDraw.Draw(overlay).rectangle([(0, 0), (CANVAS_W, height)], fill=(*_SCRIM_COLOR, alpha))

    blended = canvas.convert("RGBA")
    blended.alpha_composite(overlay, dest=(0, top))
    canvas.paste(blended.convert("RGB"))


def _available_title_lines(min_size: int, top_safe_px: float, bottom_safe_px: float,
                           base_max_lines: int) -> int:
    """Combien de lignes tiennent, AU PIRE (a la taille minimale), dans
    l'espace vertical reellement disponible entre les deux marges de
    securite -- le plancher configure (`base_max_lines`) reste le minimum
    garanti meme si le calcul donne moins (canvas anormalement petit). Sert
    de garde-fou de NOMBRE de lignes ; la hauteur reelle du bloc est bornee
    separement par `max_total_height` (voir fit_font_for_lines), le seul
    garde-fou qui reste correct quelle que soit la taille de police
    finalement choisie."""
    line_height = max(1, int(min_size * 1.18))
    available_h = max(0, CANVAS_H - top_safe_px - bottom_safe_px)
    return max(base_max_lines, min(_TITLE_MAX_LINES_CAP, available_h // line_height))


def _draw_title(canvas, text: str, y_center_frac: float, max_size: int, min_size: int,
                max_lines: int, top_safe_px: float, bottom_safe_px: float) -> None:
    from PIL import ImageDraw

    draw = ImageDraw.Draw(canvas)
    max_width = int(CANVAS_W * _TITLE_MAX_WIDTH_FRAC)
    # Le plancher `max_lines` est agrandi jusqu'a occuper l'espace vertical
    # REELLEMENT disponible : fit_font_for_lines() part toujours de la plus
    # grande taille et ne descend que si necessaire, donc un plafond de
    # lignes plus genereux le laisse choisir une police plus grande (plus de
    # lignes permises) plutot que de forcer la taille minimale dans les
    # anciennes 6/4 lignes -- exactement "prendre le plus de place possible".
    # `max_total_height` est le garde-fou qui compte vraiment : un nombre de
    # lignes genereux ne borne pas a lui seul l'espace occupe, puisqu'une
    # taille plus grande tient en moins de lignes mais chacune plus haute.
    available_h = max(0, CANVAS_H - top_safe_px - bottom_safe_px)
    dynamic_max_lines = _available_title_lines(min_size, top_safe_px, bottom_safe_px, max_lines)
    font, lines, size = fit_font_for_lines(draw, text, max_width, max_size, min_size,
                                           dynamic_max_lines, max_total_height=available_h)

    line_height = int(size * 1.18)
    block_h = line_height * len(lines)
    y_center = CANVAS_H * y_center_frac

    # Le bloc grandit AUTOUR de y_center : pour un titre long, ca le pousse
    # aussi bien vers le haut que vers le bas. On le ramene dans la zone sure
    # [top_safe_px, CANVAS_H - bottom_safe_px] plutot que de le laisser
    # deborder du cadre ou empieter sur le badge/la bande Source -- la marge
    # du haut est prioritaire (un titre qui deborde en haut se voit plus
    # qu'un qui grignote la fine bande de securite du bas).
    bottom_limit = CANVAS_H - bottom_safe_px
    if y_center + block_h / 2 > bottom_limit:
        y_center = bottom_limit - block_h / 2
    if y_center - block_h / 2 < top_safe_px:
        y_center = top_safe_px + block_h / 2

    # Pres du bas ("bottom"/"auto"), le bandeau s'etend jusqu'au bord plutot
    # que de s'arreter juste sous le texte : _draw_source() redessine de toute
    # facon son propre bandeau jusqu'en bas juste apres -- les arreter au
    # meme endroit evite une bande plus claire visible entre les deux.
    scrim_bottom = CANVAS_H if y_center_frac >= 0.65 else y_center + block_h / 2 + _SCRIM_PADDING
    _apply_scrim(canvas, y_center - block_h / 2 - _SCRIM_PADDING, scrim_bottom)

    draw = ImageDraw.Draw(canvas)  # le canvas vient d'etre assombri, on redessine dessus
    draw_outlined_text(draw, CANVAS_W / 2, y_center, lines, font, size,
                       fill=(255, 255, 255), stroke_fill=(0, 0, 0))


def _draw_source(canvas, label: str) -> None:
    """"Source : <label>" discrete, toujours en bas -- quel que soit
    l'emplacement choisi pour le titre, la source reste a un endroit fixe et
    previsible plutot que de suivre le titre, pour rester repérable d'un clic."""
    from PIL import ImageDraw

    top = CANVAS_H - _SOURCE_BAND_H
    _apply_scrim(canvas, top, CANVAS_H, alpha=_SOURCE_SCRIM_ALPHA)

    draw = ImageDraw.Draw(canvas)
    font = load_font(_SOURCE_FONT_SIZE)
    text = f"Source : {label}"
    y_center = CANVAS_H - _SOURCE_BAND_H / 2
    draw_outlined_text(draw, CANVAS_W / 2, y_center, [text], font, _SOURCE_FONT_SIZE,
                       fill=(225, 225, 225), stroke_fill=(0, 0, 0), line_height_factor=1.0)


def _draw_badge(canvas) -> None:
    from PIL import ImageDraw

    draw = ImageDraw.Draw(canvas)
    font = load_font(_BADGE_FONT_SIZE)
    label = "BREAKING"
    text_w = draw.textlength(label, font=font)
    box_w = int(text_w + 2 * _BADGE_PAD_X)
    box_h = int(_BADGE_FONT_SIZE + 2 * _BADGE_PAD_Y)
    x0, y0 = _BADGE_MARGIN, _BADGE_MARGIN
    draw.rounded_rectangle([x0, y0, x0 + box_w, y0 + box_h], radius=box_h // 2, fill=_BADGE_COLOR)
    draw.text((x0 + _BADGE_PAD_X, y0 + _BADGE_PAD_Y // 2), label, font=font, fill=_BADGE_TEXT_COLOR)


def _paste_branding(canvas) -> None:
    """Incrustation du logo ClipsOfStreams EXISTANT (video/watermark.py) --
    jamais un logo invente pour cette fonctionnalite. Centre sur l'image
    plutot qu'en coin (demande explicite) : c'est ce qui le fait lire comme
    un veritable filigrane. Silencieux si le fichier est absent : une Story
    sans marque reste utilisable, une exception ne devrait jamais faire
    echouer tout l'export pour ca."""
    from PIL import Image

    from video.watermark import default_image_path

    logo_path = default_image_path()
    if not logo_path.is_file():
        logger.info("Logo de marque introuvable -- Story generee sans incrustation.")
        return

    try:
        with Image.open(logo_path) as raw_logo:
            logo = raw_logo.convert("RGBA")
            target_dim = int(_BRANDING_SIZE_FRAC * min(CANVAS_W, CANVAS_H))
            scale = target_dim / max(logo.width, logo.height)
            new_size = (max(1, int(logo.width * scale)), max(1, int(logo.height * scale)))
            logo = logo.resize(new_size, Image.LANCZOS)

            if _BRANDING_OPACITY < 1.0:
                alpha = logo.split()[3].point(lambda a: int(a * _BRANDING_OPACITY))
                logo.putalpha(alpha)

            x, y = (CANVAS_W - logo.width) // 2, (CANVAS_H - logo.height) // 2
            canvas.paste(logo, (x, y), mask=logo)
    except Exception as e:  # noqa: BLE001 -- une marque ratee ne doit pas faire echouer l'export
        logger.warning(f"Incrustation de marque ignoree : {e}")


def _title_y_frac(position: str, template_key: str) -> float:
    if position in _TITLE_Y_FRAC:
        return _TITLE_Y_FRAC[position]
    return _TITLE_Y_FRAC_AUTO.get(template_key, 0.82)


def compose_story(image_path: str | Path, out_path: str | Path,
                  options: StoryOptions = StoryOptions()) -> Path:
    """Compose la Story et l'ecrit a `out_path`. Renvoie `out_path`.

    Propage toute erreur de lecture de l'image source (fichier absent, format
    non decodable) plutot que de l'avaler : contrairement a une miniature de
    clip (accessoire, une liste vide suffit en cas d'echec), une Story est
    exactement ce que l'utilisateur vient de demander -- son echec doit
    remonter jusqu'au dialogue GUI pour etre affiche clairement."""
    from PIL import Image

    image_path, out_path = Path(image_path), Path(out_path)
    template = get_template(options.template)

    with Image.open(image_path) as opened:
        canvas = _crop_and_resize(opened.convert("RGB"))

    source_label = (options.source_override if options.source_override is not None
                    else options.source_label).strip()

    if template.show_title:
        title_text = (options.title_override if options.title_override is not None
                      else build_display_title(options.title, options.summary, template.title_max_chars).text)
        title_text = title_text.strip()
        if title_text:
            max_size, min_size, max_lines = _TITLE_SIZES[template.key]
            scale = max(_TITLE_SCALE_MIN, min(_TITLE_SCALE_MAX, options.title_scale))
            y_frac = _title_y_frac(options.title_position, template.key)
            top_safe_px = _TITLE_TOP_SAFE_FRAC[template.key] * CANVAS_H
            bottom_safe_px = (_SOURCE_BAND_H + _SCRIM_PADDING) if source_label else _SCRIM_PADDING
            _draw_title(canvas, title_text, y_frac, int(max_size * scale), int(min_size * scale),
                       max_lines, top_safe_px, bottom_safe_px)

    if template.show_badge:
        _draw_badge(canvas)

    if source_label:
        _draw_source(canvas, source_label)

    if options.branding_enabled:
        _paste_branding(canvas)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    if options.output_format.upper() == "JPEG":
        canvas.save(out_path, format="JPEG", quality=92)
    else:
        canvas.save(out_path, format="PNG")
    return out_path
