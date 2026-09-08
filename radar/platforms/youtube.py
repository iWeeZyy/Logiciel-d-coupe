"""Adaptateur YouTube Data API v3 (officielle, conforme aux conditions).

LE POINT CENTRAL DE CE MODULE EST LE QUOTA, et il dicte tout le reste.

search.list coute 100 unites par appel, quel que soit le nombre de resultats.
Le quota par defaut d'un projet est de 10 000 unites par jour. Surveiller
27 createurs avec search.list couterait 2 700 unites par scan : trois scans
quotidiens, et plus rien. Inutilisable pour un Radar.

Le chemin retenu, celui que Google recommande explicitement pour lister les
videos d'une chaine :

    channels.list       1 unite   -> playlist "uploads" de la chaine
                                     MISE EN CACHE : elle ne change jamais
    playlistItems.list  1 unite   -> jusqu'a 50 videos recentes
    videos.list         1 unite   -> duree et statistiques de 50 videos

Soit 2 unites par createur et par scan une fois la playlist connue, contre 100.
27 createurs reviennent a environ 60 unites : plus de 150 scans par jour
possibles au lieu de trois.

search.list n'est utilise QUE pour resoudre une chaine a partir d'un nom
approximatif -- une fois, a l'ajout, jamais pendant un scan. Une URL ou un
@handle sont resolus par channels.list, donc pour 1 unite.

DETECTION DES SHORTS : l'API n'expose aucun champ le disant. Le ratio vertical
n'est pas disponible, et verifier la redirection youtube.com/shorts/<id> serait
du scraping -- exclu par les contraintes. La detection repose donc sur la
DUREE, et l'interface doit la presenter comme une heuristique, pas comme une
certitude.

Ce module ne telecharge aucune video et ne presente jamais un contenu comme
libre de droits : YouTube n'expose que le champ `license`, qui distingue
"youtube" de "creativeCommon" -- c'est une information, pas une autorisation.
"""
from __future__ import annotations

from datetime import datetime, timezone

import requests

from core.logging_setup import get_logger
from radar.models import KIND_SHORT, KIND_VOD, Creator, Opportunity
from radar.platforms.base import PlatformAdapter, PlatformStatus
from utils.errors import YouTubeApiError, YouTubeConfigError, YouTubeQuotaError
from youtube.quota import QuotaTracker
from youtube.search import _API_BASE, load_api_key

logger = get_logger()

PLATFORM = "youtube"

# Couts officiels, en unites de quota.
COST_CHANNELS_LIST = 1
COST_PLAYLIST_ITEMS = 1
COST_VIDEOS_LIST = 1
COST_SEARCH = 100

# Duree maximale d'un Short. YouTube est passe de 60 s a 3 minutes en 2024 :
# on retient la borne haute, quitte a inclure quelques videos courtes qui n'en
# sont pas -- l'inverse ferait manquer des Shorts recents.
SHORT_MAX_DURATION_S = 180


def _parse_duration_iso8601(value: str) -> int | None:
    """PT1M30S -> 90. None si la duree est absente ou illisible (les directs en
    cours n'en ont pas)."""
    if not value or not value.startswith("PT"):
        return None
    total, number = 0, ""
    for char in value[2:]:
        if char.isdigit():
            number += char
        elif char in "HMS" and number:
            total += int(number) * {"H": 3600, "M": 60, "S": 1}[char]
            number = ""
        else:
            number = ""
    return total or None


def _thumbnail(snippet: dict) -> str:
    thumbnails = snippet.get("thumbnails") or {}
    for size in ("maxres", "standard", "high", "medium", "default"):
        if size in thumbnails:
            return thumbnails[size].get("url", "")
    return ""


