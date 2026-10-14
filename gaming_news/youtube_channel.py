"""Dernieres videos d'une chaine YouTube, quand son flux RSS ne repond plus.

Constat (2026-10-14) : les flux https://www.youtube.com/feeds/videos.xml
des chaines suivies renvoient tous 404, alors que les pages des chaines
repondent -- le fil Bandes-annonces ne montrait plus que le Top AlloCine,
« toujours les memes ». yt-dlp (deja utilise par le logiciel pour les
telechargements) liste les videos de l'onglet « Videos » de la chaine, sans
les telecharger ; la date est approximative (« il y a 3 heures »), suffisante
pour trier le fil.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from core.logging_setup import get_logger
from gaming_news.models import Article, NewsSource

logger = get_logger()

_FIRST_PAGE = 30
_CHANNEL_RE = re.compile(r"channel_id=(UC[\w-]{22})")


def channel_id(feed_url: str) -> str:
    match = _CHANNEL_RE.search(feed_url or "")
    return match.group(1) if match else ""


def entries_to_articles(entries, source: NewsSource, max_articles: int,
                        title_re=None) -> list[Article]:
    """Entrees « a plat » de yt-dlp -> articles (fonction pure)."""
    articles: list[Article] = []
    for entry in entries or []:
        if len(articles) >= max_articles:
            break
        video_id, title = (entry or {}).get("id"), " ".join(((entry or {}).get("title") or "").split())
        if not video_id or not title or (title_re is not None and not title_re.search(title)):
            continue
        stamp = entry.get("timestamp")
        published = (datetime.fromtimestamp(stamp, tz=timezone.utc).isoformat()
                     if isinstance(stamp, (int, float)) else "")
        articles.append(Article(
            source_key=source.key, source_label=source.label, title=title,
            url=f"https://www.youtube.com/watch?v={video_id}", published_at=published,
            summary=" ".join((entry.get("description") or "").split()),
            feed_image_url=f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"))
    return articles


def fetch(source: NewsSource, max_articles: int, title_re=None, timeout_s: float = 15) -> list[Article]:
    """Liste vide en cas d'echec -- jamais d'exception (comme fetch_source)."""
    channel = channel_id(source.feed_url)
    if not channel:
        return []
    try:
        import yt_dlp

        options = {"extract_flat": True, "quiet": True, "no_warnings": True,
                   "skip_download": True, "socket_timeout": timeout_s,
                   # La 1re page de la chaine (30 videos) seulement : la
                   # suivante passe par l'API interne, qui refuse (403) les
                   # appels en rafale. Plus large que max_articles quand un
                   # filtre de titre ecarte extraits et Shorts.
                   "playlistend": _FIRST_PAGE if title_re is not None else min(max_articles, _FIRST_PAGE),
                   "extractor_args": {"youtubetab": {"approximate_date": [""]}}}
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/channel/{channel}/videos",
                                    download=False)
    except Exception as e:  # noqa: BLE001 -- une chaine en echec ne bloque pas les autres
        logger.warning(f"Chaîne YouTube « {source.label} » illisible : {e}")
        return []
    return entries_to_articles((info or {}).get("entries"), source, max_articles, title_re)
