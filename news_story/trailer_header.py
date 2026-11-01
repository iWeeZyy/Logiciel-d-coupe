"""En-tete des bandes-annonces ActuaFilms (design « A », 2026-11-01).

Demande de l'utilisateur : « ajouter le titre un peu comme sur
ClipsOfStreams, a la place du Bande-annonce tout simple […] un truc qui
attire l'oeil mais en gardant Bande-Annonce ». Parmi quatre essais, il a
choisi le A, avec deux precisions : « si le film en question est deja sorti
ne pas mettre "nouveau" » et « trouver aussi quelque chose pour attirer
l'oeil sur la date de sortie ».

De haut en bas, dans la bande floue au-dessus de la video :
- un badge neon anime « ▶ BANDE-ANNONCE » (video/neon_badge.py, le meme que
  le REPLAY de ClipsOfStreams), avec « NOUVEAU » a droite seulement quand
  la date de sortie est connue ET a venir -- deja sorti ou date inconnue :
  le neon seul, rien d'invente ;
- un bandeau noir translucide sur toute la largeur, filets rouges, le titre
  du film en blanc gras (le bandeau de ClipsOfStreams, en rouge cinema) ;
- la date : une page de calendrier (mois + jour) a gauche, « AU CINÉMA LE »
  en petit et la date en grand, en or, a droite ; un reflet lumineux la
  traverse a chaque boucle. Le texte est celui de release_date.line(), tel
  quel : sans date reconnaissable, il reste une simple ligne de texte.

Le neon et la date sont animes (images en boucle, incrustees par ffmpeg,
news_story/video_composer.compose_video) ; le bandeau est fixe.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date

LABEL = "BANDE-ANNONCE"
NEW = "NOUVEAU"
RED = (214, 32, 44)
GOLD = (255, 196, 40)
BAND_ALPHA = 190
BADGE_LEFT = 46
BADGE_OVERLAP = 14            # le badge chevauche le filet du haut du bandeau
BADGE_SCALE = 1.45            # a l'echelle du REPLAY (1), trop petit au-dessus d'un titre de film
GAP_BAND_DATE = 22
TITLE_SIZES = (104, 92, 80, 70, 62, 56)
DATE_SCALES = (1.0, 0.82)
TITLE_MAX_LINES = 2
DATE_FPS = 30
DATE_PERIOD_S = 3.0
SHEEN_S = 0.9                 # duree du passage du reflet, au debut de chaque boucle
_MONTH_SHORT = ("JANV.", "FÉVR.", "MARS", "AVR.", "MAI", "JUIN", "JUIL.", "AOÛT", "SEPT.",
                "OCT.", "NOV.", "DÉC.")
_SPLIT_RE = re.compile(r"^(?P<head>.*?\ble)\s+(?P<day>(?:1er|\d{1,2})\s+\S+(?:\s+\d{4})?)$",
                       re.I)


def is_trailer(label: str) -> bool:
    return " ".join((label or "").split()).upper() == LABEL


# ------------------------------------------------------------------ texte

def split_date(subtitle: str) -> tuple[str, str] | None:
    """« Au cinéma depuis le 21 octobre 2026 » -> (« Au cinéma depuis le »,
    « 21 octobre 2026 ») ; None si la ligne ne finit pas par une date
    reconnaissable. Fonction pure."""
    from news_story.release_date import parse_date

    text = " ".join((subtitle or "").split())
    match = _SPLIT_RE.match(text)
    if not match or parse_date(match.group("day")) is None:
        return None
    return match.group("head"), match.group("day")


def upcoming(subtitle: str, today: date | None = None) -> bool:
    """Vrai seulement si la date de sortie est lue ET a venir (aujourd'hui
    compris : le jour de la sortie, la bande-annonce est encore « nouveau »).
    Date inconnue : faux -- on ne pretend pas que le film est nouveau."""
    from news_story.release_date import parse_date

    parts = split_date(subtitle)
    if parts is None or "depuis" in parts[0].lower() or parts[0].lower().startswith("sorti "):
        return False
    when = parse_date(parts[1], today)
    return when is not None and when >= (today or date.today())


def badge_label(subtitle: str, today: date | None = None) -> str:
    return NEW if upcoming(subtitle, today) else ""


# ---------------------------------------------------------------- dessin

def _fonts_dir():
    from core.paths import app_base_dir

    return app_base_dir() / "assets" / "fonts"


def _font(name: str, size: int):
    from PIL import ImageFont

    try:
        return ImageFont.truetype(str(_fonts_dir() / name), size)
    except OSError:
        from news_story.post_composer import _font as fallback

        return fallback(size)


def _bold(size):
    return _font("BarlowCondensed-ExtraBoldItalic.ttf", size)


def _semi(size):
    return _font("BarlowCondensed-SemiBold.ttf", size)


def render_band(title: str, width: int, size: int, max_w: int):
    """Bandeau titre (RGBA, largeur `width`) ou None si le titre ne tient
    pas en TITLE_MAX_LINES lignes a cette taille."""
    from PIL import Image, ImageDraw

    from video.compilation import wrap_text

    font = _bold(size)
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    text = " ".join((title or "").split()).upper()
    words = text.split()
    lines = wrap_text(probe, text, font, max_w, max_lines=TITLE_MAX_LINES + 1)
    if len(lines) > TITLE_MAX_LINES or any(probe.textlength(l, font=font) > max_w for l in lines) \
            or len(" ".join(lines).split()) < len(words):
        return None
    line_h = round(size * 1.08)
    pad, rule = round(size * 0.24), 8
    band_h = line_h * len(lines) + 2 * pad
    image = Image.new("RGBA", (width, band_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, width, band_h], fill=(0, 0, 0, BAND_ALPHA))
    draw.rectangle([0, 0, width, rule], fill=RED + (255,))
    draw.rectangle([0, band_h - rule, width, band_h], fill=RED + (255,))
    y = pad
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        draw.text(((width - (bbox[2] - bbox[0])) / 2 - bbox[0],
                   y + (line_h - (bbox[3] - bbox[1])) / 2 - bbox[1]), line, font=font,
                  fill=(255, 255, 255))
        y += line_h
    return image


def _calendar(day_text: str, size: int):
    """Page de calendrier : en-tete rouge (mois abrege), jour en grand."""
    from PIL import Image, ImageDraw

    from news_story.release_date import parse_date

    when = parse_date(day_text)
    tile = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(tile)
    radius, head = round(size * 0.16), round(size * 0.32)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius, fill=(252, 248, 240, 255))
    d.rounded_rectangle([0, 0, size - 1, head + radius], radius, fill=RED + (255,))
    d.rectangle([0, head, size - 1, head + radius], fill=(252, 248, 240, 255))
    for ring_x in (size * 0.3, size * 0.7):           # anneaux de la page
        d.rounded_rectangle([ring_x - size * 0.03, -1, ring_x + size * 0.03, size * 0.1],
                            2, fill=(70, 10, 15, 255))
    month = _MONTH_SHORT[when.month - 1] if when else ""
    mf = _bold(round(head * 0.62))
    mb = d.textbbox((0, 0), month, font=mf)
    d.text(((size - (mb[2] - mb[0])) / 2 - mb[0], (head - (mb[3] - mb[1])) / 2 - mb[1] + 2),
           month, font=mf, fill=(255, 255, 255))
    number = str(when.day) if when else ""
    nf = _bold(round((size - head) * 0.78))
    nb = d.textbbox((0, 0), number, font=nf)
    d.text(((size - (nb[2] - nb[0])) / 2 - nb[0],
            head + (size - head - (nb[3] - nb[1])) / 2 - nb[1]), number, font=nf,
           fill=(25, 18, 18))
    return tile


def date_block(subtitle: str, width: int, scale: float = 1.0):
    """Bloc date fixe (RGBA, largeur `width`, contenu centre), ou None si la
    ligne n'a pas de date reconnaissable."""
    from PIL import Image, ImageDraw, ImageFilter

    parts = split_date(subtitle)
    if parts is None:
        return None
    head, day = parts
    tile_size = round(112 * scale)
    tile = _calendar(day, tile_size)
    hf, df = _semi(round(38 * scale)), _bold(round(62 * scale))
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    head, day = head.upper(), day.upper()
    hb, db = probe.textbbox((0, 0), head, font=hf), probe.textbbox((0, 0), day, font=df)
    gap = round(22 * scale)
    text_w = max(hb[2] - hb[0], db[2] - db[0])
    total_w = tile_size + gap + text_w
    if total_w > width:
        return None
    pad = round(14 * scale)                 # place pour l'ombre
    height = tile_size + 2 * pad
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    x0 = (width - total_w) // 2
    shadow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    shadow.paste(Image.new("RGBA", tile.size, (0, 0, 0, 170)), (x0, pad + 6), tile)
    image.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(8)))
    image.alpha_composite(tile, (x0, pad))
    d = ImageDraw.Draw(image)
    tx = x0 + tile_size + gap
    head_h, day_h = hb[3] - hb[1], db[3] - db[1]
    inner = round(10 * scale)
    top = pad + (tile_size - head_h - inner - day_h) / 2
    d.text((tx - hb[0], top - hb[1]), head, font=hf, fill=(255, 255, 255))
    d.text((tx - db[0], top + head_h + inner - db[1]), day, font=df, fill=GOLD,
           stroke_width=max(1, round(2 * scale)), stroke_fill=(60, 30, 0))
    return image


