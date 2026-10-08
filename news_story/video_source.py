"""La video qui accompagne un article, quand il y en a une.

Demande explicite de l'utilisateur : quand l'article est illustre par une
video (bande-annonce, teaser, extrait), le post doit etre cette video, pour
que les gens comprennent de quoi parle la news -- une photo seule ne suffit
pas toujours.

On ne fait ici que TROUVER l'adresse de la video ; le telechargement passe
par youtube/downloader.download_video (yt-dlp, deja utilise par l'appli, qui
lit aussi Dailymotion). Trois cas reconnus, dans cet ordre :

- l'article EST une video YouTube (fil Bandes-annonces) ;
- page AlloCine : son lecteur declare la video dans un attribut data-model
  (JSON echappe en HTML) avec un identifiant Dailymotion -- yt-dlp ne sait
  pas lire les articles AlloCine directement (verifie le 2026-10-08) ;
- lecteur integre classique : iframe YouTube ou Dailymotion dans la page.

Rien de trouve -> "" : le post reste une image, jamais une erreur.
"""
from __future__ import annotations

import html as html_lib
import json
import re

from news_story.image_fetcher import youtube_video_id

_DATA_MODEL_RE = re.compile(r'data-model="([^"]+)"')
_YOUTUBE_EMBED_RE = re.compile(
    r"(?:youtube\.com|youtube-nocookie\.com)/embed/([A-Za-z0-9_-]{11})")
_DAILYMOTION_EMBED_RE = re.compile(
    r"(?:dailymotion\.com/(?:embed/)?video/|dai\.ly/|dailymotion\.com/player[^\"'\s]*?[?&]video=)"
    r"([a-z0-9]{5,10})\b", re.IGNORECASE)


def dailymotion_url(video_id: str) -> str:
    return f"https://www.dailymotion.com/video/{video_id}"


def youtube_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def _allocine_dailymotion_id(page_html: str) -> str:
    for match in _DATA_MODEL_RE.finditer(page_html):
        raw = html_lib.unescape(match.group(1))
        if "idDailymotion" not in raw:
            continue
        try:
            model = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for video in model.get("videos") or []:
            if isinstance(video, dict) and video.get("idDailymotion"):
                return str(video["idDailymotion"])
    return ""


def find_video_url(article_url: str, page_html: str = "") -> str:
    """Adresse de la video de l'article, a passer a yt-dlp ; "" si aucune.
    Fonction pure : la page est fournie deja telechargee."""
    if youtube_video_id(article_url):
        return article_url
    if not page_html:
        return ""
    dm_id = _allocine_dailymotion_id(page_html)
    if dm_id:
        return dailymotion_url(dm_id)
    match = _YOUTUBE_EMBED_RE.search(page_html)
    if match:
        return youtube_url(match.group(1))
    match = _DAILYMOTION_EMBED_RE.search(page_html)
    if match:
        return dailymotion_url(match.group(1))
    return ""


def fetch_video_url(article_url: str, timeout_s: float = 10) -> str:
    """Comme find_video_url, en telechargeant la page si besoin. Jamais
    d'exception : une page injoignable veut dire « pas de video »."""
    if youtube_video_id(article_url):
        return article_url
    from news_story.image_fetcher import fetch_article_html

    try:
        page_html = fetch_article_html(article_url, timeout_s=timeout_s)
    except Exception:  # noqa: BLE001 -- la video est un bonus, jamais bloquante
        return ""
    return find_video_url(article_url, page_html or "")
