"""Decoupage de la transcription en phrases horodatees.

Construit uniquement a partir des mots horodates de Whisper (core.models.Word) :
une phrase se termine sur une ponctuation forte portee par le mot lui-meme, ou
sur une pause plus longue que `max_gap_s`. On ne tente PAS de realigner le texte
d'un segment sur ses mots (alignement approximatif, source d'erreurs d'une demi-
phrase) : faster-whisper attache deja la ponctuation au mot qu'elle suit.

Module partage : la detection de contexte (editing/context.py), le decoupage des
sous-titres et l'extraction de titres s'appuient tous dessus, pour qu'un seul
endroit decide ou commence et ou finit une phrase.

Limite connue et assumee : une abreviation ("M.", "etc.") termine une phrase a
tort. Sans dictionnaire d'abreviations par langue, le cout d'une phrase coupee
un mot trop tot est bien inferieur a celui d'un decoupage qui ne se declenche
jamais -- et la pause reelle du locuteur rattrape le plus souvent le cas.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.models import Word
from core.text_utils import ends_sentence, normalize


@dataclass(frozen=True)
class SentenceSpan:
    """Une phrase avec ses bornes temporelles reelles (celles de ses mots)."""

    start: float
    end: float
    text: str
    words: tuple[Word, ...]
    ends_with_punctuation: bool
    is_question: bool

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def word_count(self) -> int:
        return len(self.words)


def build_sentences(
    words: list[Word],
    max_gap_s: float = 0.6,
    question_starters: tuple[str, ...] | list[str] = (),
) -> list[SentenceSpan]:
    """Regroupe des mots horodates en phrases.

    `question_starters` vient de config/hooks_keywords.json (jamais code en dur
    ici) et sert a reconnaitre une question meme quand Whisper n'a pas mis de
    "?" -- ce qui arrive regulierement a l'oral.
    """
    if not words:
        return []

    starters = tuple(normalize(q) for q in question_starters if q)
    sentences: list[SentenceSpan] = []
    current: list[Word] = []

    for i, word in enumerate(words):
        current.append(word)
        is_last = i + 1 >= len(words)
        gap_to_next = 0.0 if is_last else max(0.0, words[i + 1].start - word.end)

        if is_last or ends_sentence(word.text) or gap_to_next > max_gap_s:
            sentences.append(_make_span(current, ends_with_punctuation=ends_sentence(word.text), starters=starters))
            current = []

    return sentences


def _make_span(words: list[Word], ends_with_punctuation: bool, starters: tuple[str, ...]) -> SentenceSpan:
    text = " ".join(w.text.strip() for w in words).strip()
    normalized = normalize(text)
    is_question = "?" in text or any(normalized.startswith(s) for s in starters)
    return SentenceSpan(
        start=words[0].start,
        end=words[-1].end,
        text=text,
        words=tuple(words),
        ends_with_punctuation=ends_with_punctuation,
        is_question=is_question,
    )


def index_containing(sentences: list[SentenceSpan], t: float) -> int | None:
    """Index de la phrase qui contient l'instant t, None si t tombe dans un
    silence entre deux phrases (ou hors de la plage couverte)."""
    for i, s in enumerate(sentences):
        if s.start <= t < s.end:
            return i
    return None


def index_next_starting_at_or_after(sentences: list[SentenceSpan], t: float) -> int | None:
    for i, s in enumerate(sentences):
        if s.start >= t:
            return i
    return None


def index_last_ending_at_or_before(sentences: list[SentenceSpan], t: float) -> int | None:
    found = None
    for i, s in enumerate(sentences):
        if s.end <= t:
            found = i
        else:
            break
    return found


def sentences_in_range(sentences: list[SentenceSpan], start: float, end: float) -> list[SentenceSpan]:
    """Phrases dont une partie au moins tombe dans [start, end]."""
    return [s for s in sentences if s.start < end and s.end > start]


def join_text(sentences: list[SentenceSpan]) -> str:
    return " ".join(s.text for s in sentences).strip()
