"""Recuperation et analyse des flux RSS/Atom des sources d'actualites.

Chaque source est independante : un flux en echec (reseau, XML invalide, URL
perimee) ne doit jamais empecher les autres de s'afficher -- d'ou une liste
vide plutot qu'une exception qui remonterait et casserait tout le scan.

Le parsing gere RSS 2.0 ET Atom avec le module standard xml.etree.ElementTree
(aucune dependance supplementaire) : les deux formats different par le nom des
balises mais partagent la meme structure (une liste d'entrees, chacune avec un
titre, un lien, une date). L'image qu'un flux fournit LUI-MEME (media:content,
media:thumbnail, enclosure, ou une balise <img> dans le resume HTML) est
cherchee en repli -- jamais garantie, ni prioritaire sur l'image reellement
extraite de la page de l'article (voir news_story/image_fetcher.py).

fetch_source() (reseau) et parse_feed() (analyse pure) sont deliberement
separes : les tests exercent parse_feed() avec des flux ecrits a la main, sans
jamais toucher au reseau.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from html import unescape

from core.logging_setup import get_logger
from gaming_news.models import Article, NewsSource

logger = get_logger()

_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "media": "http://search.yahoo.com/mrss/",
}

DEFAULT_TIMEOUT_S = 10
DEFAULT_MAX_ARTICLES = 20

_IMG_SRC_RE = re.compile(r'<img[^>]+src="([^"]+)"', re.IGNORECASE)


def fetch_all_sources(sources, timeout_s: float = DEFAULT_TIMEOUT_S,
                      max_articles: int = DEFAULT_MAX_ARTICLES) -> list[Article]:
    """Agrege les articles de TOUTES les sources donnees, triees par date de
    publication decroissante quand elle est analysable -- une source
    injoignable ou une date illisible ne bloquent jamais les autres (meme
    principe de tolerance que fetch_source)."""
    articles: list[Article] = []
    for source in sources:
        articles.extend(fetch_source(source, timeout_s=timeout_s, max_articles=max_articles))
    articles.sort(key=lambda a: _sortable_date(a.published_at), reverse=True)
    return articles


def _sortable_date(text: str):
    """Une date de tri toujours comparable (meme fuseau, jamais naive) --
    une date absente ou dans un format non reconnu (RSS/Atom melangent
    RFC 822 et ISO 8601) tombe en fin de liste plutot que de faire planter
    le tri."""
    from datetime import datetime, timezone

    for parser in (_parse_rfc822_date, _parse_iso8601_date):
        value = parser(text) if text else None
        if value is not None:
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return datetime.min.replace(tzinfo=timezone.utc)


def _parse_rfc822_date(text: str):
    from email.utils import parsedate_to_datetime

    try:
        return parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None


def _parse_iso8601_date(text: str):
    from datetime import datetime

    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def fetch_source(source: NewsSource, timeout_s: float = DEFAULT_TIMEOUT_S,
                 max_articles: int = DEFAULT_MAX_ARTICLES) -> list[Article]:
    """Articles d'UNE source. Liste vide si le flux est injoignable, en
    timeout, ou renvoie une erreur HTTP -- jamais d'exception (voir docstring
    du module)."""
    import requests

    try:
        response = requests.get(
            source.feed_url, timeout=timeout_s,
            headers={"User-Agent": "ClipFarming/1.0 (+lecteur d'actualites gaming)"},
        )
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        logger.warning(f"Flux « {source.label} » injoignable : {e}")
        return []

    return parse_feed(response.content, source, max_articles=max_articles)


def parse_feed(raw_xml: bytes | str, source: NewsSource,
               max_articles: int = DEFAULT_MAX_ARTICLES) -> list[Article]:
    """Analyse pure d'un flux deja telecharge. Liste vide si le contenu n'est
    pas du XML valide (page d'erreur HTML renvoyee a la place du flux, flux
    tronque...) -- jamais d'exception."""
    try:
        root = ET.fromstring(raw_xml)
    except ET.ParseError as e:
        logger.warning(f"Flux « {source.label} » illisible (XML invalide) : {e}")
        return []

    entries = root.findall(".//item")  # RSS 2.0
    is_atom = False
    if not entries:
        entries = root.findall(".//atom:entry", _NS)  # Atom
        is_atom = bool(entries)

    articles: list[Article] = []
    for entry in entries[:max_articles]:
        article = _entry_to_article(entry, source, is_atom)
        if article is not None:
            articles.append(article)
    return articles


def _text(entry, tag: str) -> str:
    node = entry.find(tag, _NS) if ":" in tag else entry.find(tag)
    return unescape((node.text or "").strip()) if node is not None and node.text else ""


def _entry_to_article(entry, source: NewsSource, is_atom: bool) -> Article | None:
    # Une fois DANS un <entry> Atom, TOUTES ses balises filles portent le
    # namespace Atom (y compris <title>) : "title" sans prefixe ne matcherait
    # rien. RSS, lui, n'a pas de namespace par defaut sur ses balises.
    if is_atom:
        title = _text(entry, "atom:title")
        link_node = entry.find("atom:link", _NS)
        url = link_node.get("href", "") if link_node is not None else ""
        published = _text(entry, "atom:published") or _text(entry, "atom:updated")
        summary = _text(entry, "atom:summary") or _text(entry, "atom:content")
    else:
        title = _text(entry, "title")
        url = _text(entry, "link")
        published = _text(entry, "pubDate")
        summary = _text(entry, "description")

    title, url = title.strip(), url.strip()
    if not title or not url:
        return None  # un titre ou un lien absent -- rien d'exploitable a afficher

    return Article(
        source_key=source.key, source_label=source.label,
        title=title, url=url, published_at=published, summary=summary,
        feed_image_url=_extract_feed_image(entry, summary),
    )


def _extract_feed_image(entry, summary: str) -> str:
    media_content = entry.find("media:content", _NS)
    if media_content is not None and media_content.get("url"):
        return media_content.get("url")
    media_thumb = entry.find("media:thumbnail", _NS)
    if media_thumb is not None and media_thumb.get("url"):
        return media_thumb.get("url")
    enclosure = entry.find("enclosure")
    if enclosure is not None and (enclosure.get("type") or "").startswith("image") and enclosure.get("url"):
        return enclosure.get("url")
    match = _IMG_SRC_RE.search(summary)
    return match.group(1) if match else ""
