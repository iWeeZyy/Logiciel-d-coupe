"""Carrousels cinema 9:16 : sorties de la semaine, box-office, « Devine le film ».

Demande explicite de l'utilisateur pour son compte cinema (TikTok mode photo,
carrousel Instagram). Meme principe que le Top news cine du jour
(daily_top.py) : un dossier = une couverture + une image par film + la
legende prete a copier. La couverture est celle du Top (daily_top.compose_cover,
textes changes) ; chaque film a sa « fiche » : l'affiche (ou une photo du film)
sur fond flou, l'etiquette, le titre, les infos d'AlloCine, le logo et la
phrase d'appel.

Rien d'invente : chaque ligne d'une fiche vient de la page AlloCine
(cinema_lists.py) ; un champ absent (pas encore de note) n'est pas affiche.
Les textes sont centres sur l'ecran, avec la marge de la colonne de boutons
TikTok reprise des deux cotes (comme les posts 9:16).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from core.logging_setup import get_logger

logger = get_logger()

W, H = 1080, 1920
_MARGIN = 140                    # colonne de boutons TikTok, reprise a gauche
_SAFE_TOP, _SAFE_BOTTOM = int(H * 0.10), H - 440
_TEXT_W = W - 2 * _MARGIN
_ACCENT = (224, 162, 76)
_WHITE, _BLACK = (255, 255, 255), (0, 0, 0)
_GAP = 26
_LABEL_SIZE = 44
_TITLE_SIZES = (80, 52)
_INFO_SIZE = 38
_BODY_SIZE = 33
_BODY_MAX_LINES = 3
_PICTURE_MIN_H = 600           # taille de l'image en dessous de laquelle le synopsis saute


@dataclass
class Slide:
    """Une fiche du carrousel : image (affiche ou photo), textes."""

    image_path: Path | None
    label: str
    title: str
    lines: list[str]
    body: str = ""
    slug: str = ""


# ------------------------------------------------------------------ dessin

def _blurred_background(image_path: Path | None):
    from PIL import Image, ImageEnhance, ImageFilter

    canvas = Image.new("RGB", (W, H), (14, 14, 18))
    if image_path is None or not Path(image_path).is_file():
        return canvas
    try:
        with Image.open(image_path) as raw:
            img = raw.convert("RGB")
    except OSError as e:
        logger.warning(f"Image illisible pour le fond : {e}")
        return canvas
    scale = max(W / img.width, H / img.height)
    img = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))),
                     Image.LANCZOS)
    left, top = (img.width - W) // 2, (img.height - H) // 2
    img = img.crop((left, top, left + W, top + H)).filter(ImageFilter.GaussianBlur(24))
    return ImageEnhance.Brightness(img).enhance(0.38)


def pixelate(image, cells: int):
    """Mosaique : l'image ramenee a `cells` cases de large puis agrandie sans
    lissage. Moins de cases = plus dur a reconnaitre."""
    from PIL import Image

    small_w = max(4, cells)
    small_h = max(3, round(image.height * small_w / image.width))
    return image.resize((small_w, small_h), Image.BILINEAR).resize(image.size, Image.NEAREST)


def _picture(image_path: Path | None, max_w: int, max_h: int, cells: int = 0):
    from PIL import Image

    if image_path is None or not Path(image_path).is_file():
        return None
    try:
        with Image.open(image_path) as raw:
            img = raw.convert("RGB")
    except OSError:
        return None
    scale = min(max_w / img.width, max_h / img.height)
    img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))),
                     Image.LANCZOS)
    return pixelate(img, cells) if cells else img


def _text_lines(draw, text: str, font, max_lines: int) -> list[str]:
    from news_story.post_composer import _wrap

    lines = _wrap(draw, text, font, _TEXT_W)
    if len(lines) <= max_lines:
        return lines
    kept = lines[:max_lines]
    last = kept[-1]
    while last and draw.textlength(last + " …", font=font) > _TEXT_W:
        last = last.rsplit(" ", 1)[0] if " " in last else last[:-1]
    kept[-1] = last.rstrip(" ,;:") + " …"
    return kept


def _sentences_in(draw, text: str, font, max_lines: int) -> list[str]:
    """Les phrases entieres du debut de `text` qui tiennent en `max_lines`
    lignes ; a defaut (premiere phrase trop longue), coupe avec « … »."""
    from news_story.article_text import split_sentences
    from news_story.post_composer import _wrap

    kept: list[str] = []
    for sentence in split_sentences(text):
        lines = _wrap(draw, " ".join(kept + [sentence]), font, _TEXT_W)
        if len(lines) > max_lines:
            break
        kept.append(sentence)
    if kept:
        return _wrap(draw, " ".join(kept), font, _TEXT_W)
    return _text_lines(draw, text, font, max_lines)


def _fit_title(draw, title: str):
    from news_story.post_composer import _font, _wrap

    for size in range(_TITLE_SIZES[0], _TITLE_SIZES[1] - 1, -4):
        font = _font(size)
        lines = _wrap(draw, title, font, _TEXT_W)
        if len(lines) <= 2 and all(draw.textlength(l, font=font) <= _TEXT_W for l in lines):
            return font, lines, size
    font = _font(_TITLE_SIZES[1])
    return font, _text_lines(draw, title, font, 3), _TITLE_SIZES[1]


def compose_slide(slide: Slide, out_path, *, logo_path: Path | None = None, cta: str = "",
                  cells: int = 0) -> Path:
    """Une fiche 9:16. `cells` > 0 : l'image est pixelisee (devine le film),
    et le fond est uni -- un fond flou de la photo trahirait la reponse."""
    from PIL import Image, ImageDraw

    from news_story.post_composer import _font, _french_spacing, _load_logo, _subtitle_font
    from news_story.video_composer import (_CTA_LINE_HEIGHT, _CTA_SIZE, _cta_block, _draw_rich,
                                           _rich_width)

    canvas = _blurred_background(None if cells else slide.image_path)
    probe = ImageDraw.Draw(canvas)
    center = W / 2

    # Mise en page du bas vers le haut : phrase d'appel, logo, textes, image.
    logo = _load_logo(logo_path)
    cta_font, cta_lines, cta_h = _cta_block(probe, _french_spacing(cta or ""), _TEXT_W)
    bottom_h = (logo.height if logo else 0) + (14 + cta_h if cta_h else 0)

    label = " ".join((slide.label or "").split()).upper()
    label_font = _font(_LABEL_SIZE)
    title = _french_spacing(" ".join((slide.title or "").split()).upper())
    title_font, title_lines, title_size = _fit_title(probe, title)
    info_font, body_font = _subtitle_font(_INFO_SIZE), _subtitle_font(_BODY_SIZE)
    info_lines: list[str] = []
    for line in slide.lines:
        line = _french_spacing(" ".join((line or "").split()))
        if line:
            info_lines += _text_lines(probe, line, info_font, 2)
    body = _french_spacing(" ".join((slide.body or "").split()))

    label_h = sum(label_font.getmetrics()) if label else 0
    title_lh = int(title_size * 1.08)
    info_lh, body_lh = int(_INFO_SIZE * 1.22), int(_BODY_SIZE * 1.22)

    def text_height(body_lines: list[str]) -> int:
        h = (label_h + _GAP if label else 0) + title_lh * len(title_lines)
        h += (14 + info_lh * len(info_lines)) if info_lines else 0
        h += (18 + body_lh * len(body_lines)) if body_lines else 0
        return h

    # Le synopsis cede la place a l'image : il n'apparait (en partie ou en
    # entier) que si l'image garde sa taille confortable. L'image prend
    # ensuite toute la place restante, sans jamais deborder sur le texte.
    def room_for(lines: list[str]) -> int:
        return _SAFE_BOTTOM - bottom_h - _GAP - text_height(lines) - _GAP - _SAFE_TOP

    body_lines: list[str] = []
    for count in range(_BODY_MAX_LINES, 0, -1):
        candidate = _sentences_in(probe, body, body_font, count) if body else []
        if candidate and room_for(candidate) >= _PICTURE_MIN_H:
            body_lines = candidate
            break
    text_h = text_height(body_lines)
    picture = _picture(slide.image_path, W - 2 * 60, max(120, room_for(body_lines)), cells)

    # Bloc image + textes centre verticalement dans la zone sure.
    total = (picture.height + _GAP if picture else 0) + text_h
    free = _SAFE_BOTTOM - bottom_h - _GAP - _SAFE_TOP - total
    y = _SAFE_TOP + max(0, free // 2)
    if picture is not None:
        shadow = Image.new("RGBA", (picture.width + 24, picture.height + 24), (0, 0, 0, 0))
        from PIL import ImageFilter

        shadow.paste((0, 0, 0, 170), (12, 12, picture.width + 12, picture.height + 12))
        shadow = shadow.filter(ImageFilter.GaussianBlur(10))
        canvas = canvas.convert("RGBA")
        canvas.alpha_composite(shadow, (int(center - picture.width / 2) - 6, y - 4))
        canvas = canvas.convert("RGB")
        canvas.paste(picture, (int(center - picture.width / 2), y))
        y += picture.height + _GAP

    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    def centered(text, font, top, fill, shadow=3):
        x = center - draw.textlength(text, font=font) / 2
        draw.text((x + shadow, top + shadow), text, font=font, fill=_BLACK + (255,))
        draw.text((x, top), text, font=font, fill=fill + (255,))

    if label:
        centered(label, label_font, y, _ACCENT, shadow=2)
        text_w = draw.textlength(label, font=label_font)
        ascent, descent = label_font.getmetrics()
        y_rule = y + (ascent + descent) // 2 + 2
        left_end, right_start = center - text_w / 2 - 26, center + text_w / 2 + 26
        if left_end > _MARGIN:
            draw.rectangle([_MARGIN, y_rule, left_end, y_rule + 1], fill=_WHITE + (255,))
        if right_start < W - _MARGIN:
            draw.rectangle([right_start, y_rule, W - _MARGIN, y_rule + 1], fill=_WHITE + (255,))
        y += label_h + _GAP
    for line in title_lines:
        centered(line, title_font, y, _WHITE, shadow=4)
        y += title_lh
    if info_lines:
        y += 14
        for line in info_lines:
            centered(line, info_font, y, _WHITE, shadow=2)
            y += info_lh
    if body_lines:
        y += 18
        for line in body_lines:
            centered(line, body_font, y, (225, 225, 225), shadow=2)
            y += body_lh

    y = _SAFE_BOTTOM - bottom_h
    if logo is not None:
        layer.alpha_composite(logo, (int(center - logo.width / 2), y))
        y += logo.height + 14
    for line in cta_lines:
        x = center - _rich_width(draw, line, cta_font, _CTA_SIZE) / 2
        _draw_rich(layer, draw, draw, (x, y), line, cta_font, _CTA_SIZE, _WHITE + (255,),
                   _BLACK + (255,))
        y += int(_CTA_SIZE * _CTA_LINE_HEIGHT)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    Image.alpha_composite(canvas.convert("RGBA"), layer).convert("RGB").save(out_path, format="PNG")
    return out_path


# ------------------------------------------------------------- les fiches

def _join(items: list[str], limit: int = 3) -> str:
    return ", ".join(items[:limit])


def _ratings(film) -> str:
    parts = []
    if film.press_rating:
        parts.append(f"Presse {film.press_rating}/5")
    if film.spectator_rating:
        parts.append(f"Spectateurs {film.spectator_rating}/5")
    return " · ".join(parts)


def release_slide(film, image_path: Path | None, index: int, total: int) -> Slide:
    lines = [" · ".join(p for p in (_join(film.genres, 2), film.duration) if p)]
    credits = []
    if film.directors:
        credits.append("De " + _join(film.directors, 2))
    if film.actors:
        credits.append("Avec " + _join(film.actors, 2))
    lines.append(" · ".join(credits))
    lines.append(_ratings(film))
    when = f"SORTIE LE {film.release_date}" if film.release_date else "AU CINÉMA"
    return Slide(image_path, f"{when} · {index}/{total}", film.title, lines, film.synopsis,
                 slug=film.title)


def _ordinal(week: str) -> str:
    return "1re semaine" if week.strip() == "1" else f"{week.strip()}e semaine"


def box_office_slide(entry, image_path: Path | None) -> Slide:
    lines = []
    if entry.entries:
        lines.append(f"{entry.entries} entrées cette semaine")
    detail = []
    if entry.cumulative and entry.cumulative != entry.entries:
        detail.append(f"{entry.cumulative} au total")
    if entry.week:
        detail.append(_ordinal(entry.week))
    lines.append(" · ".join(detail))
    lines.append(entry.distributor)
    return Slide(image_path, f"N°{entry.rank} DU BOX-OFFICE", entry.film.title, lines,
                 slug=f"{entry.rank}-{entry.film.title}")


def guess_hint(film) -> str:
    """L'indice : genre, duree, realisateur -- jamais le titre ni les acteurs."""
    parts = [_join(film.genres, 2), film.duration]
    if film.directors:
        parts.append("de " + _join(film.directors, 1))
    return " · ".join(p for p in parts if p)


