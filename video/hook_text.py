"""Texte en haut du clip : une phrase dans un encadre blanc.

Demande de l'utilisateur (2026-11-01) : sa video la plus vue de la semaine
(11 K vues, contre ~800 pour ses compilations) portait en haut une phrase
dans un encadre blanc (« Et vous vous allez faire quoi en Novembre ? »).
Instagram demande aussi du texte unique ajoute pour recommander un compte de
clips.

Le texte n'est JAMAIS invente : c'est une phrase reellement prononcee dans le
clip, choisie par le module « Titres et descriptions » (editing/metadata.py,
extraction sans IA) -- une question d'abord (elle fait commenter), sinon la
phrase la plus percutante. Rien a dire : pas de texte.

Le dessin est fait par Pillow (une image transparente plein cadre, recadree
sur son contenu), posee comme le filigrane : en calque permanent.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from core.logging_setup import get_logger

logger = get_logger()

TOP_PERCENT = 11.0          # sous la barre « Abonnements | Pour toi » de TikTok
MAX_WIDTH_PERCENT = 86.0
MAX_WORDS = 12
MAX_LINES = 3


@dataclass(frozen=True)
class HookText:
    path: str
    x: int
    y: int


_SMALL_WORDS = {"le", "la", "les", "l'", "de", "du", "des", "d'", "un", "une", "et", "à", "a",
                "en", "que", "qui", "pour", "par", "sur", "dans", "au", "aux", "ce", "cette",
                "mon", "ma", "mes", "ton", "ta", "tes", "son", "sa", "ses", "mais", "ou",
                "c'est", "je", "tu", "il", "on", "nous", "vous"}


def clean(text: str) -> str:
    """Nettoie une phrase transcrite ou un titre : guillemets autour, espace
    parasite avant une apostrophe (« c 'est »), score coupe (« 3 -2 »)."""
    text = " ".join((text or "").split())
    text = re.sub(r"(\w) ?['’] ?(\w)", r"\1'\2", text)
    text = re.sub(r"(\d) ?- ?(\d)", r"\1-\2", text)
    return text.strip(' "«»“”').strip()


def shorten(text: str, max_words: int = MAX_WORDS) -> str:
    """Au plus `max_words` mots, sans finir sur un petit mot (« … mes
    enfants qui… » -> « … mes enfants… »)."""
    words = text.split()
    if len(words) <= max_words:
        return text
    kept = words[:max_words]
    while len(kept) > 3 and kept[-1].lower().strip(",;:") in _SMALL_WORDS:
        kept.pop()
    return " ".join(kept).rstrip(" ,;:") + "…"


def choose_text(titles, max_words: int = MAX_WORDS, preferred: str = "") -> str:
    """La phrase a afficher : une QUESTION reellement posee dans le clip
    d'abord (elle fait commenter) ; sinon `preferred` -- le titre donne au
    clip sur Twitch, ecrit par un humain et souvent la chute du moment --
    s'il fait au moins 3 mots ; sinon la phrase la plus percutante prononcee.
    "" si rien. Fonction pure."""
    by_kind = {}
    for title in titles or ():
        kind = title.get("kind") if isinstance(title, dict) else getattr(title, "kind", "")
        text = title.get("text") if isinstance(title, dict) else getattr(title, "text", "")
        if kind and text and kind not in by_kind:
            by_kind[kind] = " ".join(str(text).split())
    preferred = clean(preferred)
    if len(preferred.split()) < 3:
        preferred = ""
    text = clean(by_kind.get("curiosite") or preferred or by_kind.get("direct") or "")
    return shorten(text, max_words)


def draw(text: str, out_path, out_w: int, out_h: int,
         top_percent: float = TOP_PERCENT) -> HookText | None:
    """Encadre blanc, texte noir gras, centre en haut. None si pas de texte."""
    text = " ".join((text or "").split())
    if not text:
        return None
    from PIL import Image, ImageDraw

    from news_story.post_composer import _font
    from video.compilation import wrap_text

    scale = min(out_w, out_h) / 1080
    image = Image.new("RGBA", (out_w, out_h), (0, 0, 0, 0))
    canvas = ImageDraw.Draw(image)
    font = _font(round(54 * scale))
    max_w = out_w * MAX_WIDTH_PERCENT / 100
    lines = wrap_text(canvas, text, font, max_w - 2 * round(30 * scale), max_lines=MAX_LINES)
    pad_x, pad_y = round(30 * scale), round(20 * scale)
    line_h = round(font.size * 1.2)
    width = max(canvas.textlength(line, font=font) for line in lines) + 2 * pad_x
    height = line_h * len(lines) + 2 * pad_y
    left = round((out_w - width) / 2)
    top = round(out_h * top_percent / 100)
    canvas.rounded_rectangle([left, top, left + width, top + height], radius=round(22 * scale),
                             fill=(255, 255, 255, 245))
    y = top + pad_y
    for line in lines:
        canvas.text(((out_w - canvas.textlength(line, font=font)) / 2, y), line, font=font,
                    fill=(0, 0, 0, 255))
        y += line_h
    box = image.getchannel("A").getbbox()
    x0, y0 = box[0] - box[0] % 2, box[1] - box[1] % 2
    out_path = Path(out_path)
    image.crop((x0, y0, box[2], box[3])).save(out_path)
    return HookText(str(out_path), x0, y0)
