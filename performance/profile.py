"""Profil de performance personnel (section 20) et son influence sur la
selection (section 25).

Deux idees, et la seconde compte autant que la premiere :

1. Ce qui semble fonctionner POUR CET UTILISATEUR se deduit de ses propres
   clips : duree, style de sous-titres, style de titre, variante de miniature,
   categorie narrative. Rien n'est generalise a partir de personne d'autre.

2. L'historique personnel ne remplace JAMAIS les scores generaux. Il les
   module, dans une limite fixe. Un utilisateur qui a publie trente clips ne
   connait pas encore assez son audience pour que le moteur cesse de regarder
   le contenu lui-meme -- et le jour ou il change de format, un profil devenu
   dominant l'enfermerait dans ce qu'il faisait avant.

Le vocabulaire reste celui de l'observation : "semble mieux fonctionner",
jamais "fonctionne parce que".

Module pur.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from performance.analyzer import (
    LEVEL_NONE,
    analyse,
    categorical_performance,
)
from performance.metrics import compute_performance_score

# Part maximale que le profil personnel peut prendre dans le classement final.
# 0.25 : il module, il ne decide pas (section 25).
MAX_PERSONAL_INFLUENCE = 0.25

# Nombre minimal de clips portant une valeur pour qu'elle entre dans le profil.
MIN_PER_VALUE = 3


@dataclass(frozen=True)
class DurationPreference:
    """Fenetre de duree associee aux meilleures performances constatees."""

    low: float
    high: float
    sample_size: int

    def label(self) -> str:
        return f"{self.low:.0f}–{self.high:.0f} s"


@dataclass(frozen=True)
class PersonalProfile:
    """Ce qui semble fonctionner pour cet utilisateur, et a quel point c'est solide."""

    sample_size: int = 0
    level: str = LEVEL_NONE
    confidence: int = 0
    duration: DurationPreference | None = None
    preferences: dict = field(default_factory=dict)   # facteur -> valeur
    influence: float = 0.0

    @property
    def usable(self) -> bool:
        return self.level != LEVEL_NONE and self.influence > 0.0

    def lines(self) -> list[str]:
        """Rendu lisible. Vide tant que rien n'est assez solide pour etre dit."""
        if not self.usable:
            return []
        out = []
        if self.duration is not None:
            out.append(f"Durée optimale : {self.duration.label()}")
        for factor, value in self.preferences.items():
            out.append(f"{factor} : {value}")
        return out


def _top_quartile_durations(records) -> DurationPreference | None:
    """Fenetre de duree des clips les mieux notes.

    On prend le quart superieur plutot que le maximum : une fenetre deduite d'un
    seul clip ne serait pas une preference, ce serait une anecdote.
    """
    with_perf = [r for r in records if r.has_performance and r.features.duration > 0]
    if len(with_perf) < 4 * MIN_PER_VALUE // 3:
        return None

    population = [r.performance for r in with_perf]
    scored = sorted(
        ((compute_performance_score(r.performance, population).score, r.features.duration)
         for r in with_perf),
        key=lambda pair: pair[0], reverse=True,
    )
    keep = max(MIN_PER_VALUE, len(scored) // 4)
    durations = sorted(duration for _score, duration in scored[:keep])
    if len(durations) < MIN_PER_VALUE:
        return None
    return DurationPreference(low=durations[0], high=durations[-1], sample_size=len(durations))


def build_profile(records, thresholds: dict | None = None) -> PersonalProfile:
    """Profil deduit des seules donnees de l'utilisateur."""
    summary = analyse(records, thresholds)
    if summary.level == LEVEL_NONE:
        return PersonalProfile(sample_size=summary.sample_size, level=summary.level,
                               confidence=summary.confidence)

    # L'influence croit avec la confiance, sans jamais depasser le plafond.
    influence = round(MAX_PERSONAL_INFLUENCE * summary.confidence / 100.0, 3)

    preferences: dict[str, str] = {}
    for entry in categorical_performance(records, min_per_value=MIN_PER_VALUE):
        # categorical_performance est deja trie par moyenne decroissante : la
        # premiere valeur rencontree pour un facteur est la meilleure.
        preferences.setdefault(entry.factor, entry.value)

    return PersonalProfile(
        sample_size=summary.sample_size,
        level=summary.level,
        confidence=summary.confidence,
        duration=_top_quartile_durations(records),
        preferences=preferences,
        influence=influence,
    )


def affinity(profile: PersonalProfile, duration: float) -> float:
    """0-100 : proximite d'un candidat avec ce qui semble fonctionner.

    Seule la duree est utilisable au moment de la SELECTION : le style de
    sous-titres, le titre et la miniature n'existent pas encore a ce stade du
    pipeline. Les inclure reviendrait a noter un candidat sur des choix qui ne
    seront faits qu'apres -- le profil les sert donc a l'affichage, pas au
    classement.
    """
    if not profile.usable or profile.duration is None or duration <= 0:
        return 50.0  # neutre : ni bonus ni penalite
    low, high = profile.duration.low, profile.duration.high
    if low <= duration <= high:
        return 100.0
    span = max(1.0, high - low)
    distance = (low - duration) if duration < low else (duration - high)
    return max(0.0, 100.0 * (1.0 - distance / span))


def blend(general_score: float, personal_affinity: float, influence: float) -> float:
    """Melange du score general et de l'affinite personnelle.

    `influence` est borne par MAX_PERSONAL_INFLUENCE en amont : le general reste
    majoritaire quoi qu'il arrive. C'est l'equilibre demande par la section 25.
    """
    influence = max(0.0, min(MAX_PERSONAL_INFLUENCE, influence))
    return round((1.0 - influence) * general_score + influence * personal_affinity, 2)
