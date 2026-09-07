"""Video Potential Score -- estime, avant tout telechargement, quelles videos
trouvees valent la peine d'etre analysees. Ne remplace PAS le Hook Score
(analysis/scoring.py) : celui-ci note un *passage* apres transcription reelle,
celui-la note une *video entiere* a partir des seules metadonnees API. Les
deux ne sont jamais additionnes ni confondus.

    Video Potential Score = relevance * w_relevance
                           + popularity * w_popularity
                           + duration_fit * w_duration_fit
                           + quality_proxy * w_quality_proxy

"quality_proxy" est nomme ainsi deliberement : sans transcrire, il n'y a
aucun moyen de juger la qualite reelle du contenu -- ce n'est qu'un indice
faible base sur la duree (une video de 30s ne peut pas fournir 5 clips de 45s,
une video de 8h est rarement un contenu edite avec un vrai fil narratif).
"""
from __future__ import annotations

import math

from core.text_utils import normalize
from youtube.models import PotentialScoreBreakdown, RankedVideo, VideoResult


def _clip(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def _relevance(video: VideoResult, query: str, rank_position: int, total_results: int) -> float:
    total_results = max(total_results, 1)
    position_component = _clip(100.0 * (1.0 - rank_position / total_results))

    query_tokens = set(normalize(query).split())
    text = normalize(f"{video.title} {video.description}")
    text_tokens = set(text.split())
    overlap = len(query_tokens & text_tokens) / len(query_tokens) if query_tokens else 0.0
    keyword_component = _clip(overlap * 100.0)

    return _clip(0.5 * position_component + 0.5 * keyword_component)


def _popularity(video: VideoResult, reference_view_count_log10: float) -> float:
    if video.view_count is None:
        return 0.0  # inconnu -- jamais devine
    return _clip(math.log10(video.view_count + 1) / max(reference_view_count_log10, 1e-6) * 100.0)


def _duration_fit(video: VideoResult, clip_duration: int, min_gap: int, points_per_slot: float) -> float:
    if not video.duration_seconds:
        return 0.0
    slot = clip_duration + min_gap
    usable_slots = video.duration_seconds // slot if slot > 0 else 0
    return _clip(usable_slots * points_per_slot)


def _quality_proxy(video: VideoResult, ideal_min_s: float, ideal_max_s: float) -> float:
    if not video.duration_seconds:
        return 0.0
    d = video.duration_seconds
    if ideal_min_s <= d <= ideal_max_s:
        return 100.0
    if d < ideal_min_s:
        return _clip(100.0 * d / max(ideal_min_s, 1e-6))
    overshoot_ratio = (d - ideal_max_s) / max(ideal_max_s, 1e-6)
    return _clip(100.0 - overshoot_ratio * 50.0)


def score_video(
    video: VideoResult,
    query: str,
    rank_position: int,
    total_results: int,
    clip_duration: int,
    min_gap: int,
    weights: dict,
    params: dict,
) -> RankedVideo:
    relevance = _relevance(video, query, rank_position, total_results)
    popularity = _popularity(video, params.get("reference_view_count_log10", 6.0))
    duration_fit = _duration_fit(video, clip_duration, min_gap, params.get("duration_fit_points_per_slot", 20.0))
    quality_proxy = _quality_proxy(
        video,
        params.get("ideal_duration_min_s", 600.0),
        params.get("ideal_duration_max_s", 5400.0),
    )

    total = (
        relevance * weights.get("relevance", 0)
        + popularity * weights.get("popularity", 0)
        + duration_fit * weights.get("duration_fit", 0)
        + quality_proxy * weights.get("quality_proxy", 0)
    )

    breakdown = PotentialScoreBreakdown(
        relevance=relevance, popularity=popularity, duration_fit=duration_fit,
        quality_proxy=quality_proxy, total=total,
    )
    return RankedVideo(video=video, score=breakdown)


def rank_videos(
    videos: list[VideoResult],
    query: str,
    clip_duration: int,
    min_gap: int,
    weights: dict,
    params: dict,
    sort_by: str = "potential",
) -> list[RankedVideo]:
    total = len(videos)
    ranked = [
        score_video(v, query, i, total, clip_duration, min_gap, weights, params)
        for i, v in enumerate(videos)
    ]

    if sort_by == "potential":
        ranked.sort(key=lambda r: r.score.total, reverse=True)
    elif sort_by == "views":
        ranked.sort(key=lambda r: (r.video.view_count or 0), reverse=True)
    elif sort_by == "date":
        ranked.sort(key=lambda r: r.video.published_at, reverse=True)
    # "relevance" : conserve l'ordre renvoye par l'API (deja trie par pertinence cote YouTube).

    return ranked
