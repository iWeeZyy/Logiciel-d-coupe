"""Structures de donnees partagees entre tous les modules du pipeline.

Aucun module de video/, analysis/ ou transcription/ ne doit se passer de dicts
non types entre eux : tout transite par ces dataclasses, serialisables en JSON
(pour le cache de transcription et pour output/results.json).
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class Word:
    text: str
    start: float
    end: float
    probability: Optional[float] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Word":
        return Word(text=d["text"], start=d["start"], end=d["end"], probability=d.get("probability"))


@dataclass
class Segment:
    id: int
    start: float
    end: float
    text: str
    words: list[Word] = field(default_factory=list)
    avg_logprob: Optional[float] = None
    no_speech_prob: Optional[float] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    @staticmethod
    def from_dict(d: dict) -> "Segment":
        return Segment(
            id=d["id"],
            start=d["start"],
            end=d["end"],
            text=d["text"],
            words=[Word.from_dict(w) for w in d.get("words", [])],
            avg_logprob=d.get("avg_logprob"),
            no_speech_prob=d.get("no_speech_prob"),
        )


@dataclass
class Transcript:
    language: str
    language_probability: float
    duration: float
    full_text: str
    segments: list[Segment] = field(default_factory=list)

    def words(self) -> list[Word]:
        """Tous les mots horodates, toutes phrases confondues, dans l'ordre."""
        out: list[Word] = []
        for seg in self.segments:
            out.extend(seg.words)
        return out

    def to_dict(self) -> dict:
        return {
            "language": self.language,
            "language_probability": self.language_probability,
            "duration": self.duration,
            "full_text": self.full_text,
            "segments": [s.to_dict() for s in self.segments],
        }

    @staticmethod
    def from_dict(d: dict) -> "Transcript":
        return Transcript(
            language=d["language"],
            language_probability=d["language_probability"],
            duration=d["duration"],
            full_text=d["full_text"],
            segments=[Segment.from_dict(s) for s in d.get("segments", [])],
        )


@dataclass
class AudioFeatures:
    rms_mean: float = 0.0
    rms_std: float = 0.0
    peak_count: int = 0
    intensity_rise: float = 0.0
    silence_before_s: float = 0.0
    silence_after_s: float = 0.0
    pitch_variation: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TextFeatures:
    word_count: int = 0
    words_per_second: float = 0.0
    question_count: int = 0
    keyword_hits: dict[str, int] = field(default_factory=dict)
    keyword_score_raw: float = 0.0
    avg_sentence_length: float = 0.0
    short_sentence_ratio: float = 0.0
    strong_punctuation_count: int = 0
    speech_rate_acceleration: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Candidate:
    start: float
    end: float
    text: str
    words: list[Word]
    audio: AudioFeatures
    text_features: TextFeatures

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class ScoreBreakdown:
    """Detail du scoring d'un passage.

    `total` reste ce qu'il a toujours ete : la somme ponderee des six
    sous-scores d'accroche, autrement dit le **Hook Score** (la propriete `hook`
    en est un simple alias lisible, pas un champ duplique).

    `content`, `rewatch` et `viral` sont venus ensuite : ils recombinent les
    memes mesures deja extraites, sans aucune extraction supplementaire.
    `viral` est le score de classement affiche en tete d'un clip.
    """

    audio: float = 0.0
    keywords: float = 0.0
    questions: float = 0.0
    speech_density: float = 0.0
    silence_build_up: float = 0.0
    intensity: float = 0.0
    total: float = 0.0
    content: float = 0.0
    rewatch: float = 0.0
    viral: float = 0.0

    def __post_init__(self) -> None:
        # Un objet construit avec le seul `total` (code anterieur aux trois
        # nouveaux scores, ou stub de test) reste classable : faute de calcul
        # dedie, le potentiel viral vaut le Hook Score.
        if self.viral == 0.0 and self.total != 0.0:
            self.viral = self.total

    @property
    def hook(self) -> float:
        """Alias lisible de `total` -- meme valeur, aucun champ en double."""
        return self.total

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ScoredCandidate:
    candidate: Candidate
    scores: ScoreBreakdown
    reasons: list[str] = field(default_factory=list)


@dataclass
class ProgressEvent:
    """Emis par core.logging_setup.StepProgress a chaque etape/sous-etape --
    consomme par la CLI (affichage inchange) et par gui/controller.py (signal Qt)."""
    step_index: int
    total_steps: int
    label: str
    sub_label: Optional[str] = None
    elapsed_s: float = 0.0
    clips_found: Optional[int] = None
    step_fraction: Optional[float] = None  # 0..1, avancement DANS l'etape courante si connu
    # Liste complete et ordonnee des etapes de CE run. Elle varie selon les
    # modules actives, d'ou son transport dans l'evenement plutot qu'une copie
    # figee cote interface (qui se desynchroniserait des l'ajout d'une etape).
    step_labels: list[str] = field(default_factory=list)


@dataclass
class ClipResult:
    index: int
    file_name: str
    start: float
    end: float
    duration: float
    score: float
    scores: dict
    transcript: str
    language: str
    reasons: list[str] = field(default_factory=list)
    # Trace de la detection de contexte (editing/context.py) : applique ou non,
    # confiance, categorie narrative, raisons du recalage. Vide quand le module
    # est desactive -- un lecteur plus ancien de results.json ignore la cle.
    context: dict = field(default_factory=dict)
    # Chemins relatifs des fichiers de sous-titres exportes (subtitles/*.srt,
    # *.vtt). Vide quand l'export est desactive -- les sous-titres incrustes
    # dans la video, eux, ne dependent pas de cette liste.
    subtitles: list[str] = field(default_factory=list)
    # Traces des modules d'edition : cadrage retenu (fixe/suivi/deux visages) et
    # montage applique (silences et hesitations retires, zooms). Vides quand les
    # modules sont desactives.
    framing: dict = field(default_factory=dict)
    montage: dict = field(default_factory=dict)
    # Titres/description extraits du clip et miniatures generees (chemins
    # relatifs). Vides quand les modules correspondants sont desactives.
    metadata: dict = field(default_factory=dict)
    thumbnails: list[str] = field(default_factory=list)

    @property
    def category(self) -> str:
        return self.context.get("category", "")

    def to_dict(self) -> dict:
        return {
            "clip": self.file_name,
            "start": round(self.start, 2),
            "end": round(self.end, 2),
            "duration": round(self.duration, 2),
            "score": round(self.score, 1),
            "scores": {k: round(v, 1) for k, v in self.scores.items()},
            "language": self.language,
            "transcript": self.transcript,
            "reasons": self.reasons,
            "context": self.context,
            "subtitles": self.subtitles,
            "framing": self.framing,
            "montage": self.montage,
            "metadata": self.metadata,
            "thumbnails": self.thumbnails,
        }