# ---------------------------------------------------------------- dossiers

def _slug(text: str) -> str:
    from news_story.daily_top import _slug as slug

    return slug(text)


def _write(slides: list[Slide], out_dir: Path, *, cover_kwargs: dict, caption: str,
           logo_path: Path | None, cta: str, on_progress=None, cells: list[int] | None = None
           ) -> list[Path]:
    from news_story.daily_top import compose_cover

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = [compose_cover(out_dir / "00_couverture.png", logo_path=logo_path, cta=cta,
                             **cover_kwargs)]
    for i, slide in enumerate(slides, 1):
        if on_progress:
            on_progress(i, len(slides))
        path = out_dir / f"{i:02d}_{_slug(slide.slug or slide.title)}.png"
        written.append(compose_slide(slide, path, logo_path=logo_path, cta=cta,
                                     cells=(cells[i - 1] if cells else 0)))
    (out_dir / "legende.txt").write_text(caption, encoding="utf-8")
    return written


def releases_folder_name(day: date) -> str:
    return f"Sorties ciné {day.isoformat()}"


def box_office_folder_name(day: date) -> str:
    return f"Box-office {day.isoformat()}"


def releases_caption(films: list, day: date) -> str:
    from news_story.daily_top import french_date

    lines = [f"🍿 Les sorties ciné de la semaine ({french_date(_wednesday(day))})", ""]
    for i, film in enumerate(films, 1):
        detail = " · ".join(p for p in (_join(film.genres, 2), film.duration) if p)
        lines.append(f"{i}. {film.title}" + (f" — {detail}" if detail else ""))
    lines += ["", "Tu vas voir lequel ? 👇", "", "Source : AlloCiné",
              "#cinema #sortiescinema #film #filmtok #cinematok"]
    return "\n".join(lines) + "\n"


