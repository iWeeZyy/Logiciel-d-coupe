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
    audio: float = 0.0
    keywords: float = 0.0
    questions: float = 0.0
    speech_density: float = 0.0
    silence_build_up: float = 0.0
    intensity: float = 0.0
    total: float = 0.0

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
        }
