"""Performance Score : ramener des statistiques heterogenes sur une echelle
comparable, 0 a 100 (section 14).

Le probleme que ce module resout : "clip A = 10 000 vues, clip B = 5 000 vues"
ne dit rien tant qu'on ignore que A a six mois et B trois jours, ou que A a ete
publie quand le compte avait dix fois plus d'abonnes.

Trois principes, et le troisieme est le plus important :

1. On ne compare que ce qui est comparable. Chaque composante est calculee
   RELATIVEMENT aux autres clips de l'utilisateur, pas a un barème absolu :
   "bon" veut dire "bon pour ce compte", seule comparaison qui ait un sens ici.
2. Une donnee absente n'est jamais remplacee par une valeur plausible. Elle est
   retiree du calcul et le poids est redistribue sur ce qui est connu. Le score
   dit alors sur quoi il repose.
3. Le score n'est pas une prediction et ne mesure pas une cause : c'est un
   resume de ce qui a ete constate.

Module pur : ni fichier, ni reseau.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

# Poids des composantes. L'engagement pese plus que les vues brutes : une vue
# est largement subie (elle depend de la distribution), un like ou un partage
# est un geste.
DEFAULT_WEIGHTS = {
    "reach": 0.30,        # vues, rapportees a l'age du clip
    "engagement": 0.35,   # likes + commentaires + partages + enregistrements / vues
    "retention": 0.35,    # taux de completion et revisionnage
}

# En dessous de ce nombre de clips comparables, une composante relative n'a pas
# de sens : classer un clip parmi deux autres ne dit rien.
MIN_COMPARABLE = 3


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_date(value: str):
    if not value:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(value, fmt)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def age_days(performance, reference=None) -> float | None:
    """Age du clip en jours, ou None si la date de publication est inconnue."""
    published = _parse_date(performance.published_at)
    if published is None:
        return None
    delta = (reference or _now()) - published
    return max(0.0, delta.total_seconds() / 86400.0)


def views_per_day(performance, reference=None) -> float | None:
    """Vues rapportees a l'age. Sans date, on renvoie les vues brutes : c'est
    moins juste, mais refuser de comparer serait pire que comparer prudemment
    -- et l'absence de date est signalee dans les composantes manquantes."""
    if performance.views is None:
        return None
    age = age_days(performance, reference)
    if age is None:
        return float(performance.views)
    # Le premier jour compte pour un jour entier : diviser par 0,2 jour
    # donnerait des chiffres absurdes sur un clip publie il y a une heure.
    return float(performance.views) / max(1.0, age)


def engagement_rate(performance) -> float | None:
    """Interactions rapportees aux vues, en pourcentage.

    Sans vues, aucun taux n'est calculable : 200 likes ne veulent rien dire si
    l'on ignore s'ils viennent de 300 ou de 300 000 vues.
    """
    if not performance.views:
        return None
    interactions = sum(
        value for value in (
            performance.likes, performance.comments,
            performance.shares, performance.saves,
        ) if value is not None
    )
    if interactions == 0 and all(
        getattr(performance, name) is None
        for name in ("likes", "comments", "shares", "saves")
    ):
        return None
    return 100.0 * interactions / performance.views


def retention(performance) -> float | None:
    """Retention : taux de completion, complete par le revisionnage s'il est
    connu. Le revisionnage est plafonne a la moitie du poids -- c'est un bonus,
    pas un substitut a la completion."""
    completion = performance.completion_rate
    rewatch = performance.rewatch_rate
    if completion is None and rewatch is None:
        return None
    if completion is None:
        return min(100.0, rewatch)
    if rewatch is None:
        return min(100.0, completion)
    return min(100.0, 0.75 * completion + 0.25 * min(100.0, rewatch))


def _percentile_rank(value: float, population: list[float]) -> float:
    """Position de `value` dans `population`, 0 a 100.

    Rang plutot que normalisation min-max : un seul clip exceptionnel ecraserait
    tous les autres vers zero avec un min-max.
    """
    if not population:
        return 50.0
    below = sum(1 for other in population if other < value)
    equal = sum(1 for other in population if other == value)
    return 100.0 * (below + 0.5 * equal) / len(population)


@dataclass(frozen=True)
class PerformanceScore:
    """Resultat, avec ce sur quoi il repose et ce qui manquait."""

    score: float
    components: dict = field(default_factory=dict)
    missing: tuple = ()
    comparable_clips: int = 0
    reliable: bool = False

    def explain(self) -> str:
        if not self.reliable:
            return (f"Score indicatif : {self.comparable_clips} clip(s) comparable(s), "
                    f"il en faut au moins {MIN_COMPARABLE}.")
        if self.missing:
            return "Calculé sans : " + ", ".join(self.missing)
        return "Calculé sur toutes les composantes."


def compute_performance_score(
    performance,
    population: list,
    weights: dict | None = None,
    reference=None,
) -> PerformanceScore:
    """Performance Score d'un clip, relatif aux `population` autres saisies.

    `population` : les ClipPerformance des autres clips de l'utilisateur. Le
    clip lui-meme peut y figurer, cela ne change pas son rang de facon notable.
    """
    w = dict(DEFAULT_WEIGHTS)
    w.update(weights or {})

    measures = {
        "reach": (views_per_day(performance, reference),
                  [v for v in (views_per_day(p, reference) for p in population) if v is not None]),
        "engagement": (engagement_rate(performance),
                       [v for v in (engagement_rate(p) for p in population) if v is not None]),
        "retention": (retention(performance),
                      [v for v in (retention(p) for p in population) if v is not None]),
    }

    components: dict[str, float] = {}
    missing: list[str] = []
    comparable = 0
    for name, (value, others) in measures.items():
        if value is None:
            missing.append(name)
            continue
        components[name] = _percentile_rank(value, others)
        comparable = max(comparable, len(others))

    if not components:
        return PerformanceScore(score=0.0, missing=tuple(measures), comparable_clips=0)

    # Redistribution du poids sur les seules composantes connues : une donnee
    # absente ne doit ni valoir zero, ni etre remplacee par une estimation.
    total_weight = sum(max(0.0, w.get(name, 0.0)) for name in components)
    if total_weight <= 0:
        score = sum(components.values()) / len(components)
    else:
        score = sum(max(0.0, w.get(n, 0.0)) * v for n, v in components.items()) / total_weight

    return PerformanceScore(
        score=round(score, 1),
        components={k: round(v, 1) for k, v in components.items()},
        missing=tuple(missing),
        comparable_clips=comparable,
        reliable=comparable >= MIN_COMPARABLE,
    )
