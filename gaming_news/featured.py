"""Articles « a la une » d'un site, et Top bandes-annonces AlloCine.

Demande explicite de l'utilisateur : dans le fil Cine et series, les news A LA
UNE des sites doivent sortir en premier ; dans le fil Bandes-annonces, le Top
bandes-annonces d'AlloCine (https://www.allocine.fr/video/bandes-annonces/),
dans l'ordre du top.

« A la une » = l'ordre dans lequel la page d'accueil du site (ou de sa
rubrique cinema) presente ses articles. On ne devine rien : on lit les liens
de cette page dans l'ordre, et on classe les articles du flux RSS qui y
figurent. Un lien de la page absent du flux (rubrique, page de film, contenu
sponsorise) est ignore.

AlloCine masque une partie de ses liens (section « A la Une » notamment) dans
un attribut class="ACr…" : l'adresse y est en base64, avec la chaine « ACr »
inseree pour la brouiller. decode_allocine_link() la retrouve.

Parseurs purs, reseau a part (fetch_*), comme le reste de gaming_news/.
"""
from __future__ import annotations

import base64
import binascii
import re
from dataclasses import replace
from html import unescape
from urllib.parse import urljoin, urlparse

from core.logging_setup import get_logger
from gaming_news.models import Article, NewsSource

logger = get_logger()

BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
FEATURED_TOP_N = 6           # articles marques « a la une » par site

_LINK_RE = re.compile(
    r'href="([^"#]+)"|class="ACr([A-Za-z0-9+/=]+?(?:ACr[A-Za-z0-9+/=]+?)*)[\s"]')


def decode_allocine_link(obfuscated: str) -> str:
    raw = obfuscated.replace("ACr", "")
    raw += "=" * (-len(raw) % 4)
    try:
        return base64.b64decode(raw).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return ""


def page_links(page_html: str, base_url: str) -> list[str]:
    """Liens de la page, dans l'ordre, sans doublon, adresses absolues."""
    out: list[str] = []
    seen: set[str] = set()
    for match in _LINK_RE.finditer(page_html or ""):
        if match.group(1):
            url = urljoin(base_url, unescape(match.group(1)))
        else:
            url = decode_allocine_link(match.group(2))
            if not url:
                continue
            url = urljoin(base_url, url)
        key = url_key(url)
        if key not in seen:
            seen.add(key)
            out.append(url)
    return out


def url_key(url: str) -> str:
    """Cle de comparaison d'adresses : hote sans www, chemin sans « / » final,
    sans parametres de suivi ni ancre."""
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower().removeprefix("www.")
    return f"{host}{parsed.path.rstrip('/')}"


def rank_featured(articles: list[Article], page_html: str, base_url: str,
                  top_n: int = FEATURED_TOP_N) -> list[Article]:
    """Les articles du flux, ceux presents sur la page « a la une » recevant
    leur rang (0 = le premier presente), dans la limite de `top_n`."""
    by_key = {url_key(a.url): i for i, a in enumerate(articles)}
    ranked = list(articles)
    rank = 0
    for url in page_links(page_html, base_url):
        index = by_key.get(url_key(url))
        if index is None or ranked[index].rank is not None:
            continue
        ranked[index] = replace(ranked[index], rank=rank)
        rank += 1
        if rank >= top_n:
            break
    return ranked


def fetch_page(url: str, timeout_s: float) -> str:
    import requests

    try:
        response = requests.get(url, timeout=timeout_s, headers={"User-Agent": BROWSER_UA})
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        logger.warning(f"Page « a la une » injoignable ({url}) : {e}")
        return ""
    from news_story.image_fetcher import decode_html

    return decode_html(response.content, response.headers.get("Content-Type", ""))


# ------------------------------------------------- Top bandes-annonces AlloCine

_TRAILER_LINK_RE = re.compile(
    r'<a[^>]+href="(/video/player_gen_cmedia=\d+&amp;cfilm=\d+\.html)"[^>]*>(.*?)</a>', re.S)
