"""Radar Score : quels contenus meritent l'attention (sections 8, 10 ; 7 et 9
du Radar Twitch).

Ce n'est pas un classement par nombre de vues -- c'est explicitement ce que la
section 8 interdit. Un Short a 200 000 vues d'une chaine a 5 millions
d'abonnes est ordinaire ; le meme chiffre chez un createur a 50 000 abonnes est
un evenement. Le score compare donc chaque contenu a deux references : la
vitesse a laquelle il progresse, et ce que ce createur fait D'HABITUDE.

Cinq composantes, toutes affichables separement (section 8 exige la
transparence) :

    vitesse      vues par heure depuis la publication
    engagement   likes et commentaires rapportes aux vues
    relatif      comparaison aux contenus recents du meme createur
    fraicheur    un contenu de six jours n'est plus une opportunite
    format       duree exploitable pour un clip vertical

Chaque composante peut manquer. Une composante absente est RETIREE du calcul et
son poids redistribue, jamais remplacee par une valeur plausible -- meme regle
que le Performance Score. Le score dit alors sur quoi il repose.

La priorite du createur module le resultat sans l'ecraser (section 11 du Radar
Twitch) : un createur en priorite faible qui produit un contenu exceptionnel
doit pouvoir passer devant.

Module pur : ni reseau, ni base de donnees.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from radar.models import PRIORITY_NORMAL, PRIORITY_WEIGHTS
from radar.trends import age_hours, views_per_hour_since_publication

DEFAULT_WEIGHTS = {
    "speed": 0.30,
    "engagement": 0.20,
    "relative": 0.30,
    "freshness": 0.10,
    "format": 0.10,
}

DEFAULT_PARAMS = {
    # Vitesse a laquelle la composante atteint 100. Volontairement haute :
    # sinon tout contenu correct plafonne et le score ne discrimine plus.
    "views_per_hour_full_score": 5000.0,
    # Taux d'engagement (interactions / vues, en %) valant 100.
    "engagement_full_score_percent": 8.0,
    # Au-dela, un contenu n'est plus une opportunite du jour.
    "freshness_half_life_hours": 24.0,
    # Fenetre de duree exploitable pour un format vertical court.
    "format_min_s": 8.0,
    "format_max_s": 90.0,
    # Multiple de la moyenne du createur valant 100 en performance relative.
    "relative_full_score_ratio": 4.0,
    # En dessous, la "moyenne recente du createur" ne veut rien dire.
    "min_history_for_relative": 3,
}


def _clip(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


@dataclass(frozen=True)
class RadarScore:
    """Score et son detail. `missing` nomme ce qui n'a pas pu etre mesure."""

    total: float
    components: dict = field(default_factory=dict)
    missing: tuple = ()
    relative_ratio: float | None = None
    priority_factor: float = 1.0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["missing"] = list(self.missing)
        return d

    def lines(self) -> list[str]:
        """Detail affichable, dans l'ordre de la section 8."""
        icons = {"speed": "🔥 Vitesse des vues", "engagement": "❤️ Engagement",
                 "relative": "📈 Performance relative", "freshness": "🕐 Fraîcheur",
                 "format": "🎯 Format"}
        return [f"{icons[name]} : {value:.0f}"
                for name, value in self.components.items() if name in icons]

    def explain_missing(self) -> str:
        if not self.missing:
            return ""
        labels = {"speed": "vitesse", "engagement": "engagement",
                  "relative": "historique du créateur", "freshness": "date de publication",
                  "format": "durée"}
        return "Non mesuré : " + ", ".join(labels.get(m, m) for m in self.missing)


def speed_component(opportunity, params: dict, reference=None) -> float | None:
    speed = views_per_hour_since_publication(
        opportunity.view_count, opportunity.published_at, reference
    )
    if speed is None:
        return None
    full = max(1.0, float(params["views_per_hour_full_score"]))
    return _clip(100.0 * speed / full)


def engagement_component(opportunity, params: dict) -> float | None:
    """Interactions rapportees aux vues.

    Sans vues, aucun taux : 5 000 likes ne veulent rien dire si l'on ignore
    s'ils viennent de 20 000 ou de 2 millions de vues.
    """
    if not opportunity.view_count:
        return None
    interactions = sum(v for v in (opportunity.like_count, opportunity.comment_count)
                       if v is not None)
    if opportunity.like_count is None and opportunity.comment_count is None:
        return None
    rate = 100.0 * interactions / opportunity.view_count
    full = max(0.01, float(params["engagement_full_score_percent"]))
    return _clip(100.0 * rate / full)


