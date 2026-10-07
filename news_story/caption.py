"""Legende (description) d'un post, prete a copier dans Instagram.

Le visuel ne porte que le titre ; c'est la legende qui raconte la news --
exactement le format des comptes d'actualite cinema/series pris pour modele.
Comme le reste de news_story/, AUCUNE IA generative : la legende est faite du
titre et du resume de l'article tels que fournis par le flux (balisage HTML
retire, redite du titre ecartee -- meme regle que title_shortener), suivis de
la source. Rien n'est reformule ni invente ; l'utilisateur la retouche
librement avant de publier.
"""
from __future__ import annotations

import re

from news_story.title_shortener import _normalize, _strip_html

# Retirer une balise HTML laisse parfois un espace orphelin avant un point ou
# une virgule (« est <b>finalisée</b>. » -> « est finalisée . »).
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([.,)])")

THEME_EMOJI = {"cinema": "🎬", "gaming": "🎮"}
_MAX_SUMMARY_CHARS = 1600  # Instagram coupe a 2200 caracteres au total


def build_caption(title: str, summary: str = "", source_label: str = "",
                  theme: str = "") -> str:
    title = " ".join((title or "").split())
    summary = _SPACE_BEFORE_PUNCT.sub(r"\1", _strip_html(summary or ""))
    if len(summary) > _MAX_SUMMARY_CHARS:
        summary = summary[:_MAX_SUMMARY_CHARS].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"

    emoji = THEME_EMOJI.get(theme, "")
    head = f"{emoji} {title}".strip()
    parts = [head] if title else []
    norm_title, norm_summary = _normalize(title), _normalize(summary)
    # Un resume qui ne fait que repeter le titre n'apporte rien.
    if summary and not (norm_summary.startswith(norm_title) and len(summary) <= len(title) + 3):
        parts.append(summary)
    if source_label.strip():
        parts.append(f"Source : {source_label.strip()}")
    return "\n\n".join(parts)
