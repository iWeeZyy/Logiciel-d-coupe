"""Recuperation de l'image REELLE utilisee par un article, jamais une image
generee (voir news_story/__init__.py et la spec produit -- aucune API/modele
generatif d'image n'est appele nulle part dans ce paquet).

Ordre de priorite explicitement demande : og:image -> twitter:image -> autre
image de metadonnees de page (image_src / lien favicon exclu) -> image
detectee dans le contenu -> image fournie par le flux RSS/Atom lui-meme (en
dernier repli, geree par l'appelant via Article.feed_image_url, pas ici).

Deliberement PAS un scraping complet du DOM/corps de page : seules les
balises <meta>/<link> de l'en-tete sont inspectees, jamais un telechargement
systematique de toutes les <img> de la page (contrainte explicite de la
spec : "ne doit pas telecharger toutes les images d'une page
indiscriminement").
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin

from core.logging_setup import get_logger

logger = get_logger()

DEFAULT_TIMEOUT_S = 10

# Priorite decroissante : plus l'index est bas, plus la source est fiable.
_META_IMAGE_PRIORITY = {
    "og:image": 0,
    "og:image:secure_url": 0,
    "og:image:url": 0,
    "twitter:image": 1,
    "twitter:image:src": 1,
}
_LINK_IMAGE_PRIORITY = {
    "image_src": 2,
}

_IMG_TAG_RE = re.compile(r'<img[^>]+src="([^"]+)"[^>]*>', re.IGNORECASE)
_IMG_WIDTH_RE = re.compile(r'width="(\d+)"', re.IGNORECASE)
_IMG_HEIGHT_RE = re.compile(r'height="(\d+)"', re.IGNORECASE)


@dataclass(frozen=True)
class ImageCandidate:
    """Une image candidate trouvee pour un article, avec sa provenance.

    `priority` : plus bas = plus fiable (og:image avant twitter:image avant
    une image de contenu) -- utilise pour trier, jamais pour filtrer : la
    spec demande de proposer PLUSIEURS candidates a l'utilisateur quand il y
    en a plusieurs, pas de n'en garder qu'une seule automatiquement."""

    url: str
    source: str  # "og:image", "twitter:image", "image_src", "content", "feed"
    priority: int


class _MetaImageParser(HTMLParser):
    """Extrait uniquement les balises <meta>/<link> pertinentes de l'en-tete
    HTML -- jamais le corps de la page (pas de scan d'<img> en dehors de
    `_extract_content_images`, applique separement et seulement en dernier
    repli, voir `extract_candidates`)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.found: list[tuple[str, str, int]] = []  # (url, source, priority)
        self._seen_head_end = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._seen_head_end:
            return
        attrs_d = {k.lower(): (v or "") for k, v in attrs}

        if tag == "meta":
            prop = (attrs_d.get("property") or attrs_d.get("name") or "").lower()
            content = attrs_d.get("content", "").strip()
            if content and prop in _META_IMAGE_PRIORITY:
                self.found.append((content, prop, _META_IMAGE_PRIORITY[prop]))
        elif tag == "link":
            rel = (attrs_d.get("rel") or "").lower()
            href = attrs_d.get("href", "").strip()
            if href and rel in _LINK_IMAGE_PRIORITY:
                self.found.append((href, rel, _LINK_IMAGE_PRIORITY[rel]))
        elif tag in ("body",):
            # Les metadonnees utiles vivent dans <head> ; inutile de
            # continuer a parser le reste de la page pour cette extraction.
            self._seen_head_end = True


def extract_candidates(html: str, page_url: str = "") -> list[ImageCandidate]:
    """Candidates tirees des metadonnees de page, triees par fiabilite puis
    par ordre d'apparition. Liste vide si aucune metadonnee image n'existe
    (page sans og:image/twitter:image ni image_src) -- jamais d'exception
    sur un HTML mal forme, HTMLParser est deja tolerant par nature."""
    parser = _MetaImageParser()
    try:
        parser.feed(html)
    except Exception as e:  # HTML pathologique : on degrade, on ne casse pas
        logger.warning(f"Analyse HTML des metadonnees image interrompue : {e}")

    seen_urls: set[str] = set()
    candidates: list[ImageCandidate] = []
    for raw_url, source, priority in parser.found:
        url = urljoin(page_url, raw_url) if page_url else raw_url
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        candidates.append(ImageCandidate(url=url, source=source, priority=priority))

    candidates.sort(key=lambda c: c.priority)
    return candidates


def _extract_content_images(html: str, page_url: str, limit: int = 5) -> list[ImageCandidate]:
    """Repli quand aucune metadonnee (og:image/twitter:image/image_src)
    n'existe : les premieres <img> du corps de page, avec une taille
    minimale grossiere pour ecarter icones/pixels de tracking. Volontairement
    limite (`limit`) -- ce n'est PAS un telechargement systematique de toutes
    les images de la page, seulement les premieres candidates plausibles."""
    candidates: list[ImageCandidate] = []
    seen_urls: set[str] = set()
    for match in _IMG_TAG_RE.finditer(html):
        if len(candidates) >= limit:
            break
        tag = match.group(0)
        raw_url = match.group(1).strip()
        if not raw_url or raw_url.startswith("data:"):
            continue

        width_m = _IMG_WIDTH_RE.search(tag)
        height_m = _IMG_HEIGHT_RE.search(tag)
        if width_m and int(width_m.group(1)) < 150:
            continue
        if height_m and int(height_m.group(1)) < 150:
            continue

        url = urljoin(page_url, raw_url) if page_url else raw_url
        if url in seen_urls:
            continue
        seen_urls.add(url)
        candidates.append(ImageCandidate(url=url, source="content", priority=3))

    return candidates


def fetch_article_html(url: str, timeout_s: float = DEFAULT_TIMEOUT_S) -> str:
    """Le HTML brut de la page d'article. Chaine vide si injoignable, en
    timeout, ou en erreur HTTP -- jamais d'exception (meme convention que
    gaming_news/feed_fetcher.fetch_source)."""
    import requests

    try:
        response = requests.get(
            url, timeout=timeout_s,
            headers={"User-Agent": "ClipFarming/1.0 (+lecteur d'actualites gaming)"},
        )
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        logger.warning(f"Page d'article injoignable ({url}) : {e}")
        return ""

    response.encoding = response.encoding or "utf-8"
    return response.text


def candidates_for_article(article_url: str, feed_image_url: str = "",
                           timeout_s: float = DEFAULT_TIMEOUT_S) -> list[ImageCandidate]:
    """Toutes les candidates connues pour un article, dans l'ordre de
    priorite complet demande par la spec : og:image/twitter:image/image_src
    -> image de contenu -> image du flux RSS en tout dernier repli (jamais
    prioritaire, une image de flux est souvent une vignette generique du
    site plutot que l'image reelle de l'article)."""
    html = fetch_article_html(article_url, timeout_s=timeout_s)
    candidates = extract_candidates(html, page_url=article_url) if html else []

    if not candidates and html:
        candidates = _extract_content_images(html, page_url=article_url)

    if feed_image_url and feed_image_url not in {c.url for c in candidates}:
        candidates.append(ImageCandidate(url=feed_image_url, source="feed", priority=4))

    return candidates
