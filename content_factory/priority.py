"""Content Factory Priority Score : le classement d'un candidat quand on
produit plusieurs clips d'un coup (section 6 du cahier des charges).

Ce n'est pas un nouveau score de contenu -- il ne mesure rien de neuf sur la
video. Il combine des mesures DEJA calculees pour repondre a une autre
question : parmi tous les passages notes, lesquels valent la peine d'etre
produits en priorite ?

Trois precisions sur ce qui est, ou n'est pas, dedans :

- La DIVERSITE n'y figure pas, bien que la section 6 la cite. Un candidat pris
  isolement n'a pas de diversite : elle n'existe que par rapport a ce qui est
  deja retenu. Elle est donc appliquee pendant la selection
  (content_factory/diversity.py), comme une penalite entre candidats, et non
  comme une composante figee d'un score individuel. L'inverse donnerait un
  chiffre qui ne veut rien dire.

- La QUALITE VISUELLE n'est pas mesuree. Aucune image n'est decodee au moment
  de la selection : la detection de visages tourne clip par clip, apres, parce
  qu'analyser toutes les images d'une video de deux heures couterait plus cher
  que toute la production. Le poids correspondant existe dans la config et vaut
  0 par defaut : la place est prete, la mesure n'est pas inventee.

- Le POTENTIEL DE PUBLICATION est une contrainte de format, pas un jugement :
  duree dans la fenetre publiable et clip qui se termine sur une phrase finie.

Module pur : pas de DB, pas de ffmpeg, pas de reseau.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from core.models import ScoredCandidate
from editing.sentences import SentenceSpan

# Poids par defaut, surchargeables par config/editing.json (content_factory.
# priority.weights). Ils somment a 1.0 hors qualite visuelle, laissee a 0.
DEFAULT_WEIGHTS = {
    "viral": 0.55,
    "context_quality": 0.20,
    "audio_quality": 0.15,
    "publishability": 0.10,
    "visual_quality": 0.0,
}

DEFAULT_PARAMS = {
    # Fenetre de duree consideree comme publiable telle quelle. En dehors, le
    # clip n'est pas rejete -- il perd seulement de la priorite.
    "publishable_min_s": 15.0,
    "publishable_max_s": 90.0,
    # Au-dela de cet ecart a la fenetre, la composante duree tombe a zero.
    "duration_tolerance_s": 30.0,
}


def _clip01(value: float) -> float:
    return max(0.0, min(1.0, value))


@dataclass(frozen=True)
class PriorityBreakdown:
    """Detail du Priority Score, affichable tel quel : l'utilisateur doit
    pouvoir comprendre pourquoi un passage est propose (section 22)."""

    viral: float
    context_quality: float
    audio_quality: float
    publishability: float
    visual_quality: float
    total: float

    def to_dict(self) -> dict:
        return {k: round(v, 1) for k, v in asdict(self).items()}


def context_quality(candidate, sentences: list[SentenceSpan]) -> float:
    """0-100 : dans quelle mesure les bornes du candidat tombent deja sur une
    structure de phrase.

    Un passage qui commence au debut d'une phrase et s'arrete a la fin d'une
    autre est exploitable tel quel ; un passage qui coupe deux phrases en leur
    milieu devra etre recale, et le recalage peut echouer (duree maximale). On
    mesure donc la distance aux bornes de phrase les plus proches, rapportee a
    la duree d'une phrase typique.
    """
    if not sentences:
        return 50.0  # aucune structure connue : ni bon ni mauvais signe

    typical = sum(s.end - s.start for s in sentences) / len(sentences)
    typical = max(0.5, typical)

    start_gap = min(abs(candidate.start - s.start) for s in sentences)
    end_gap = min(abs(candidate.end - s.end) for s in sentences)

    start_score = _clip01(1.0 - start_gap / typical)
    end_score = _clip01(1.0 - end_gap / typical)
    return 100.0 * (0.5 * start_score + 0.5 * end_score)


def audio_quality(candidate, audio_stats: dict) -> float:
    """0-100 : le passage est-il audible et vivant ?

    Reutilise les mesures deja faites par AudioAnalyzer -- niveau moyen replace
    dans la dynamique de la video, et variation d'intensite (un passage plat au
    bon niveau reste un passage plat).
    """
    mean_db = audio_stats.get("mean_db", -40.0)
    p90_db = audio_stats.get("p90_db", -20.0)
    headroom = max(1.0, p90_db - mean_db)

    loudness = _clip01((candidate.audio.rms_mean - mean_db) / headroom)
    liveliness = _clip01(candidate.audio.rms_std / 8.0)
    return 100.0 * (0.7 * loudness + 0.3 * liveliness)


def publishability(candidate, params: dict) -> float:
    """0-100 : le clip est-il directement publiable, en duree et en fin ?"""
    lo = float(params.get("publishable_min_s", DEFAULT_PARAMS["publishable_min_s"]))
    hi = float(params.get("publishable_max_s", DEFAULT_PARAMS["publishable_max_s"]))
    tolerance = max(1e-6, float(params.get("duration_tolerance_s",
                                           DEFAULT_PARAMS["duration_tolerance_s"])))

    duration = candidate.duration
    if lo <= duration <= hi:
        duration_score = 1.0
    else:
        distance = (lo - duration) if duration < lo else (duration - hi)
        duration_score = _clip01(1.0 - distance / tolerance)

    return 100.0 * duration_score


def compute_priority(
    scored: ScoredCandidate,
    sentences: list[SentenceSpan],
    audio_stats: dict,
    weights: dict | None = None,
    params: dict | None = None,
) -> PriorityBreakdown:
    """Priority Score d'un candidat. Les poids absents reprennent la valeur par
    defaut, et l'ensemble est renormalise : une config partielle ou mal sommee
    ne doit pas changer silencieusement l'echelle du score."""
    w = dict(DEFAULT_WEIGHTS)
    w.update(weights or {})
    p = dict(DEFAULT_PARAMS)
    p.update(params or {})

    components = {
        "viral": scored.scores.viral,
        "context_quality": context_quality(scored.candidate, sentences),
        "audio_quality": audio_quality(scored.candidate, audio_stats),
        "publishability": publishability(scored.candidate, p),
        "visual_quality": 0.0,
    }

    total_weight = sum(max(0.0, w.get(name, 0.0)) for name in components)
    if total_weight <= 0:
        total = scored.scores.viral  # config vide : on ne perd pas le classement
    else:
        total = sum(max(0.0, w.get(name, 0.0)) * value
                    for name, value in components.items()) / total_weight

    return PriorityBreakdown(total=total, **components)
