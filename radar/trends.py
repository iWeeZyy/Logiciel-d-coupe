"""Detection de progression, a partir de NOS releves successifs.

Le point a comprendre avant de lire ce module : ni l'API YouTube ni l'API Twitch
ne renvoient une vitesse. Elles renvoient un compteur a l'instant present. Une
progression ne peut donc etre calculee qu'entre deux releves que NOUS avons
pris, a deux moments differents.

Consequence directe, et assumee : au premier scan d'un contenu, aucune tendance
n'est calculable. Le module renvoie alors l'etat "pas encore de point de
comparaison" -- et surtout pas une progression de 0 %, qui serait fausse.

Le vocabulaire suit la meme regle que le reste du projet : "potentiel eleve
detecte", "forte progression", jamais "ce clip va devenir viral". Un niveau de
confiance accompagne chaque detection, fonde sur ce qui la soutient reellement
(nombre de releves, duree observee, volume).

Module pur.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone

# En dessous, l'ecart entre deux releves est trop court pour qu'une variation
# soit distinguable du bruit d'arrondi des compteurs publics.
MIN_INTERVAL_S = 300.0

# Progression horaire, en pourcentage du compteur precedent, a partir de
# laquelle on parle de forte progression.
STRONG_GROWTH_PERCENT_PER_HOUR = 25.0
MODERATE_GROWTH_PERCENT_PER_HOUR = 8.0

LEVEL_UNKNOWN = "inconnue"
LEVEL_FLAT = "stable"
LEVEL_MODERATE = "moderee"
LEVEL_STRONG = "forte"

LEVEL_LABELS = {
    LEVEL_UNKNOWN: "Pas encore de point de comparaison",
    LEVEL_FLAT: "Stable",
    LEVEL_MODERATE: "En progression",
    LEVEL_STRONG: "🚀 Forte progression détectée",
}


def _parse(stamp: str):
    if not stamp:
        return None
    try:
        return datetime.fromisoformat(stamp)
    except ValueError:
        return None


@dataclass(frozen=True)
class Trend:
    """Progression constatee entre deux releves, ou son absence."""

    level: str = LEVEL_UNKNOWN
    growth_percent: float | None = None        # variation totale entre les deux releves
    growth_percent_per_hour: float | None = None
    views_per_hour: float | None = None
    from_views: int | None = None
    to_views: int | None = None
    interval_hours: float | None = None
    confidence: int = 0                        # 0-100
    samples: int = 0

    @property
    def label(self) -> str:
        return LEVEL_LABELS[self.level]

    @property
    def detected(self) -> bool:
        return self.level in (LEVEL_MODERATE, LEVEL_STRONG)

    def sentence(self) -> str:
        """Phrase affichable. Silencieuse quand il n'y a rien a dire."""
        if self.level == LEVEL_UNKNOWN:
            return ("Premier relevé : la progression sera mesurable au prochain scan.")
        if self.level == LEVEL_FLAT:
            return "Progression faible depuis le dernier relevé."
        return (f"{self.from_views:,} vues → {self.to_views:,} vues "
                f"en {self.interval_hours:.1f} h ({self.growth_percent:+.0f} %)"
                ).replace(",", " ")

    def to_dict(self) -> dict:
        return asdict(self)


def _confidence(samples: int, interval_hours: float, to_views: int) -> int:
    """Confiance dans la progression constatee.

    Trois appuis, et aucun ne suffit seul : le nombre de releves, la duree
    observee (une variation sur dix minutes est fragile), et le volume (passer
    de 10 a 25 vues fait +150 % sans rien signifier).
    """
    by_samples = min(1.0, (samples - 1) / 4.0)
    by_duration = min(1.0, interval_hours / 3.0)
    by_volume = min(1.0, to_views / 5000.0) if to_views else 0.0
    return int(round(100 * (0.35 * by_samples + 0.35 * by_duration + 0.30 * by_volume)))


def compute_trend(snapshots: list) -> Trend:
    """Progression d'un contenu a partir de ses releves, du plus ancien au plus
    recent.

    On compare le dernier releve au plus recent qui en est assez eloigne dans
    le temps : comparer a l'avant-dernier, s'il date de deux minutes, ne mesure
    que le bruit.
    """
    usable = [s for s in snapshots if s.view_count is not None]
    if len(usable) < 2:
        return Trend(samples=len(usable))

    latest = usable[-1]
    latest_time = _parse(latest.captured_at)
    if latest_time is None:
        return Trend(samples=len(usable))

    reference = None
    for candidate in reversed(usable[:-1]):
        candidate_time = _parse(candidate.captured_at)
        if candidate_time is None:
            continue
        if (latest_time - candidate_time).total_seconds() >= MIN_INTERVAL_S:
            reference = (candidate, candidate_time)
            break
    if reference is None:
        # Des releves existent, mais tous trop rapproches pour conclure.
        return Trend(samples=len(usable))

    previous, previous_time = reference
    interval_hours = (latest_time - previous_time).total_seconds() / 3600.0
    gained = latest.view_count - previous.view_count

    growth_percent = (100.0 * gained / previous.view_count) if previous.view_count else None
    growth_per_hour = (growth_percent / interval_hours) if growth_percent is not None else None
    views_per_hour = gained / interval_hours if interval_hours > 0 else None

    if growth_per_hour is None:
        level = LEVEL_UNKNOWN
    elif growth_per_hour >= STRONG_GROWTH_PERCENT_PER_HOUR:
        level = LEVEL_STRONG
    elif growth_per_hour >= MODERATE_GROWTH_PERCENT_PER_HOUR:
        level = LEVEL_MODERATE
    else:
        level = LEVEL_FLAT

    return Trend(
        level=level,
        growth_percent=round(growth_percent, 1) if growth_percent is not None else None,
        growth_percent_per_hour=round(growth_per_hour, 1) if growth_per_hour is not None else None,
        views_per_hour=round(views_per_hour, 1) if views_per_hour is not None else None,
        from_views=previous.view_count,
        to_views=latest.view_count,
        interval_hours=round(interval_hours, 2),
        confidence=_confidence(len(usable), interval_hours, latest.view_count),
        samples=len(usable),
    )


def age_hours(published_at: str, reference=None) -> float | None:
    published = _parse(published_at)
    if published is None:
        return None
    now = reference or datetime.now(timezone.utc)
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    return max(0.0, (now - published).total_seconds() / 3600.0)


def views_per_hour_since_publication(view_count, published_at: str, reference=None) -> float | None:
    """Vitesse moyenne depuis la publication.

    Utilisable des le PREMIER releve, contrairement a la progression : elle ne
    compare pas deux releves, elle rapporte un compteur a un age. C'est une
    moyenne, pas une vitesse instantanee -- un contenu qui a explose puis
    stagne affiche la meme valeur qu'un contenu regulier.
    """
    if view_count is None:
        return None
    age = age_hours(published_at, reference)
    if age is None:
        return None
    return view_count / max(0.5, age)