def box_office_caption(entries: list, week_label: str) -> str:
    title = "📊 Box-office France" + (f" — {week_label}" if week_label else "")
    lines = [title, ""]
    for entry in entries:
        lines.append(f"{entry.rank}. {entry.film.title}"
                     + (f" — {entry.entries} entrées" if entry.entries else ""))
    lines += ["", "Tu l'as déjà vu ? 👇", "", "Source : AlloCiné",
              "#cinema #boxoffice #film #filmtok #cinematok"]
    return "\n".join(lines) + "\n"


def compose_releases(films: list, images: list[Path | None], out_dir, *, day: date,
                     logo_path: Path | None = None, cta: str = "", on_progress=None) -> list[Path]:
    from news_story.daily_top import french_date

    slides = [release_slide(f, img, i, len(films)) for i, (f, img) in enumerate(zip(films, images), 1)]
    first = next((img for img in images if img), None)
    return _write(slides, out_dir, logo_path=logo_path, cta=cta, on_progress=on_progress,
                  caption=releases_caption(films, day),
                  cover_kwargs=dict(day=day, count=len(films), background=first,
                                    heading=("SORTIES", "DE LA SEMAINE"),
                                    date_text=f"au cinéma le {french_date(_wednesday(day))}",
                                    info=f"{len(films)} films à voir en salle"))