def relative_component(opportunity, creator_history: list, params: dict,
                       reference=None) -> tuple[float | None, float | None]:
    """Comparaison aux contenus recents du meme createur (section 10).

    On compare des VITESSES et non des totaux : un contenu d'il y a trois jours
    a mecaniquement plus de vues qu'un contenu d'il y a trois heures, sans etre
    meilleur. Renvoie (composante, rapport a la moyenne).

    `creator_history` : les autres contenus deja connus de ce createur. En
    dessous du minimum configure, "la moyenne du createur" ne veut rien dire et
    la composante est absente plutot qu'approximative.
    """
    minimum = int(params["min_history_for_relative"])
    speeds = []
    for other in creator_history:
        if other.content_id == opportunity.content_id:
            continue
        speed = views_per_hour_since_publication(other.view_count, other.published_at, reference)
        if speed is not None and speed > 0:
            speeds.append(speed)
    if len(speeds) < minimum:
        return None, None

    own = views_per_hour_since_publication(
        opportunity.view_count, opportunity.published_at, reference
    )
    if own is None:
        return None, None

    average = sum(speeds) / len(speeds)
    if average <= 0:
        return None, None
    ratio = own / average
    full = max(1.0, float(params["relative_full_score_ratio"]))
    # 1x la moyenne = 50 ; `full` fois la moyenne = 100. En dessous de la
    # moyenne, la composante descend sans jamais devenir negative.
    component = 50.0 + 50.0 * (ratio - 1.0) / (full - 1.0) if full > 1 else 50.0
    return _clip(component), round(ratio, 2)


def freshness_component(opportunity, params: dict, reference=None) -> float | None:
    age = age_hours(opportunity.published_at, reference)
    if age is None:
        return None
    half_life = max(1.0, float(params["freshness_half_life_hours"]))
    # Decroissance lineaire jusqu'a deux demi-vies, puis zero.
    return _clip(100.0 * (1.0 - age / (2 * half_life)))


def format_component(opportunity, params: dict) -> float | None:
    if opportunity.duration_s is None:
        return None
    low = float(params["format_min_s"])
    high = float(params["format_max_s"])
    duration = float(opportunity.duration_s)
    if low <= duration <= high:
        return 100.0
    # Hors fenetre : penalise, jamais elimine -- un live de quatre heures reste
    # une opportunite, on y decoupera un extrait.
    distance = (low - duration) if duration < low else (duration - high)
    span = max(1.0, high - low)
    return _clip(100.0 * (1.0 - distance / (2 * span)))


def compute_radar_score(
    opportunity,
    creator_history: list | None = None,
    priority: str = PRIORITY_NORMAL,
    weights: dict | None = None,
    params: dict | None = None,
    reference=None,
) -> RadarScore:
    """Radar Score d'une opportunite. Les poids absents reprennent la valeur par
    defaut et l'ensemble est renormalise."""
    w = dict(DEFAULT_WEIGHTS)
    w.update(weights or {})
    p = dict(DEFAULT_PARAMS)
    p.update(params or {})

    relative, ratio = relative_component(opportunity, creator_history or [], p, reference)
    raw = {
        "speed": speed_component(opportunity, p, reference),
        "engagement": engagement_component(opportunity, p),
        "relative": relative,
        "freshness": freshness_component(opportunity, p, reference),
        "format": format_component(opportunity, p),
    }

    components = {name: value for name, value in raw.items() if value is not None}
    missing = tuple(name for name, value in raw.items() if value is None)

    if not components:
        return RadarScore(total=0.0, missing=missing)

    total_weight = sum(max(0.0, w.get(name, 0.0)) for name in components)
    if total_weight <= 0:
        base = sum(components.values()) / len(components)
    else:
        base = sum(max(0.0, w.get(n, 0.0)) * v for n, v in components.items()) / total_weight

    factor = PRIORITY_WEIGHTS.get(priority, 1.0)
    return RadarScore(
        total=round(_clip(base * factor), 1),
        components={k: round(v, 1) for k, v in components.items()},
        missing=missing,
        relative_ratio=ratio,
        priority_factor=factor,
    )


def relative_sentence(ratio: float | None) -> str:
    """Phrase de la section 10, ou rien si la comparaison n'est pas possible."""
    if ratio is None:
        return ""
    if ratio >= 1.15:
        return f"Ce contenu est actuellement {ratio:.1f}× au-dessus de la moyenne récente du créateur."
    if ratio <= 0.85:
        return f"Ce contenu est actuellement {1 / ratio:.1f}× en dessous de la moyenne récente du créateur."
    return "Ce contenu est dans la moyenne récente du créateur."
