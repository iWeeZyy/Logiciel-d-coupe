"""Lecture de la transcription : signaux linguistiques, rythme, emotions.

Module PUR -- pas de reseau, pas de base, pas d'interface -- comme
production.py, staff.py et radar/scoring.py. Il ne recoit que des phrases deja
construites par editing/sentences.py et rend des mesures.

Deux regles gouvernent tout le fichier :

1. On mesure, on ne devine pas. Chaque signal renvoye correspond a quelque chose
   de compte dans le texte. Une emotion "detectee" reste une SUPPOSITION, et
   elle est publiee avec la phrase qui l'a declenchee pour que l'utilisateur
   tranche lui-meme.
2. Un marqueur isole ne fait pas une emotion. "pourquoi", "attention" ou
   "dernier" sont des mots ordinaires ; c'est leur accumulation, ou leur
   rencontre avec une ponctuation forte, qui autorise une confiance moyenne.
   Une seule occurrence reste en confiance faible, jamais affirmee.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from core.text_utils import contains_phrase, normalize
from radar.analysis.lexicon import EMOTION_LABELS, Lexicon
from radar.analysis.models import (
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    CONFIDENCE_MEDIUM,
    DetectedEmotion,
)

_NUMBER_RE = re.compile(r"\b\d+([.,]\d+)?\b")
_WORD_RE = re.compile(r"[\w'’-]+", re.UNICODE)
# Un nom propre suppose : une majuscule qui n'ouvre pas la phrase. Whisper
# capitalise les debuts de phrase, donc la premiere position ne prouve rien.
_CAPITALIZED_RE = re.compile(r"(?<!^)(?<![.!?…]\s)\b([A-ZÀ-Þ][\wÀ-ÿ'’-]{2,})\b")


@dataclass(frozen=True)
class Silence:
    start: float
    end: float

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass(frozen=True)
class LinguisticProfile:
    """Ce que le texte du clip contient reellement, en chiffres."""

    word_count: int = 0
    sentence_count: int = 0
    duration_s: float = 0.0
    speech_density: float = 0.0        # mots par seconde de clip
    silence_ratio: float = 0.0         # part du clip sans parole
    question_count: int = 0
    exclamation_count: int = 0
    short_sentence_count: int = 0
    long_sentence_count: int = 0
    number_count: int = 0
    proper_noun_candidates: tuple = ()
    negation_count: int = 0
    superlative_count: int = 0
    strong_expression_count: int = 0
    repeated_terms: tuple = ()
    silences: tuple = ()
    longest_silence_s: float = 0.0
    rhythm_change: float = 0.0         # variation de debit entre les deux moities
    signals: tuple = ()                # libelles lisibles, pour l'interface

    @property
    def has_speech(self) -> bool:
        return self.word_count > 0


def _sentence_words(sentence) -> list[str]:
    return _WORD_RE.findall(sentence.text)


def _silences(sentences, gap_s: float) -> list[Silence]:
    """Trous entre deux mots consecutifs, quelle que soit la phrase.

    Calcule sur les MOTS et non sur les phrases : deux phrases separees par un
    silence d'une seconde et deux phrases enchainees ont les memes bornes de
    phrase, seul l'ecart entre mots les distingue.
    """
    words = [w for s in sentences for w in s.words]
    out: list[Silence] = []
    for previous, current in zip(words, words[1:]):
        if current.start - previous.end >= gap_s:
            out.append(Silence(start=previous.end, end=current.start))
    return out


def _repetitions(sentences, minimum: int) -> list[tuple[str, int]]:
    """Mots significatifs repetes. Les mots-outils sont exclus via la liste deja
    utilisee par le Content Factory : une deuxieme liste finirait par diverger."""
    from content_factory.diversity import _STOPWORDS

    counts: dict[str, int] = {}
    for sentence in sentences:
        for raw in _sentence_words(sentence):
            term = normalize(raw)
            if len(term) < 4 or term in _STOPWORDS:
                continue
            counts[term] = counts.get(term, 0) + 1
    repeated = [(term, n) for term, n in counts.items() if n >= minimum]
    repeated.sort(key=lambda item: (-item[1], item[0]))
    return repeated


def _rhythm_change(sentences, duration: float) -> float:
    """Ecart relatif de debit entre la premiere et la seconde moitie du clip.

    Renvoie 0 quand le clip est trop court ou trop peu bavard pour que la
    comparaison veuille dire quelque chose -- une moitie a trois mots produirait
    des variations spectaculaires et vides de sens.
    """
    if duration <= 4.0:
        return 0.0
    middle = duration / 2.0
    first = sum(1 for s in sentences for w in s.words if w.start < middle)
    second = sum(1 for s in sentences for w in s.words if w.start >= middle)
    if first < 3 or second < 3:
        return 0.0
    half = middle if middle > 0 else 1.0
    rate_first = first / half
    rate_second = second / half
    reference = max(rate_first, rate_second)
    return abs(rate_second - rate_first) / reference if reference else 0.0


def analyze(sentences, lexicon: Lexicon, duration_s: float = 0.0) -> LinguisticProfile:
    """Mesure le contenu textuel d'un clip (section 4, etape 4)."""
    if not sentences:
        return LinguisticProfile(duration_s=max(0.0, duration_s),
                                 silence_ratio=1.0 if duration_s > 0 else 0.0,
                                 signals=("aucune parole détectée",))

    spoken_start = min(s.start for s in sentences)
    spoken_end = max(s.end for s in sentences)
    duration = max(duration_s, spoken_end) or (spoken_end - spoken_start)

    all_text = " ".join(s.text for s in sentences)
    normalized = normalize(all_text)
    words_total = sum(len(_sentence_words(s)) for s in sentences)

    short_threshold = lexicon.threshold("short_sentence_words")
    long_threshold = lexicon.threshold("long_sentence_words")

    questions = sum(1 for s in sentences if s.is_question)
    exclamations = sum(s.text.count("!") for s in sentences)
    short_sentences = sum(1 for s in sentences if 0 < len(_sentence_words(s)) <= short_threshold)
    long_sentences = sum(1 for s in sentences if len(_sentence_words(s)) >= long_threshold)

    numbers = len(_NUMBER_RE.findall(all_text))
    proper_nouns: list[str] = []
    for sentence in sentences:
        for match in _CAPITALIZED_RE.findall(sentence.text):
            if match not in proper_nouns:
                proper_nouns.append(match)

    negations = sum(1 for term in lexicon.negations if contains_phrase(normalized, term))
    superlatives = sum(1 for term in lexicon.superlatives if contains_phrase(normalized, term))
    strong = sum(1 for term in lexicon.strong_expressions if contains_phrase(normalized, term))

    silences = _silences(sentences, lexicon.threshold("silence_gap_s"))
    silence_total = sum(s.duration for s in silences)
    silence_ratio = min(1.0, silence_total / duration) if duration > 0 else 0.0
    density = words_total / duration if duration > 0 else 0.0

    repeated = _repetitions(sentences, int(lexicon.threshold("repetition_min_occurrences")))
    rhythm = _rhythm_change(sentences, duration)

    signals: list[str] = []
    if questions:
        signals.append(f"{questions} question(s)")
    if exclamations:
        signals.append(f"{exclamations} exclamation(s)")
    if short_sentences >= 3:
        signals.append(f"{short_sentences} phrases courtes (rythme rapide)")
    if long_sentences:
        signals.append(f"{long_sentences} phrase(s) longue(s)")
    if numbers:
        signals.append(f"{numbers} nombre(s) cité(s)")
    if proper_nouns:
        signals.append("noms propres possibles : " + ", ".join(proper_nouns[:4]))
    if negations >= 2:
        signals.append("tournures négatives")
    if superlatives:
        signals.append(f"{superlatives} superlatif(s)")
    if strong:
        signals.append("expression(s) marquée(s)")
    if repeated:
        signals.append("répétitions : " + ", ".join(term for term, _ in repeated[:3]))
    if rhythm >= lexicon.threshold("rhythm_change_ratio"):
        signals.append(f"changement de rythme ({rhythm * 100:.0f} %)")
    if silences:
        signals.append(f"{len(silences)} silence(s), le plus long "
                       f"{max(s.duration for s in silences):.1f} s")

    return LinguisticProfile(
        word_count=words_total,
        sentence_count=len(sentences),
        duration_s=duration,
        speech_density=density,
        silence_ratio=silence_ratio,
        question_count=questions,
        exclamation_count=exclamations,
        short_sentence_count=short_sentences,
        long_sentence_count=long_sentences,
        number_count=numbers,
        proper_noun_candidates=tuple(proper_nouns[:8]),
        negation_count=negations,
        superlative_count=superlatives,
        strong_expression_count=strong,
        repeated_terms=tuple(repeated[:5]),
        silences=tuple(silences),
        longest_silence_s=max((s.duration for s in silences), default=0.0),
        rhythm_change=rhythm,
        signals=tuple(signals),
    )