_THUMB_RE = re.compile(r'data-src="(https://[^"]+acsta\.net[^"]+)"')
_CARD_THUMB_RE = re.compile(r'(?:data-src|src)="(https://[^"]+acsta\.net[^"]+)"')
_TAG_RE = re.compile(r"<[^>]+>")
_CARD = '<div class="card video-card-trailer'
TOP_TRAILERS_N = 5            # le Top AlloCine bouge peu : seulement ses 5 premieres
LATEST_TRAILERS_URL = "https://www.allocine.fr/video/bandes-annonces/plus-recentes/"


def _trailer_cards(page: str):
    """(lien, titre, vignette) de chaque carte, dans l'ordre de la page. La
    vignette est cherchee DANS la carte du lien (elle suit le lien sur la
    page actuelle) ; ancienne mise en page : la derniere avant le lien."""
    if _CARD in page:
        for card in page.split(_CARD)[1:]:
            match = _TRAILER_LINK_RE.search(card)
            if match:
                thumbs = [u for u in _CARD_THUMB_RE.findall(card) if "/empty/" not in u]
                yield match.group(1), match.group(2), thumbs[0] if thumbs else ""
        return
    for match in _TRAILER_LINK_RE.finditer(page):
        before = page[max(0, match.start() - 1500):match.start()]
        thumbs = _THUMB_RE.findall(before)
        yield match.group(1), match.group(2), thumbs[-1] if thumbs else ""


def parse_allocine_trailers(page_html: str, source: NewsSource,
                            max_articles: int = 30, ranked: bool = True) -> list[Article]:
    """Bandes-annonces de la page, dans son ordre (rang 0 = numero 1 du top ;
    `ranked=False` pour la page des plus recentes : pas de rang)."""
    start = page_html.find("Top Trailers")
    page = page_html[start:] if start >= 0 and _CARD not in page_html else page_html
    articles: list[Article] = []
    seen: set[str] = set()
    for raw_href, raw_title, thumb in _trailer_cards(page):
        href = unescape(raw_href)
        title = " ".join(unescape(_TAG_RE.sub(" ", raw_title)).split())
        if href in seen or not title:
            continue
        seen.add(href)
        articles.append(Article(
            source_key=source.key, source_label=source.label, title=title,
            url=urljoin("https://www.allocine.fr/", href), feed_image_url=thumb,
            rank=len(articles) if ranked else None))
        if len(articles) >= max_articles:
            break
    return articles


def fetch_allocine_trailers(source: NewsSource, timeout_s: float,
                            max_articles: int) -> list[Article]:
    page = fetch_page(source.feed_url, timeout_s)
    return parse_allocine_trailers(page, source, min(max_articles, TOP_TRAILERS_N)) if page else []


_DATE_PUBLISHED_RE = re.compile(r'"(?:uploadDate|datePublished)"\s*:\s*"([^"]+)"')


def player_details(page_html: str) -> tuple[str, str]:
    """(date de mise en ligne ISO, synopsis du film) d'une page de lecture
    AlloCine ; "" pour ce qui manque."""
    from news_story.release_date import parse_synopsis

    match = _DATE_PUBLISHED_RE.search(page_html or "")
    return (match.group(1) if match else ""), parse_synopsis(page_html)


def fetch_allocine_latest_trailers(source: NewsSource, timeout_s: float,
                                   max_articles: int) -> list[Article]:
    """Les bandes-annonces les plus recentes d'AlloCine. La liste n'a pas de
    date : chaque page de lecture donne la sienne (et le synopsis du film),
    lues en parallele pour trier ces videos avec celles des chaines YouTube."""
    from concurrent.futures import ThreadPoolExecutor

    page = fetch_page(source.feed_url or LATEST_TRAILERS_URL, timeout_s)
    articles = parse_allocine_trailers(page, source, max_articles, ranked=False) if page else []
    if not articles:
        return []
    with ThreadPoolExecutor(max_workers=8) as pool:
        details = list(pool.map(lambda a: player_details(fetch_page(a.url, timeout_s)), articles))
    return [replace(a, published_at=day, summary=synopsis)
            for a, (day, synopsis) in zip(articles, details)]
