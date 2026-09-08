"""Confrontation de l'estime au constate (sections 15 et 17).

Ce module ne decide rien et ne modifie aucun reglage : il constate. Les
propositions d'ajustement sont l'affaire de learning.py, et elles passeront
toujours par l'utilisateur.

Vocabulaire tenu strictement, parce qu'il engage :
- "estime" / "potentiel estime" pour ce que le moteur a calcule AVANT ;
- "constate" pour ce que la plateforme a mesure APRES ;
- "ecart" pour leur difference, jamais "erreur du modele" -- un potentiel
  estime n'est pas une prediction de vues, et un ecart n'est donc pas la preuve
  d'une faute de calcul ;
- "correlation" et jamais "cause" : rien ici ne permet d'etablir qu'un facteur
  PROVOQUE une meilleure performance.

Module pur.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from performance.metrics import compute_performance_score

# Seuils de fiabilite (section 17), surchargeables par la config.
DEFAULT_THRESHOLDS = {"none": 10, "trend": 25, "solid": 50}

LEVEL_NONE = "insuffisant"
LEVEL_FIRST = "premieres_tendances"
LEVEL_TRENDS = "tendances"
LEVEL_SOLID = "fiable"

LEVEL_LABELS = {
    LEVEL_NONE: "Analyse insuffisante",
    LEVEL_FIRST: "Premières tendances",
    LEVEL_TRENDS: "Tendances intéressantes",
    LEVEL_SOLID: "Analyse plus fiable",
}


def reliability_level(sample_size: int, thresholds: dict | None = None) -> str:
    t = dict(DEFAULT_THRESHOLDS)
    t.update(thresholds or {})
    if sample_size >= t["solid"]:
        return LEVEL_SOLID
    if sample_size >= t["trend"]:
        return LEVEL_TRENDS
    if sample_size >= t["none"]:
        return LEVEL_FIRST
    return LEVEL_NONE


def confidence_percent(sample_size: int, thresholds: dict | None = None) -> int:
    """Confiance affichable, 0-100, plafonnee.

    Volontairement plafonnee sous 100 : aucune quantite de donnees saisies a la
    main ne justifie d'annoncer une certitude (section 22, "ne jamais afficher
    une fausse precision").
    """
    t = dict(DEFAULT_THRESHOLDS)
    t.update(thresholds or {})
    solid = max(1, t["solid"])
    return int(min(85, round(100 * min(1.0, sample_size / solid))))


@dataclass(frozen=True)
class Deviation:
    """Ecart entre potentiel estime et performance constatee, pour un clip."""

    clip_id: str
    estimated: float
    observed: float
    reliable: bool

    @property
    def gap(self) -> float:
        return round(self.observed - self.estimated, 1)

    def sentence(self) -> str:
        gap = self.gap
        if abs(gap) < 8:
            return "Performance conforme au potentiel estimé."
        if gap > 0:
            return f"Le clip a dépassé son potentiel estimé de {gap:+.0f} points."
        return f"Le clip est resté {abs(gap):.0f} points sous son potentiel estimé."


def deviations(records, thresholds: dict | None = None) -> list[Deviation]:
    """Ecarts pour tous les clips dont les performances ont ete saisies."""
    with_perf = [r for r in records if r.has_performance]
    population = [r.performance for r in with_perf]
    out = []
    for record in with_perf:
        score = compute_performance_score(record.performance, population)
        out.append(Deviation(
            clip_id=record.clip_id,
            estimated=round(record.features.viral_potential_score, 1),
            observed=score.score,
            reliable=score.reliable,
        ))
    return out


def _pearson(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    if n < 3:
        return 0.0
    mean_x, mean_y = sum(xs) / n, sum(ys) / n
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)
    if var_x <= 1e-12 or var_y <= 1e-12:
        return 0.0
    return cov / ((var_x * var_y) ** 0.5)


CORRELATION_LABELS = ((0.5, "forte"), (0.3, "moyenne"), (0.15, "faible"))


def correlation_label(value: float) -> str:
    magnitude = abs(value)
    for threshold, label in CORRELATION_LABELS:
        if magnitude >= threshold:
            return label
    return "negligeable"


@dataclass(frozen=True)
class FactorCorrelation:
    """Lien constate entre un facteur numerique et la performance.

    "Correlation", jamais "cause" : ces donnees d'observation ne permettent pas
    de dire qu'un facteur provoque une meilleure performance.
    """

    factor: str
    correlation: float
    sample_size: int

    @property
    def label(self) -> str:
        return correlation_label(self.correlation)


NUMERIC_FACTORS = (
    ("hook_score", "Hook Score"),
    ("rewatch_score", "Rewatch Score"),
    ("content_score", "Content Score"),
    ("viral_potential_score", "Viral Potential Score"),
    ("duration", "Durée"),
    ("word_density", "Densité de parole"),
    ("audio_intensity", "Intensité audio"),
    ("context_quality", "Qualité du contexte"),
)


def factor_correlations(records, thresholds: dict | None = None) -> list[FactorCorrelation]:
    """Correlations entre chaque facteur numerique et le Performance Score.

    Un facteur dont la valeur manque sur certains clips n'est calcule que sur
    ceux ou elle existe : on ne remplace jamais une donnee absente par une
    moyenne, ce qui inventerait de la correlation.
    """
    with_perf = [r for r in records if r.has_performance]
    if len(with_perf) < 3:
        return []

    population = [r.performance for r in with_perf]
    scores = {r.clip_id: compute_performance_score(r.performance, population).score
              for r in with_perf}

    out = []
    for attribute, label in NUMERIC_FACTORS:
        pairs = [
            (float(getattr(r.features, attribute)), scores[r.clip_id])
            for r in with_perf
            if getattr(r.features, attribute, None) is not None
        ]
        if len(pairs) < 3:
            continue
        xs = [x for x, _ in pairs]
        ys = [y for _, y in pairs]
        out.append(FactorCorrelation(factor=label, correlation=round(_pearson(xs, ys), 3),
                                     sample_size=len(pairs)))
    out.sort(key=lambda c: abs(c.correlation), reverse=True)
    return out


@dataclass(frozen=True)
class CategoryPerformance:
    """Moyenne constatee pour une valeur d'un facteur non numerique
    (style de sous-titres, variante de miniature, categorie narrative...)."""

    factor: str
    value: str
    average_score: float
    sample_size: int


CATEGORICAL_FACTORS = (
    ("subtitle_style", "Style de sous-titres"),
    ("title_style", "Style de titre"),
    ("thumbnail_variant", "Variante de miniature"),
    ("category", "Catégorie narrative"),
    ("framing_mode", "Cadrage"),
)

# En dessous, une "moyenne" porte sur si peu de clips qu'elle n'apprend rien.
MIN_PER_CATEGORY = 3


def categorical_performance(records, min_per_value: int = MIN_PER_CATEGORY) -> list[CategoryPerformance]:
    with_perf = [r for r in records if r.has_performance]
    if not with_perf:
        return []
    population = [r.performance for r in with_perf]
    scores = {r.clip_id: compute_performance_score(r.performance, population).score
              for r in with_perf}

    out = []
    for attribute, label in CATEGORICAL_FACTORS:
        buckets: dict[str, list[float]] = {}
        for record in with_perf:
            value = getattr(record.features, attribute, "") or ""
            if not value:
                continue
            buckets.setdefault(value, []).append(scores[record.clip_id])
        for value, values in buckets.items():
            if len(values) < min_per_value:
                continue
            out.append(CategoryPerformance(
                factor=label, value=value,
                average_score=round(sum(values) / len(values), 1),
                sample_size=len(values),
            ))
    out.sort(key=lambda c: c.average_score, reverse=True)
    return out


@dataclass(frozen=True)
class AnalysisSummary:
    """Ce que le logiciel a constate, et a quel point c'est solide."""

    sample_size: int
    level: str
    confidence: int
    deviations: list = field(default_factory=list)
    correlations: list = field(default_factory=list)
    categories: list = field(default_factory=list)

    @property
    def level_label(self) -> str:
        return LEVEL_LABELS[self.level]

    @property
    def usable(self) -> bool:
        return self.level != LEVEL_NONE


def analyse(records, thresholds: dict | None = None) -> AnalysisSummary:
    with_perf = [r for r in records if r.has_performance]
    size = len(with_perf)
    level = reliability_level(size, thresholds)
    return AnalysisSummary(
        sample_size=size,
        level=level,
        confidence=confidence_percent(size, thresholds),
        deviations=deviations(records, thresholds),
        # En dessous du premier seuil, on ne presente aucune tendance : afficher
        # une correlation sur cinq clips serait une fausse precision.
        correlations=factor_correlations(records, thresholds) if level != LEVEL_NONE else [],
        categories=categorical_performance(records) if level != LEVEL_NONE else [],
    )
