"""Recherche YouTube via la YouTube Data API v3 officielle (REST direct via
`requests`, pas le SDK google-api-python-client -- inutilement lourd pour
deux endpoints).

Cout en quota (voir youtube/quota.py) :
- search.list  : 100 unites, quelle que soit la pagination demandee.
- videos.list  : 1 unite pour jusqu'a 50 IDs -- toujours appele groupe,
  jamais un par un.

Aucune donnee n'est devinee : duration/view_count/license restent None tant
que videos.list n'a pas repondu, jamais estimes.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import requests

from core.logging_setup import get_logger
from core.paths import user_data_dir
from utils.errors import YouTubeApiError, YouTubeConfigError, YouTubeQuotaError
from youtube.models import SearchFilters, VideoResult
from youtube.quota import QuotaTracker

logger = get_logger()

_API_BASE = "https://www.googleapis.com/youtube/v3"
SEARCH_COST_UNITS = 100
VIDEOS_LIST_COST_UNITS = 1

_API_KEY_FILE = user_data_dir() / "youtube_api_key.txt"
_ISO8601_DURATION_RE = re.compile(r"^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$")


def load_api_key() -> str:
    """Ordre : variable d'environnement YOUTUBE_API_KEY, puis un fichier texte
    `youtube_api_key.txt` dans le dossier de donnees de l'utilisateur (jamais
    compile dans le .exe -- voir README, section recherche YouTube)."""
    env_key = os.environ.get("YOUTUBE_API_KEY", "").strip()
    if env_key:
        return env_key
    if _API_KEY_FILE.exists():
        # utf-8-sig, pas utf-8 : la "Windows PowerShell" 5.1 (par opposition a
        # pwsh 7+) ecrit un BOM UTF-8 en tete de fichier avec `-Encoding utf8`
        # -- ce caractere invisible (﻿) n'est pas retire par .strip() (il
        # ne compte pas comme un espace) et corrompait silencieusement la cle,
        # rejetee par Google sans qu'aucun affichage (Notepad, Get-Content) ne
        # laisse voir de difference. utf-8-sig ignore un BOM eventuel tout en
        # lisant un fichier sans BOM normalement.
        key = _API_KEY_FILE.read_text(encoding="utf-8-sig").strip()
        if key:
            return key
    raise YouTubeConfigError(
        "Aucune cle YouTube Data API trouvee. Definis la variable d'environnement "
        "YOUTUBE_API_KEY, ou cree un fichier texte contenant uniquement la cle a "
        # Le chemin exact, pas une description : l'emplacement depend du systeme
        # et du mode (installe ou depot), et le deviner a deja fait perdre du
        # temps a un utilisateur.
        f"cet emplacement precis :\n{_API_KEY_FILE}\n"
        "Voir README.md, section recherche YouTube, pour l'obtenir gratuitement."
    )


def _parse_duration_iso8601(duration: str) -> int:
    match = _ISO8601_DURATION_RE.match(duration or "")
    if not match:
        return 0
    hours, minutes, seconds = (int(g) if g else 0 for g in match.groups())
    return hours * 3600 + minutes * 60 + seconds


def _parse_search_item(item: dict) -> VideoResult | None:
    video_id = item.get("id", {}).get("videoId")
    if not video_id:
        return None
    snippet = item.get("snippet", {})
    thumbnails = snippet.get("thumbnails", {})
    thumb = thumbnails.get("medium") or thumbnails.get("default") or {}
    return VideoResult(
        video_id=video_id,
        title=snippet.get("title", ""),
        channel_id=snippet.get("channelId", ""),
        channel_title=snippet.get("channelTitle", ""),
        published_at=snippet.get("publishedAt", ""),
        description=snippet.get("description", ""),
        thumbnail_url=thumb.get("url", ""),
    )


def _merge_video_details(video: VideoResult, details_item: dict) -> None:
    content_details = details_item.get("contentDetails", {})
    statistics = details_item.get("statistics", {})
    status = details_item.get("status", {})

    duration_raw = content_details.get("duration")
    if duration_raw:
        video.duration_seconds = _parse_duration_iso8601(duration_raw)

    if "viewCount" in statistics:
        video.view_count = int(statistics["viewCount"])
    if "likeCount" in statistics:
        video.like_count = int(statistics["likeCount"])

    license_value = status.get("license")
    if license_value:
        video.license = license_value


def _raise_for_api_error(response: requests.Response) -> None:
    try:
        payload = response.json()
        reason = payload.get("error", {}).get("errors", [{}])[0].get("reason", "")
        message = payload.get("error", {}).get("message", response.text[:300])
    except (ValueError, KeyError, IndexError):
        reason, message = "", response.text[:300]

    if reason in ("quotaExceeded", "dailyLimitExceeded"):
        raise YouTubeQuotaError(
            "Quota YouTube Data API atteint pour aujourd'hui. Reessaie apres "
            "minuit heure du Pacifique, ou augmente le quota de ton projet "
            "Google Cloud (facturation)."
        )
    if reason == "keyInvalid" or response.status_code == 400:
        raise YouTubeApiError(f"Cle ou requete YouTube API invalide : {message}")
    raise YouTubeApiError(f"Erreur YouTube API ({response.status_code}) : {message}")


def _apply_client_side_filters(videos: list[VideoResult], filters: SearchFilters) -> list[VideoResult]:
    """search.list n'a ni parametre de duree exacte ni de vues minimum -- ces
    deux filtres sont appliques ici, apres coup, sur les VideoResult deja
    completes par videos.list. Une video sans la donnee necessaire est
    exclue plutot que supposee conforme."""
    out = []
    for v in videos:
        if filters.min_duration_s is not None or filters.max_duration_s is not None:
            if v.duration_seconds is None:
                continue
            if filters.min_duration_s is not None and v.duration_seconds < filters.min_duration_s:
                continue
            if filters.max_duration_s is not None and v.duration_seconds > filters.max_duration_s:
                continue
        if filters.min_view_count is not None:
            if v.view_count is None or v.view_count < filters.min_view_count:
                continue
        out.append(v)
    return out


def search_videos(
    query: str,
    max_results: int,
    filters: SearchFilters,
    quota: QuotaTracker,
    api_key: str | None = None,
    timeout: int = 15,
) -> list[VideoResult]:
    api_key = api_key or load_api_key()
    max_results = max(1, min(max_results, 50))

    if quota.would_exceed(SEARCH_COST_UNITS):
        raise YouTubeQuotaError(
            f"Cette recherche coute {SEARCH_COST_UNITS} unites de quota, il n'en "
            f"reste que {quota.remaining()} aujourd'hui. Reessaie apres minuit "
            "heure du Pacifique."
        )

    params = {
        "part": "snippet",
        "type": "video",
        "q": query,
        "maxResults": max_results,
        "order": filters.order,
        "key": api_key,
    }
    if filters.language:
        params["relevanceLanguage"] = filters.language
    if filters.video_duration_bucket:
        params["videoDuration"] = filters.video_duration_bucket
    if filters.published_after:
        params["publishedAfter"] = filters.published_after
    if filters.published_before:
        params["publishedBefore"] = filters.published_before
    if filters.channel_id:
        params["channelId"] = filters.channel_id
    if filters.category_id:
        params["videoCategoryId"] = filters.category_id
    if filters.creative_commons_only:
        params["videoLicense"] = "creativeCommon"

    try:
        response = requests.get(f"{_API_BASE}/search", params=params, timeout=timeout)
    except requests.exceptions.RequestException as e:
        raise YouTubeApiError(f"Impossible de contacter l'API YouTube (reseau) : {e}") from e

    quota.record(SEARCH_COST_UNITS)
    if response.status_code != 200:
        _raise_for_api_error(response)

    items = response.json().get("items", [])
    videos = [v for v in (_parse_search_item(item) for item in items) if v is not None]

    if videos:
        _fetch_video_details(videos, quota, api_key, timeout)

    return _apply_client_side_filters(videos, filters)


def _fetch_video_details(videos: list[VideoResult], quota: QuotaTracker, api_key: str, timeout: int) -> None:
    if quota.would_exceed(VIDEOS_LIST_COST_UNITS):
        logger.warning("Quota YouTube insuffisant pour recuperer vues/duree -- resultats incomplets.")
        return

    ids = ",".join(v.video_id for v in videos)
    params = {"part": "contentDetails,statistics,status", "id": ids, "key": api_key}
    try:
        response = requests.get(f"{_API_BASE}/videos", params=params, timeout=timeout)
    except requests.exceptions.RequestException as e:
        logger.warning(f"Details video (vues/duree) indisponibles (reseau) : {e}")
        return

    quota.record(VIDEOS_LIST_COST_UNITS)
    if response.status_code != 200:
        logger.warning("Details video (vues/duree) indisponibles (erreur API).")
        return

    by_id = {v.video_id: v for v in videos}
    for item in response.json().get("items", []):
        video = by_id.get(item.get("id"))
        if video is not None:
            _merge_video_details(video, item)