def date_frames(subtitle: str, width: int, scale: float = 1.0, fps: int = DATE_FPS,
                period: float = DATE_PERIOD_S):
    """Bloc date anime : un reflet lumineux le traverse (diagonale) pendant
    SHEEN_S au debut de chaque boucle, puis repos. [] sans date."""
    from PIL import Image, ImageChops, ImageDraw, ImageFilter

    still = date_block(subtitle, width, scale)
    if still is None:
        return []
    w, h = still.size
    alpha = still.getchannel("A")
    content = alpha.getbbox() or (0, 0, w, h)
    span = content[2] - content[0] + h
    out = []
    for n in range(max(2, round(period * fps))):
        t = n / fps
        if t >= SHEEN_S:
            out.append(still)
            continue
        u = 0.5 - 0.5 * math.cos(math.pi * t / SHEEN_S)      # depart et arrivee en douceur
        cx = content[0] - h / 2 + u * span
        band = Image.new("L", (w, h), 0)
        ImageDraw.Draw(band).polygon([(cx - 18 * scale + h * 0.35, 0), (cx + 18 * scale + h * 0.35, 0),
                                      (cx + 18 * scale - h * 0.35, h), (cx - 18 * scale - h * 0.35, h)],
                                     fill=150)
        band = ImageChops.multiply(band.filter(ImageFilter.GaussianBlur(6 * scale)), alpha)
        light = Image.new("RGBA", (w, h), (255, 255, 255, 0))
        light.putalpha(band)
        out.append(Image.alpha_composite(still, light))
    return out