def _wednesday(day: date) -> date:
    """Mercredi de la semaine de sortie (les films sortent le mercredi)."""
    from datetime import timedelta

    return day - timedelta(days=(day.weekday() - 2) % 7)


def compose_box_office(entries: list, images: list[Path | None], out_dir, *, day: date,
                       week_label: str = "", logo_path: Path | None = None, cta: str = "",
                       on_progress=None) -> list[Path]:
    slides = [box_office_slide(e, img) for e, img in zip(entries, images)]
    first = next((img for img in images if img), None)
    return _write(slides, out_dir, logo_path=logo_path, cta=cta, on_progress=on_progress,
                  caption=box_office_caption(entries, week_label),
                  cover_kwargs=dict(day=day, count=len(entries), background=first,
                                    heading=("BOX-OFFICE", "FRANCE"),
                                    date_text=week_label or None,
                                    info=f"Le top {len(entries)} des entrées"))


# ---------------------------------------------------------- devine le film

GUESS_CELLS = (14, 34)          # question (tres pixelise), indice (moins)


def guess_folder_name(film_title: str) -> str:
    return f"Devine le film {_slug(film_title)}"


def guess_caption() -> str:
    return ("🎬 Devine le film ! Réponse en dernière image 👀\n\n"
            "Tu l'as trouvé à quelle image ? Dis-le en commentaire 👇\n\n"
            "#devinelefilm #cinema #quiz #film #filmtok #cinematok\n")


