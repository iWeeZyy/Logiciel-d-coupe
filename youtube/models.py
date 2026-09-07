"""Structures de donnees du module de recherche YouTube -- independantes des
dataclasses du pipeline video (core/models.py). La recherche ne doit rien
savoir du moteur de traitement, et inversement (voir la note d'architecture
du cahier des charges : "la recherche doit etre independante du moteur de
traitement video")."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class VideoResult:
    video_id: str
    title: str
    channel_id: str
    channel_title: str
    published_at: str  # ISO 8601, tel que renvoye par l'API
    description: str
    thumbnail_url: str

    # Remplis par un second appel (videos.list) -- absents tant qu'il n'a pas
    # ete fait, jamais devines.
    duration_seconds: Optional[int] = None
    view_count: Optional[int] = None
    like_count: Optional[int] = None
    license: Optional[str] = None  # "youtube" | "creativeCommon" | None (inconnu)

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["url"] = self.url
        return d


@dataclass
class PotentialScoreBreakdown:
    relevance: float = 0.0
    popularity: float = 0.0
    duration_fit: float = 0.0
    quality_proxy: float = 0.0
    total: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RankedVideo:
    video: VideoResult
    score: PotentialScoreBreakdown

    def to_dict(self) -> dict:
        return {"video": self.video.to_dict(), "score": self.score.to_dict()}


@dataclass
class SearchFilters:
    language: Optional[str] = None
    order: str = "relevance"  # relevance | date | rating | viewCount | title
    video_duration_bucket: Optional[str] = None  # short | medium | long (tel quel cote API)
    min_duration_s: Optional[int] = None   # filtre client, apres coup, precis
    max_duration_s: Optional[int] = None
    min_view_count: Optional[int] = None   # filtre client -- search.list n'a pas de parametre "vues minimum"
    published_after: Optional[str] = None  # RFC3339
    published_before: Optional[str] = None
    channel_id: Optional[str] = None
    category_id: Optional[str] = None
    creative_commons_only: bool = False