# ------------------------------------------------------------------ plan

@dataclass
class Sprite:
    """Images animees en boucle, posees a (x, y) sur la video."""

    frames: list
    x: int
    y: int
    fps: int


@dataclass
class Header:
    height: int
    band: object                      # image fixe (bandeau)
    band_y: int                       # relatif au haut de l'en-tete
    sprites: list = field(default_factory=list)       # positions relatives
    text_line: str = ""               # date sans forme reconnue : simple texte
    text_y: int = 0


def build(title: str, subtitle: str, width: int, max_w: int, available_h: int,
          today: date | None = None) -> Header | None:
    """Plan de l'en-tete, le plus grand qui tient dans `available_h` ; None
    si meme la plus petite taille ne tient pas (l'appelant revient alors a
    la mise en page simple)."""
    from video import neon_badge

    badge = neon_badge.frames("", BADGE_SCALE, word=LABEL, label=badge_label(subtitle, today),
                              icon="play")
    badge_h = badge[0].height
    text_line = "" if not subtitle or split_date(subtitle) else " ".join(subtitle.split())
    dates = {}
    # La date garde sa taille tant que le titre reste grand (>= 80) ; au-dela,
    # elle est un peu reduite avant que le titre ne le soit encore.
    candidates = ([(s, DATE_SCALES[0]) for s in TITLE_SIZES if s >= 80]
                  + [(s, DATE_SCALES[1]) for s in TITLE_SIZES])
    bands = {}
    for size, date_scale in candidates:
        if size not in bands:
            bands[size] = render_band(title, width, size, max_w)
        band = bands[size]
        if band is None:
            continue
        band_y = badge_h - BADGE_OVERLAP
        if date_scale not in dates:
            dates[date_scale] = date_frames(subtitle, max_w + 140, date_scale) if subtitle else []
        date = dates[date_scale]
        date_h = date[0].height if date else (60 if text_line else 0)
        height = band_y + band.height + (GAP_BAND_DATE + date_h if date_h else 0)
        if height > available_h:
            continue
        sprites = [Sprite(badge, BADGE_LEFT, 0, neon_badge.FPS)]
        bottom = band_y + band.height + GAP_BAND_DATE
        if date:
            sprites.append(Sprite(date, (width - date[0].width) // 2, bottom, DATE_FPS))
        return Header(height, band, band_y, sprites, text_line, bottom)
    return None