def detect_emotions(sentences, lexicon: Lexicon, profile: LinguisticProfile) -> list[DetectedEmotion]:
    """Emotions supposees, avec la phrase qui les a declenchees.

    La confiance ne depend jamais d'un seul mot : il faut plusieurs marqueurs
    distincts, ou un marqueur accompagne d'une ponctuation forte, pour depasser
    "faible". Une detection restee faible doit etre presentee comme une
    hypothese par l'interface, pas comme un fait (section 4, etape 5).
    """
    found: list[DetectedEmotion] = []

    for name, markers in lexicon.emotions.items():
        hits = 0
        distinct = 0
        evidence = ""
        evidence_has_strong_punctuation = False

        for marker in markers:
            marker_hits = 0
            for sentence in sentences:
                if contains_phrase(normalize(sentence.text), marker):
                    marker_hits += 1
                    if not evidence:
                        evidence = sentence.text.strip()
                        evidence_has_strong_punctuation = "!" in sentence.text or "?" in sentence.text
            if marker_hits:
                distinct += 1
                hits += marker_hits

        if not hits:
            continue

        if distinct >= 2 and hits >= 3:
            confidence = CONFIDENCE_HIGH
        elif hits >= 2 or (hits == 1 and evidence_has_strong_punctuation):
            confidence = CONFIDENCE_MEDIUM
        else:
            confidence = CONFIDENCE_LOW

        found.append(DetectedEmotion(
            name=EMOTION_LABELS.get(name, name),
            confidence=confidence,
            evidence=evidence,
        ))

    order = {CONFIDENCE_HIGH: 0, CONFIDENCE_MEDIUM: 1, CONFIDENCE_LOW: 2}
    found.sort(key=lambda e: (order.get(e.confidence, 3), e.name))
    return found