class YouTubeAdapter(PlatformAdapter):
    platform = PLATFORM

    def __init__(self, api_key: str | None = None, quota: QuotaTracker | None = None,
                 daily_quota_limit: int = 10000, timeout: int = 15):
        self._api_key = api_key
        # Meme compteur que la recherche YouTube existante (youtube/quota.py) :
        # les deux fonctionnalites puisent dans le MEME quota Google, un
        # deuxieme compteur les ferait diverger et laisserait croire a de la
        # marge qui n'existe pas.
        self.quota = quota or QuotaTracker(daily_limit=daily_quota_limit)
        self.timeout = timeout

    # ------------------------------------------------------------- etat
    def api_key(self) -> str:
        if self._api_key:
            return self._api_key
        self._api_key = load_api_key()
        return self._api_key

    def status(self) -> PlatformStatus:
        """Utilisable ou non, sans jamais lever : l'interface a besoin de le
        savoir pour desactiver l'onglet proprement."""
        try:
            self.api_key()
        except YouTubeConfigError as error:
            return PlatformStatus(
                platform=PLATFORM, available=False, reason=str(error),
                setup_hint="Clé YouTube Data API v3 requise (gratuite, console Google Cloud).",
            )
        return PlatformStatus(platform=PLATFORM, available=True)

    # ---------------------------------------------------------- requetes
    def _get(self, endpoint: str, params: dict, cost: int) -> dict:
        """Appel API avec comptabilisation du quota et erreurs typees."""
        if self.quota.would_exceed(cost):
            raise YouTubeQuotaError(
                f"Quota YouTube insuffisant pour cette requête ({cost} unité(s) nécessaires, "
                f"{self.quota.remaining()} restantes aujourd'hui). Le compteur se remet à zéro "
                "à minuit heure du Pacifique."
            )
        params = {**params, "key": self.api_key()}
        try:
            response = requests.get(f"{_API_BASE}/{endpoint}", params=params, timeout=self.timeout)
        except requests.RequestException as error:
            raise YouTubeApiError(
                f"Impossible de joindre l'API YouTube : {error}. "
                "Le Radar a besoin d'une connexion internet pour scanner ; "
                "les données déjà collectées restent consultables hors ligne."
            ) from error
        self.quota.record(cost)

        if response.status_code == 403:
            body = response.text.lower()
            if "quota" in body:
                raise YouTubeQuotaError(
                    "Quota YouTube épuisé pour aujourd'hui. Il se réinitialise à minuit "
                    "heure du Pacifique. Les résultats déjà enregistrés restent disponibles."
                )
            raise YouTubeApiError(f"Accès refusé par l'API YouTube : {response.text[:200]}")
        if response.status_code == 404:
            raise YouTubeApiError("Ressource introuvable (chaîne supprimée ou identifiant erroné).")
        if not response.ok:
            raise YouTubeApiError(
                f"L'API YouTube a répondu {response.status_code} : {response.text[:200]}"
            )
        return response.json()

    # -------------------------------------------------------- resolution
    @staticmethod
    def parse_query(query: str) -> tuple[str, str]:
        """(type, valeur) a partir d'un nom, d'un @handle ou d'une URL.

        Distinguer ces trois formes AVANT d'appeler l'API est ce qui evite de
        depenser 100 unites de search.list pour une URL qui contient deja
        l'identifiant.
        """
        text = (query or "").strip()
        if not text:
            return "empty", ""
        lowered = text.lower()
        if "youtube.com" in lowered or "youtu.be" in lowered:
            path = text.split("youtube.com", 1)[-1].split("youtu.be", 1)[-1]
            path = path.split("?", 1)[0].rstrip("/")
            segments = [s for s in path.split("/") if s]
            for index, segment in enumerate(segments):
                if segment == "channel" and index + 1 < len(segments):
                    return "channel_id", segments[index + 1]
                if segment.startswith("@"):
                    return "handle", segment[1:]
                if segment in ("c", "user") and index + 1 < len(segments):
                    return "name", segments[index + 1]
            return "name", segments[-1] if segments else text
        if text.startswith("@"):
            return "handle", text[1:]
        if text.startswith("UC") and len(text) == 24:
            return "channel_id", text
        return "name", text

    def resolve_creator(self, query: str) -> Creator | None:
        kind, value = self.parse_query(query)
        if kind == "empty":
            return None

        if kind == "channel_id":
            return self._channel_by(id=value)
        if kind == "handle":
            return self._channel_by(forHandle=f"@{value}")

        # Nom approximatif : c'est le SEUL cas qui justifie search.list, et il
        # n'arrive qu'a l'ajout d'un createur, jamais pendant un scan.
        data = self._get("search", {
            "part": "snippet", "type": "channel", "q": value, "maxResults": 1,
        }, COST_SEARCH)
        items = data.get("items") or []
        if not items:
            return None
        channel_id = (items[0].get("snippet") or {}).get("channelId") or \
                     (items[0].get("id") or {}).get("channelId")
        return self._channel_by(id=channel_id) if channel_id else None

    def _channel_by(self, **selector) -> Creator | None:
        data = self._get("channels", {
            "part": "snippet,statistics,contentDetails", "maxResults": 1, **selector,
        }, COST_CHANNELS_LIST)
        items = data.get("items") or []
        if not items:
            return None
        item = items[0]
        snippet = item.get("snippet") or {}
        statistics = item.get("statistics") or {}
        uploads = ((item.get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads", "")
        handle = (snippet.get("customUrl") or "").lstrip("@")

        subscribers = statistics.get("subscriberCount")
        return Creator(
            platform=PLATFORM,
            platform_id=item.get("id", ""),
            username=handle,
            display_name=snippet.get("title", ""),
            url=f"https://www.youtube.com/channel/{item.get('id', '')}",
            avatar_url=_thumbnail(snippet),
            # hiddenSubscriberCount : une chaine peut masquer ses abonnes. On
            # laisse None plutot que d'afficher 0, qui serait faux.
            follower_count=int(subscribers) if subscribers is not None else None,
            extra={"uploads_playlist": uploads} if uploads else {},
        )

    # --------------------------------------------------------------- scan
    def _uploads_playlist(self, creator: Creator) -> str | None:
        """Playlist des uploads, mise en cache sur le createur.

        Elle ne change jamais pour une chaine donnee : la relire a chaque scan
        serait une unite de quota gaspillee par createur et par scan.
        """
        cached = (creator.extra or {}).get("uploads_playlist")
        if cached:
            return cached
        resolved = self._channel_by(id=creator.platform_id)
        if resolved is None:
            return None
        playlist = (resolved.extra or {}).get("uploads_playlist")
        if playlist:
            creator.extra = {**(creator.extra or {}), "uploads_playlist": playlist}
        return playlist

    def scan(self, creator: Creator, since_iso: str, max_results: int = 50) -> list[Opportunity]:
        """Contenus publies depuis `since_iso` par ce createur.

        Le tri de la playlist d'uploads est chronologique inverse : on s'arrete
        des qu'on passe sous la date demandee, plutot que de paginer inutilement.
        """
        playlist = self._uploads_playlist(creator)
        if not playlist:
            raise YouTubeApiError(
                f"Impossible de trouver les vidéos de « {creator.label} » "
                "(chaîne supprimée, ou sans vidéo publique)."
            )

        data = self._get("playlistItems", {
            "part": "snippet,contentDetails", "playlistId": playlist,
            "maxResults": min(50, max(1, max_results)),
        }, COST_PLAYLIST_ITEMS)

        recent_ids, published_by_id = [], {}
        for item in data.get("items") or []:
            details = item.get("contentDetails") or {}
            video_id = details.get("videoId")
            published = details.get("videoPublishedAt") or ""
            if not video_id:
                continue
            if published and published < since_iso:
                continue  # playlist triee : les suivantes sont plus anciennes
            recent_ids.append(video_id)
            published_by_id[video_id] = published

        if not recent_ids:
            return []
        return self._videos(recent_ids, creator, published_by_id)

    def _videos(self, video_ids: list[str], creator: Creator,
                published_by_id: dict) -> list[Opportunity]:
        """Duree et statistiques, groupees : 1 unite pour 50 videos."""
        data = self._get("videos", {
            "part": "snippet,contentDetails,statistics,status,liveStreamingDetails",
            "id": ",".join(video_ids[:50]),
        }, COST_VIDEOS_LIST)

        out = []
        for item in data.get("items") or []:
            snippet = item.get("snippet") or {}
            statistics = item.get("statistics") or {}
            duration = _parse_duration_iso8601((item.get("contentDetails") or {}).get("duration", ""))
            video_id = item.get("id", "")

            def count(name):
                value = statistics.get(name)
                # Un compteur masque par le createur doit rester inconnu, pas
                # devenir zero : le score en tiendrait compte comme d'un fait.
                return int(value) if value is not None else None

            is_short = duration is not None and duration <= SHORT_MAX_DURATION_S
            out.append(Opportunity(
                platform=PLATFORM,
                content_id=video_id,
                kind=KIND_SHORT if is_short else KIND_VOD,
                creator_key=creator.key,
                title=snippet.get("title", ""),
                url=f"https://www.youtube.com/watch?v={video_id}",
                thumbnail_url=_thumbnail(snippet),
                published_at=published_by_id.get(video_id) or snippet.get("publishedAt", ""),
                duration_s=duration,
                category=snippet.get("categoryId", ""),
                view_count=count("viewCount"),
                like_count=count("likeCount"),
                comment_count=count("commentCount"),
                is_live=(snippet.get("liveBroadcastContent") == "live"),
                extra={
                    # Heuristique explicite : l'API ne dit pas si c'est un Short.
                    "short_detection": "durée" if is_short else "",
                    # Information, pas autorisation : une licence Creative
                    # Commons n'est pas un blanc-seing, et "youtube" ne signifie
                    # jamais libre de droits.
                    "license": (item.get("status") or {}).get("license", ""),
                },
            ))
        return out