def compose_guess(film, still: Path, out_dir, *, logo_path: Path | None = None, cta: str = ""
                  ) -> list[Path]:
    """Trois images, sans couverture : la photo tres pixelisee (« DEVINE LE
    FILM »), moins pixelisee avec un indice, puis nette avec la reponse."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    hint = guess_hint(film)
    slides = [
        (Slide(still, "DEVINE LE FILM", "Quel est ce film ?",
               ["Réponse en dernière image"]), GUESS_CELLS[0]),
        (Slide(still, "INDICE", "Tu l'as ?", [hint] if hint else ["Regarde bien…"]), GUESS_CELLS[1]),
        (Slide(still, "RÉPONSE", film.title,
               [" · ".join(p for p in (film.release_date, _join(film.directors, 1)) if p)]), 0),
    ]
    written = []
    for i, (slide, cells) in enumerate(slides, 1):
        written.append(compose_slide(slide, out_dir / f"{i:02d}.png", logo_path=logo_path,
                                     cta=cta, cells=cells))
    (out_dir / "legende.txt").write_text(guess_caption(), encoding="utf-8")
    return written


def pick_still(urls: list[str], download) -> Path | None:
    """La premiere photo en paysage (une photo de scene, pas une affiche) qui
    se telecharge. `download(url) -> Path`."""
    from PIL import Image

    for url in urls[:12]:
        try:
            path = Path(download(url))
            with Image.open(path) as img:
                if img.width >= img.height * 1.25 and img.width >= 600:
                    return path
        except Exception as e:  # noqa: BLE001 -- une photo en echec, on passe a la suivante
            logger.warning(f"Photo de film illisible ({url}) : {e}")
    return None


def download_image(url: str) -> Path | None:
    from news_story.image_cache import ImageFetchError, download

    if not url:
        return None
    try:
        return Path(download(url).path)
    except ImageFetchError as e:
        logger.warning(f"Image introuvable ({url}) : {e}")
        return None


def clean_title_for_answer(title: str) -> str:
    """« Le Seigneur des anneaux : les deux tours (version longue) » -> sans
    la mention de version, inutile pour deviner."""
    return re.sub(r"\s*\((?:version|director'?s cut)[^)]*\)\s*$", "", title, flags=re.I).strip()
