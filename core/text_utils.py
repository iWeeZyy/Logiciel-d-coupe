"""Petites fonctions de traitement de texte partagees (normalisation, decoupage en phrases).

Reste volontairement sans dependance externe : pas besoin d'un tokenizer NLP
pour ce que text_analyzer.py demande (mots-clefs, questions, longueur de phrase).
"""
from __future__ import annotations

import re
import unicodedata

_SENTENCE_RE = re.compile(r"[^.!?…]+[.!?…]*")
_WORD_RE = re.compile(r"[\w'-]+", re.UNICODE)


def normalize(text: str) -> str:
    """Minuscule + accents retires, pour une comparaison mots-cles robuste."""
    text = text.lower().strip()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return text


def split_sentences(text: str) -> list[str]:
    """Decoupe en phrases en conservant la ponctuation terminale (important pour
    detecter un "?" -- un simple split sur la ponctuation la supprimerait)."""
    parts = [p.strip() for p in _SENTENCE_RE.findall(text) if p.strip()]
    return parts


def count_words(text: str) -> int:
    return len(_WORD_RE.findall(text))


def contains_phrase(normalized_haystack: str, normalized_phrase: str) -> bool:
    """Sous-chaine sur texte deja normalise -- suffisant pour des expressions courtes
    (mots-cles, amorces de question), pas besoin d'un matching token-par-token ici."""
    return normalized_phrase in normalized_haystack
