"""Confiance globale d'une analyse (section 10).

Trois niveaux nommes, jamais un pourcentage. Le systeme ne dispose d'aucune
mesure qui justifierait d'ecrire "97,4 %" : afficher un tel chiffre serait une
fausse precision, c'est-a-dire une information inventee sur la fiabilite d'une
information -- exactement ce que le reste de l'analyse s'interdit.

Chaque facteur qui a joue est renvoye en clair. Une confiance faible sans motif
ne servirait a rien : ce qui est utile, c'est de savoir SI c'est l'audio, la
quantite de parole ou le manque de contexte qui limite le resultat.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from radar.analysis.models import CONFIDENCE_HIGH, CONFIDENCE_LOW, CONFIDENCE_MEDIUM

# Seuils de la note interne. Ils ne sont jamais affiches : ils servent
# uniquement a choisir entre trois mots.
THRESHOLD_HIGH = 2.0
THRESHOLD_MEDIUM = 0.5

MIN_WORDS_SOLID = 25
MIN_WORDS_USABLE = 8
GOOD_WORD_PROBABILITY = 0.75
POOR_WORD_PROBABILITY = 0.55


@dataclass(frozen=True)
class ConfidenceReport:
    level: str = CONFIDENCE_LOW
    reasons: tuple = field(default_factory=tuple)
    factors: dict = field(default_factory=dict)


def mean_word_probability(transcript) -> float | None:
    """Probabilite moyenne des mots renvoyee par Whisper, ou None.

    None quand le modele ne l'a pas fournie : la confiance se calcule alors
    sans ce facteur, au lieu de supposer une valeur.
    """
    if transcript is None:
        return None
    values = [w.probability for s in getattr(transcript, "segments", []) or []
              for w in getattr(s, "words", []) or [] if w.probability is not None]
    return sum(values) / len(values) if values else None


def no_speech_ratio(transcript) -> float | None:
    """Part moyenne de "pas de parole" estimee par Whisper sur les segments."""
    if transcript is None:
        return None
    values = [s.no_speech_prob for s in getattr(transcript, "segments", []) or []
              if getattr(s, "no_speech_prob", None) is not None]
    return sum(values) / len(values) if values else None


def evaluate(*, profile=None, key_moment=None, word_probability: float | None = None,
             language_probability: float | None = None, speech_absence: float | None = None,
             warnings=()) -> ConfidenceReport:
    """Combine les facteurs disponibles en un niveau et ses motifs."""
    score = 0.0
    reasons: list[str] = []
    factors: dict = {}

    word_count = getattr(profile, "word_count", 0) if profile is not None else 0
    factors["mots_transcrits"] = word_count
    if word_count >= MIN_WORDS_SOLID:
        score += 1.0
        reasons.append(f"{word_count} mots transcrits, de quoi décrire le contenu")
    elif word_count >= MIN_WORDS_USABLE:
        reasons.append(f"seulement {word_count} mots transcrits")
    else:
        score -= 1.0
        reasons.append("très peu de parole exploitable dans ce clip")

    if word_probability is not None:
        factors["probabilite_moyenne_des_mots"] = round(word_probability, 3)
        if word_probability >= GOOD_WORD_PROBABILITY:
            score += 1.0
            reasons.append("transcription nette")
        elif word_probability < POOR_WORD_PROBABILITY:
            score -= 1.0
            reasons.append("transcription incertaine (audio peu clair ou voix couverte)")
    else:
        reasons.append("qualité de transcription non mesurée par le modèle")

    if language_probability is not None:
        factors["probabilite_de_langue"] = round(language_probability, 3)
        if language_probability >= 0.90:
            score += 0.5
        elif language_probability < 0.60:
            score -= 1.0
            reasons.append("langue mal identifiée")

    if speech_absence is not None:
        factors["absence_de_parole_estimee"] = round(speech_absence, 3)
        if speech_absence > 0.60:
            score -= 0.5
            reasons.append("de longs passages sans parole")

    if profile is not None and getattr(profile, "silence_ratio", 0.0) > 0.60:
        score -= 0.5
        reasons.append("le clip est majoritairement silencieux")

    if key_moment is not None:
        score += 0.5
    else:
        score -= 1.0
        reasons.append("aucun moment ne se détache nettement des autres")

    if warnings:
        score -= 0.5

    if score >= THRESHOLD_HIGH:
        level = CONFIDENCE_HIGH
    elif score >= THRESHOLD_MEDIUM:
        level = CONFIDENCE_MEDIUM
    else:
        level = CONFIDENCE_LOW

    factors["note_interne"] = round(score, 2)
    return ConfidenceReport(level=level, reasons=tuple(reasons), factors=factors)
