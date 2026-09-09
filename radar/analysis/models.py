"""Vocabulaire de l'analyse de contenu.

Une analyse est un OBJET DE DONNEES, pas un rapport deja mis en forme : elle
porte ce qui a ete reellement mesure, et l'interface decide de l'affichage. Ce
qui n'a pas pu etre mesure vaut None ou une liste vide, jamais une valeur
plausible -- c'est la meme regle que radar/models.py et que seed de scoring :
une donnee absente doit rester reconnaissable comme absente.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from typing import Optional

# Niveaux d'analyse (section 18). Les valeurs sont stockees en base : les
# changer casserait les analyses deja enregistrees.
LEVEL_FAST = "rapide"
LEVEL_STANDARD = "standard"
LEVEL_DEEP = "approfondie"
LEVELS = (LEVEL_FAST, LEVEL_STANDARD, LEVEL_DEEP)

LEVEL_LABELS = {
    LEVEL_FAST: "⚡ Rapide",
    LEVEL_STANDARD: "🧠 Standard",
    LEVEL_DEEP: "🔬 Approfondie",
}

LEVEL_DESCRIPTIONS = {
    LEVEL_FAST: "Transcription, résumé et description courte.",
    LEVEL_STANDARD: "Transcription horodatée, moment clé, descriptions, titres et hashtags.",
    LEVEL_DEEP: "Standard, plus le rythme, les silences et plusieurs candidats pour le moment clé.",
}

# Confiance globale (section 10). Trois niveaux nommes, JAMAIS un pourcentage :
# le systeme ne dispose d'aucune mesure qui justifierait d'ecrire "97,4 %".
CONFIDENCE_HIGH = "elevee"
CONFIDENCE_MEDIUM = "moyenne"
CONFIDENCE_LOW = "faible"

CONFIDENCE_LABELS = {
    CONFIDENCE_HIGH: "🟢 Confiance élevée",
    CONFIDENCE_MEDIUM: "🟡 Confiance moyenne",
    CONFIDENCE_LOW: "🔴 Confiance faible",
}

# Etats affiches sur une carte du Radar (section 2).
STATE_NONE = "non_analyse"
STATE_RUNNING = "en_cours"
STATE_DONE = "analyse"
STATE_FAILED = "impossible"

STATE_LABELS = {
    STATE_NONE: "○ Non analysé",
    STATE_RUNNING: "⏳ Analyse en cours",
    STATE_DONE: "✓ Analysé",
    STATE_FAILED: "⚠️ Analyse impossible",
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _known(cls, data: dict) -> dict:
    names = {f.name for f in fields(cls)}
    return {k: v for k, v in data.items() if k in names}


@dataclass(frozen=True)
class DetectedEmotion:
    """Une emotion supposee, avec ce sur quoi elle repose.

    `evidence` n'est pas decoratif : une emotion sans preuve citable ne doit pas
    etre affichee comme un fait, et c'est l'interface qui le montre. La
    confiance est un mot ("elevee"/"moyenne"/"faible"), pas un nombre.
    """

    name: str
    confidence: str = CONFIDENCE_LOW
    evidence: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class KeyMoment:
    """Le moment juge le plus important, et son enchainement.

    Les bornes sont celles de PHRASES ENTIERES : une description construite sur
    une phrase coupee au milieu serait incomprehensible (section 5).
    """

    start: float
    end: float
    text: str = ""
    label: str = ""
    setup_text: str = ""
    reaction_text: str = ""
    payoff_text: str = ""
    score: Optional[float] = None
    # Autres candidats, renseignes seulement en analyse approfondie.
    alternatives: tuple = ()

    def to_dict(self) -> dict:
        d = asdict(self)
        d["alternatives"] = [dict(a) for a in self.alternatives]
        return d


@dataclass
class ClipAnalysis:
    """Resultat complet d'une analyse, tel qu'il est stocke et reaffiche.

    Les champs d'identite (streamer, titre, vues...) sont recopies au moment de
    l'analyse : ils decrivent le contenu TEL QU'IL ETAIT quand il a ete analyse.
    Le Radar, lui, continue de mettre a jour ses propres compteurs.
    """

    content_id: str = ""              # cle "plateforme:identifiant"
    platform: str = ""
    creator_label: str = ""
    clip_title: str = ""
    clip_url: str = ""
    duration_s: Optional[float] = None
    published_at: str = ""
    view_count: Optional[int] = None
    radar_score: Optional[float] = None

    # --- transcription
    language: str = ""
    language_probability: Optional[float] = None
    transcript_text: str = ""
    segments: list = field(default_factory=list)   # [{start, end, text}]
    words: list = field(default_factory=list)      # [{text, start, end, probability}]

    # --- analyse
    key_moment: Optional[dict] = None
    summary: str = ""
    description: str = ""
    short_description: str = ""
    social_description: str = ""
    hashtags: list = field(default_factory=list)
    title_direct: str = ""
    title_curiosity: str = ""
    title_punchy: str = ""
    detected_topics: list = field(default_factory=list)
    detected_signals: list = field(default_factory=list)
    detected_emotions: list = field(default_factory=list)
    speech_density: Optional[float] = None
    silence_ratio: Optional[float] = None

    # --- fiabilite et tracabilite
    confidence: str = CONFIDENCE_LOW
    confidence_reasons: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    analysis_level: str = LEVEL_STANDARD
    analyzed_at: str = field(default_factory=utc_now_iso)
    processing_time_s: Optional[float] = None
    model_used: str = ""
    media_fingerprint: str = ""

    @property
    def confidence_label(self) -> str:
        return CONFIDENCE_LABELS.get(self.confidence, self.confidence)

    @property
    def key_moment_start(self) -> Optional[float]:
        return (self.key_moment or {}).get("start")

    @property
    def key_moment_end(self) -> Optional[float]:
        return (self.key_moment or {}).get("end")

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "ClipAnalysis":
        return ClipAnalysis(**_known(ClipAnalysis, d))


def format_timestamp(seconds: float | None) -> str:
    """mm:ss, pour un affichage lisible d'un moment dans le clip."""
    if seconds is None:
        return "--:--"
    seconds = max(0.0, float(seconds))
    return f"{int(seconds // 60):02d}:{int(seconds % 60):02d}"
