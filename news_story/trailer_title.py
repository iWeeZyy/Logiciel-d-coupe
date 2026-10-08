"""Le titre du FILM, tire du titre d'une video de bande-annonce.

Les chaines YouTube titrent « TEMPÊTE Bande Annonce VF Teaser (2026) »,
« MALFAISANTE | Nouvelle bande-annonce officielle VOST [Au cinéma le 14
octobre] » ou « Tempête - Teaser Officiel | Prime Video ». Sur le post, seul
le nom du film compte (demande explicite) ; l'etiquette « BANDE-ANNONCE » dit
deja ce que c'est.

On ne fait que COUPER le titre de la video, jamais le reformuler : tout ce
qui suit le premier separateur (« | », « - ») ou le premier mot de
bande-annonce est retire. Si la coupe ne laisse rien, le titre d'origine est
rendu tel quel.
"""
from __future__ import annotations

import re

_SEPARATORS_RE = re.compile(r"\s+[|–—-]\s+|\s*\|\s*")
_TRAILER_WORDS_RE = re.compile(
    r"\b(?:nouvelle\s+|premi[eè]re\s+|derni[eè]re\s+)?"
    r"(?:bande[- ]?annonce|teaser|trailer|official|officiel(?:le)?|extrait|clip|featurette)\b",
    re.IGNORECASE)
_TRAILING_NOISE_RE = re.compile(
    r"[\s:,;.!\-–—]*(?:\(\d{4}\)|\[[^\]]*\]|\((?:vf|vost|vostfr)\)|\b(?:vf|vost|vostfr)\b)?"
    r"[\s:,;.!\-–—]*$", re.IGNORECASE)


def film_title(video_title: str) -> str:
    original = " ".join((video_title or "").split())
    if not original:
        return ""
    title = _SEPARATORS_RE.split(original, maxsplit=1)[0]
    match = _TRAILER_WORDS_RE.search(title)
    if match:
        title = title[:match.start()]
    # Restes en fin de titre : « (2026) », « [Au cinéma ...] », « VF »...
    previous = None
    while previous != title:
        previous = title
        title = _TRAILING_NOISE_RE.sub("", title).strip()
    return title or original
