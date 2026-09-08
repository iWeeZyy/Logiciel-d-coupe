"""Structures de la couche performances.

Deux objets, et la distinction entre eux est le coeur du systeme :

- ClipFeatures : ce que le logiciel a MESURE avant publication (section 11).
  Rempli automatiquement a la production, jamais saisi.
- ClipPerformance : ce que la plateforme a CONSTATE apres publication
  (section 12). Saisi par l'utilisateur, et entierement facultatif.

Les comparer est tout l'objet de la suite. Les melanger dans une seule
structure rendrait impossible de savoir ce qui vient d'une mesure et ce qui
vient d'une declaration.

Tous les champs de performance sont optionnels, y compris les vues : le
logiciel doit fonctionner meme si l'utilisateur ne renseigne qu'un chiffre, et
distinguer "zero vue" de "je n'ai pas saisi les vues" -- d'ou None et non 0.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Optional

# Version du format des fichiers de donnees. Un futur import CSV/JSON ou un
# changement de structure pourra migrer sans deviner ce qu'il lit.
SCHEMA_VERSION = 1


def _drop_none(d: dict) -> dict:
    return {k: v for k, v in d.items() if v is not None}


@dataclass
class ClipFeatures:
    """Caracteristiques d'un clip au moment de sa production (section 11).

    Rien n'est mesure specialement pour cette fiche : tout provient de ce que le
    pipeline a deja calcule. C'est une PHOTO de ces valeurs, prise a la
    production, parce que les reglages et le code evolueront et qu'une
    comparaison ulterieure doit porter sur ce qui a reellement ete produit.
    """

    clip_id: str
    project: str = ""
    created_at: str = ""

    # Scores du moteur
    hook_score: float = 0.0
    rewatch_score: float = 0.0
    content_score: float = 0.0
    viral_potential_score: float = 0.0
    priority_score: Optional[float] = None

    # Mesures du passage
    duration: float = 0.0
    word_density: float = 0.0
    audio_intensity: float = 0.0
    # None = non mesure pour ce clip. 0.0 signifierait 'aucun silence',
    # ce qui serait faux et fausserait toute correlation ulterieure.
    silence_ratio: Optional[float] = None
    question_detected: bool = False
    loop_potential: Optional[float] = None
    context_quality: Optional[float] = None

    # Choix d'edition, pour l'A/B de la section 19
    category: str = ""
    subtitle_style: str = ""
    title_style: str = ""
    thumbnail_variant: str = ""
    framing_mode: str = ""
    montage_applied: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "ClipFeatures":
        known = {f.name for f in fields(ClipFeatures)}
        return ClipFeatures(**{k: v for k, v in d.items() if k in known})


@dataclass
class ClipPerformance:
    """Statistiques constatees sur la plateforme (section 12).

    Tous les champs sont facultatifs. `platform` et `published_at` servent a la
    normalisation (section 14) : comparer un clip de trois jours a un clip de
    six mois sans le savoir n'aurait aucun sens.
    """

    clip_id: str
    platform: str = ""
    published_at: str = ""          # ISO 8601
    recorded_at: str = ""           # date de la saisie

    views: Optional[int] = None
    likes: Optional[int] = None
    comments: Optional[int] = None
    shares: Optional[int] = None
    saves: Optional[int] = None
    impressions: Optional[int] = None
    average_view_duration_s: Optional[float] = None
    completion_rate: Optional[float] = None   # 0-100
    rewatch_rate: Optional[float] = None      # 0-100
    follower_count: Optional[int] = None      # taille du compte, si connue

    note: str = ""

    def is_empty(self) -> bool:
        """Aucune donnee chiffree : une fiche vide ne doit pas etre enregistree
        ni comptee dans les seuils de fiabilite."""
        return all(getattr(self, name) is None for name in (
            "views", "likes", "comments", "shares", "saves", "impressions",
            "average_view_duration_s", "completion_rate", "rewatch_rate",
        ))

    def to_dict(self) -> dict:
        return _drop_none(asdict(self))

    @staticmethod
    def from_dict(d: dict) -> "ClipPerformance":
        known = {f.name for f in fields(ClipPerformance)}
        return ClipPerformance(**{k: v for k, v in d.items() if k in known})


@dataclass
class PerformanceRecord:
    """Un clip et, s'il a ete publie et saisi, ses resultats."""

    features: ClipFeatures
    performance: Optional[ClipPerformance] = None

    @property
    def clip_id(self) -> str:
        return self.features.clip_id

    @property
    def has_performance(self) -> bool:
        return self.performance is not None and not self.performance.is_empty()
