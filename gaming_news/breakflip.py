"""Source Breakflip (https://www.breakflip.com/) -- un site SANS flux RSS.

Tous ses flux WordPress (/feed, /?feed=rss2, flux de categorie...) repondent
une erreur « Non disponible » (verifie le 2026-09-28) : le lecteur RSS
generique (feed_fetcher.parse_feed) ne peut rien en tirer. On lit donc
directement :

1. la page « Actualites » (`feed_url` de la source), dont chaque carte porte
   deja le titre, le chapeau (resume), l'image et le lien de l'article --
   une seule requete pour 20 articles ;
2. les dates EXACTES dans le sitemap Yoast du site : la page n'affiche que
   des ages relatifs (« Il y a 1 mois »), inutilisables pour trier face aux
   autres sources. L'index des sitemaps donne, pour chaque sous-sitemap
   `actu-sitemap*.xml`, sa date de derniere modification : le plus recent
   contient les derniers articles, avec leur date (<lastmod>).

Si l'etape 2 echoue, les articles restent affiches, simplement sans date
(tries en fin de liste) -- jamais une date inventee ou approximee.

Parseurs purs (parse_hub, latest_actu_sitemap, parse_sitemap_dates) separes
du reseau (fetch), comme feed_fetcher.py : les tests les exercent sur du
HTML/XML ecrit a la main.
"""
from __future__ import annotations

import re
from dataclasses import replace
from html import unescape

from core.logging_setup import get_logger
from gaming_news.models import Article, NewsSource

logger = get_logger()

SITEMAP_INDEX_URL = "https://www.breakflip.com/sitemap_index.xml"

# Une carte commence par le lien plein cadre vers l'article ; tout ce qui suit
# jusqu'a la carte suivante lui appartient.
_CARD_START_RE = re.compile(
    r'<a\s+href="(https://www\.breakflip\.com/actualites/\d+\.html)"[^>]*class="[^"]*absolute',
    re.IGNORECASE)
_IMG_RE = re.compile(r'<img[^>]+src="([^"]+)"', re.IGNORECASE)
_H3_RE = re.compile(r"<h3[^>]*>(.*?)</h3>", re.IGNORECASE | re.DOTALL)
_P_RE = re.compile(r"<p[^>]*>(.*?)</p>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_SITEMAP_ENTRY_RE = re.compile(
    r"<loc>\s*([^<\s]+)\s*</loc>\s*<lastmod>\s*([^<\s]+)\s*</lastmod>", re.IGNORECASE)


def _clean(fragment: str) -> str:
    return " ".join(unescape(_TAG_RE.sub(" ", fragment)).split())


def parse_hub(html: str, source: NewsSource, max_articles: int = 20) -> list[Article]:
    """Articles de la page « Actualites », dans l'ordre de la page. Liste
    vide si la structure n'est pas reconnue (refonte du site) -- jamais
    d'exception."""
    starts = list(_CARD_START_RE.finditer(html))
    articles: list[Article] = []
    seen: set[str] = set()
    for index, match in enumerate(starts):
        url = match.group(1)
        if url in seen:
            continue
        end = starts[index + 1].start() if index + 1 < len(starts) else len(html)
        card = html[match.end():end]
        title_match = _H3_RE.search(card)
        if not title_match:
            continue
        title = _clean(title_match.group(1))
        if not title:
            continue
        # Le premier <p> APRES le titre est le chapeau ; le suivant, l'age
        # relatif et l'auteur (« Il y a 1 mois par ... »), ignore.
        summary_match = _P_RE.search(card, title_match.end())
        summary = _clean(summary_match.group(1)) if summary_match else ""
        if summary.lower().startswith("il y a "):
            summary = ""
        image_match = _IMG_RE.search(card)
        seen.add(url)
        articles.append(Article(
            source_key=source.key, source_label=source.label,
            title=title, url=url, summary=summary,
            feed_image_url=unescape(image_match.group(1)) if image_match else "",
        ))
        if len(articles) >= max_articles:
            break
    return articles


def latest_actu_sitemap(index_xml: str) -> str:
    """URL du sous-sitemap d'actualites modifie le plus recemment, ou ""."""
    candidates = [(lastmod, loc) for loc, lastmod in _SITEMAP_ENTRY_RE.findall(index_xml)
                  if "/actu-sitemap" in loc]
    if not candidates:
        return ""
    # Meme lastmod pour plusieurs fichiers (actu-sitemap.xml et le dernier
    # numerote) : le plus haut numero l'emporte, c'est lui qui recoit les
    # nouveaux articles.
    return max(candidates, key=lambda c: (c[0], _sitemap_number(c[1])))[1]


def _sitemap_number(url: str) -> int:
    match = re.search(r"actu-sitemap(\d*)\.xml", url)
    return int(match.group(1)) if match and match.group(1) else 1


def parse_sitemap_dates(sitemap_xml: str) -> dict[str, str]:
    """{url de l'article: date ISO 8601} d'un sous-sitemap."""
    return {loc: lastmod for loc, lastmod in _SITEMAP_ENTRY_RE.findall(sitemap_xml)}


def fetch(source: NewsSource, timeout_s: float, max_articles: int,
          user_agent: str) -> list[Article]:
    """Articles Breakflip, dates comprises quand le sitemap les donne. Liste
    vide si la page est injoignable -- jamais d'exception."""
    import requests

    headers = {"User-Agent": user_agent}
    try:
        response = requests.get(source.feed_url, timeout=timeout_s, headers=headers)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        logger.warning(f"Page « {source.label} » injoignable : {e}")
        return []

    from news_story.image_fetcher import decode_html

    page = decode_html(response.content, response.headers.get("Content-Type", ""))
    articles = parse_hub(page, source, max_articles=max_articles)
    if not articles:
        logger.warning(f"Page « {source.label} » : aucun article reconnu (structure du site modifiee ?)")
        return []

    dates: dict[str, str] = {}
    try:
        index = requests.get(SITEMAP_INDEX_URL, timeout=timeout_s, headers=headers)
        index.raise_for_status()
        sitemap_url = latest_actu_sitemap(index.text)
        if sitemap_url:
            sitemap = requests.get(sitemap_url, timeout=timeout_s, headers=headers)
            sitemap.raise_for_status()
            dates = parse_sitemap_dates(sitemap.text)
    except requests.exceptions.RequestException as e:
        logger.warning(f"Dates « {source.label} » indisponibles (sitemap) : {e}")

    return [replace(a, published_at=dates.get(a.url, "")) for a in articles]
