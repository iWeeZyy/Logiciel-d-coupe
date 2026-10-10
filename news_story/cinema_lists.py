"""Listes de films AlloCine : sorties de la semaine, box-office France, films
les mieux notes (pour « Devine le film »), et photos d'un film.

Demande explicite de l'utilisateur pour son compte cinema : un carrousel des
sorties de la semaine, un du box-office, et un jeu « Devine le film ». Tout
vient des pages publiques d'AlloCine, lues telles quelles -- rien n'est
invente : un champ absent de la page (pas de note, pas de synopsis) reste vide.

Parseurs purs (HTML -> donnees), reseau a part (fetch_*), comme
gaming_news/featured.py.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from html import unescape
from urllib.parse import urljoin

from core.logging_setup import get_logger

logger = get_logger()

BASE_URL = "https://www.allocine.fr/"
RELEASES_URL = BASE_URL + "film/sorties-semaine/"
BOX_OFFICE_URL = BASE_URL + "boxoffice/france/"
BEST_URL = BASE_URL + "film/meilleurs/"

_TAG_RE = re.compile(r"<[^>]+>")
_FILM_ID_RE = re.compile(r"cfilm=(\d+)|fichefilm-(\d+)")
# Les vignettes sont redimensionnees par un segment « c_310_420/ » de
# l'adresse : sans lui, le CDN rend l'image d'origine (pleine resolution).
_RESIZE_RE = re.compile(r"/[a-z]_\d+_\d+/")


@dataclass
class Film:
    title: str
    url: str = ""
    film_id: str = ""
    poster_url: str = ""
    release_date: str = ""          # « 7 octobre 2026 », tel qu'affiche
    duration: str = ""              # « 1h 30min »
    genres: list[str] = field(default_factory=list)
    directors: list[str] = field(default_factory=list)
    actors: list[str] = field(default_factory=list)
    synopsis: str = ""
    press_rating: str = ""          # « 2,0 » (sur 5), "" si pas de note
    spectator_rating: str = ""


@dataclass
class BoxOfficeEntry:
    rank: int
    film: Film
    distributor: str = ""
    entries: str = ""               # « 379 236 », entrees de la semaine
    cumulative: str = ""            # « 882 101 »
    week: str = ""                  # « 2 » : semaine d'exploitation


_INVISIBLE_RE = re.compile("[\u200b\u200c\u200d\u2060\ufeff]")


def _text(fragment: str) -> str:
    text = _INVISIBLE_RE.sub("", unescape(_TAG_RE.sub(" ", fragment or "")))
    return " ".join(text.split())


def full_size(image_url: str) -> str:
    """Adresse de l'image d'origine (sans le segment de redimensionnement)."""
    return _RESIZE_RE.sub("/", image_url or "", count=1)


def film_id_of(url: str) -> str:
    match = _FILM_ID_RE.search(url or "")
    return (match.group(1) or match.group(2)) if match else ""


def _image_url(fragment: str, img_class: str) -> str:
    """data-src d'abord (images chargees a la demande), sinon src -- jamais
    l'image transparente de remplissage « data:image/gif »."""
    for match in re.finditer(r'<img[^>]+class="%s[^"]*"[^>]*>' % re.escape(img_class), fragment):
        tag = match.group(0)
        for attr in ("data-src", "src"):
            found = re.search(r'\b%s="(https?://[^"]+)"' % attr, tag)
            if found:
                return full_size(unescape(found.group(1)))
    return ""


def _rating(fragment: str, label: str) -> str:
    match = re.search(label + r"\s*</(?:span|a)>.*?stareval-note[^>]*>([^<]*)<", fragment, re.S)
    note = (match.group(1).strip() if match else "")
    return note if re.fullmatch(r"\d[,.]\d", note) else ""


def parse_film_card(card: str) -> Film | None:
    """Une carte « entity-card » (sorties, meilleurs films)."""
    title_match = re.search(r'meta-title-link"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', card, re.S)
    if not title_match:
        return None
    url = urljoin(BASE_URL, unescape(title_match.group(1)))
    film = Film(title=_text(title_match.group(2)), url=url, film_id=film_id_of(url),
                poster_url=_image_url(card, "thumbnail-img"))
    info = re.search(r'meta-body-info">(.*?)</div>', card, re.S)
    if info:
        date = re.search(r'<span class="date">(.*?)</span>', info.group(1), re.S)
        film.release_date = _text(date.group(1)) if date else ""
        parts = [p.strip() for p in _text(info.group(1)).split("|")]
        for part in parts:
            if re.fullmatch(r"(\d+h\s*)?\d+min|\d+h", part):
                film.duration = part
        genre_zone = info.group(1).split("</span>", 1)[-1] if date else info.group(1)
        film.genres = [_text(g) for g in re.findall(r'dark-grey-link">(.*?)</span>', genre_zone)
                       if _text(g)]
    direction = re.search(r'meta-body-direction[^"]*">(.*?)</div>', card, re.S)
    if direction:
        film.directors = [_text(n) for n in re.findall(r'dark-grey-link"[^>]*>(.*?)</(?:span|a)>',
                                                         direction.group(1)) if _text(n)]
    actors = re.search(r'meta-body-actor[^"]*">(.*?)</div>', card, re.S)
    if actors:
        film.actors = [_text(n) for n in re.findall(r'dark-grey-link"[^>]*>(.*?)</(?:span|a)>',
                                                      actors.group(1)) if _text(n)]
    synopsis = re.search(r'<div class="content-txt[^"]*">(.*?)</div>', card, re.S)
    film.synopsis = _text(synopsis.group(1)) if synopsis else ""
    film.press_rating = _rating(card, "Presse")
    film.spectator_rating = _rating(card, "Spectateurs")
    return film


def _cards(page_html: str) -> list[str]:
    return (page_html or "").split('<li class="mdl">')[1:]


def parse_releases(page_html: str) -> list[Film]:
    """Films sortis cette semaine, dans l'ordre de la page (les plus attendus
    d'abord)."""
    films = [parse_film_card(card) for card in _cards(page_html)]
    return [f for f in films if f is not None and f.title]


def parse_best_films(page_html: str) -> list[Film]:
    return parse_releases(page_html)


def parse_box_office(page_html: str) -> tuple[str, list[BoxOfficeEntry]]:
    """(« semaine du 30 septembre 2026 », classement). La semaine est celle que
    la page selectionne ; "" si elle ne la donne pas."""
    week = re.search(r'<option[^>]*selected[^>]*>\s*([^<]+?)\s*</option>', page_html or "")
    week_label = f"semaine du {_text(week.group(1))}" if week else ""
    entries: list[BoxOfficeEntry] = []
    for row in re.findall(r'<tr class="responsive-table-row">(.*?)</tr>', page_html or "", re.S):
        title = re.search(r'meta-title-link"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', row, re.S)
        if not title:
            continue
        url = urljoin(BASE_URL, unescape(title.group(1)))
        rank = re.search(r'label-ranking">\s*(\d+)\s*<', row)
        cells = [_text(c) for c in re.findall(r'<td data-heading="[^"]*"[^>]*>(.*?)</td>', row, re.S)]
        distributor = re.search(r'<div class="meta-body">(.*?)</div>', row, re.S)
        film = Film(title=_text(title.group(2)), url=url, film_id=film_id_of(url),
                    poster_url=_image_url(row, "thumbnail-img"))
        entries.append(BoxOfficeEntry(
            rank=int(rank.group(1)) if rank else len(entries) + 1, film=film,
            distributor=_text(distributor.group(1)) if distributor else "",
            entries=cells[0] if len(cells) > 0 else "",
            cumulative=cells[1] if len(cells) > 1 else "",
            week=cells[2] if len(cells) > 2 else ""))
    return week_label, entries


def parse_film_stills(page_html: str) -> list[str]:
    """Photos du film (section « Photos » de sa page, pas les affiches), en
    pleine resolution, dans l'ordre de la page."""
    page = page_html or ""
    start = re.search(r'titlebar-title[^>]*>\s*Photos\s*</h2>', page)
    if start:
        page = page[start.end():]
    urls: list[str] = []
    for match in re.finditer(r'<img[^>]+class="shot-img"[^>]*>', page):
        found = re.search(r'\bdata-src="(https?://[^"]+)"', match.group(0)) or \
            re.search(r'\bsrc="(https?://[^"]+)"', match.group(0))
        if found:
            url = full_size(unescape(found.group(1)))
            if url not in urls:
                urls.append(url)
    return urls


def photos_url(film: Film) -> str:
    return f"{BASE_URL}film/fichefilm-{film.film_id}/photos/" if film.film_id else ""


# ---------------------------------------------------------------- reseau

def fetch_releases(timeout_s: float = 20) -> list[Film]:
    from gaming_news.featured import fetch_page

    return parse_releases(fetch_page(RELEASES_URL, timeout_s))


def fetch_box_office(timeout_s: float = 20) -> tuple[str, list[BoxOfficeEntry]]:
    from gaming_news.featured import fetch_page

    return parse_box_office(fetch_page(BOX_OFFICE_URL, timeout_s))


def fetch_best_films(pages: int = 3, timeout_s: float = 20) -> list[Film]:
    from gaming_news.featured import fetch_page

    films: list[Film] = []
    for page in range(1, pages + 1):
        url = BEST_URL if page == 1 else f"{BEST_URL}?page={page}"
        films += parse_best_films(fetch_page(url, timeout_s))
    return films


def fetch_film_stills(film: Film, timeout_s: float = 20) -> list[str]:
    from gaming_news.featured import fetch_page

    url = photos_url(film)
    return parse_film_stills(fetch_page(url, timeout_s)) if url else []


# ------------------------------------------- semaines passees, presse/public

def agenda_url(wednesday) -> str:
    """Page des sorties d'une semaine passee (« film/agenda/sem-2026-09-30/ »)."""
    return f"{BASE_URL}film/agenda/sem-{wednesday.isoformat()}/"


def rating_value(rating: str) -> float | None:
    try:
        return float(rating.replace(",", "."))
    except (AttributeError, ValueError):
        return None


def rating_gap(film: Film) -> float | None:
    """Note spectateurs moins note presse ; None s'il manque l'une des deux."""
    press, public = rating_value(film.press_rating), rating_value(film.spectator_rating)
    return None if press is None or public is None else round(public - press, 1)


def fetch_recent_releases(weeks: int = 4, today=None, timeout_s: float = 20) -> list[Film]:
    """Sorties des `weeks` dernieres semaines (celle-ci comprise), sans doublon."""
    from datetime import date, timedelta

    from gaming_news.featured import fetch_page

    today = today or date.today()
    wednesday = today - timedelta(days=(today.weekday() - 2) % 7)
    films: list[Film] = []
    seen: set[str] = set()
    for week in range(weeks):
        for film in parse_releases(fetch_page(agenda_url(wednesday - timedelta(weeks=week)),
                                              timeout_s)):
            key = film.film_id or film.title
            if key not in seen:
                seen.add(key)
                films.append(film)
    return films


# ------------------------------------------------------------ streaming

# Pages plateformes d'AlloCine. Chaque semaine, AlloCine y publie (pour
# Netflix au moins) un article « Netflix : 19 nouveautés débarquent cette
# semaine » : l'agenda jour par jour. Une plateforme sans cet article est
# simplement absente du carrousel -- rien n'est devine.
STREAMING_PLATFORMS = (
    ("Netflix", "netflix/"), ("Disney+", "disney/"), ("Canal+", "mycanal/"),
    ("Apple TV", "appletvplus/"), ("HBO Max", "max/"),
    ("Paramount+", "paramountplus/"),
)
_NEWS_SERIES_URL = BASE_URL + "news/series/"
_DAY_RE = re.compile(r"^(lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche)\b|incontournable",
                     re.I)


@dataclass
class StreamingItem:
    platform: str
    title: str
    kind: str = ""            # « Film », « Série », « Films »
    day: str = ""             # « Vendredi 9 octobre »
    synopsis: str = ""
    url: str = ""             # fiche AlloCine du film / de la serie (affiche)
    poster_url: str = ""


def find_agenda_article(page_html: str, platform: str) -> str:
    """Adresse du dernier article « <Plateforme> : … nouveautés … » de la page."""
    for href, title in re.findall(r'meta-title-link"[^>]*href="(/article/[^"]+)"[^>]*>(.*?)</a>',
                                  page_html or "", re.S):
        title = _text(title)
        if re.match(re.escape(platform) + r"\s*:", title, re.I) and "nouveaut" in title.lower():
            return urljoin(BASE_URL, unescape(href))
    return ""


def parse_streaming_agenda(article_html: str, platform: str) -> list[StreamingItem]:
    """Titres de l'agenda, jour par jour (« Vendredi 9 octobre »), plus
    « L'incontournable de la semaine ». Les autres intertitres de l'article
    (sans date) sont ignores ; un titre deja vu n'est pas repete."""
    page = article_html or ""
    start, end = page.find("<article"), page.find("</article>")
    page = page[start:end] if start >= 0 and end > start else page
    items: list[StreamingItem] = []
    day = ""
    for tag, inner in re.findall(r'<(h2|p)\b[^>]*>(.*?)</\1>', page, re.S):
        text = _text(inner)
        if tag == "h2":
            day = text if _DAY_RE.search(text) else ""
            continue
        if not day or " - " not in text:
            continue
        head, _, rest = text.partition(" - ")
        kind, _, synopsis = rest.partition(" : ")
        head = re.sub(r"\s+,", ",", head).strip(" ,")
        if any(i.title == head for i in items):
            continue
        link = re.search(r'href="(/(?:film|series)/[^"]+)"', inner)
        items.append(StreamingItem(
            platform=platform, title=head, kind=kind.strip(), day=day,
            synopsis=synopsis.strip(),
            url=urljoin(BASE_URL, unescape(link.group(1))) if link else ""))
    return items


def page_poster(page_html: str) -> str:
    """Affiche d'une fiche film/serie (og:image), pleine taille."""
    match = re.search(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', page_html or "")
    return full_size(unescape(match.group(1))) if match else ""


def fetch_streaming(timeout_s: float = 20, on_progress=None) -> list[StreamingItem]:
    """Nouveautes de la semaine des plateformes qui publient leur agenda."""
    from gaming_news.featured import fetch_page

    series_news = fetch_page(_NEWS_SERIES_URL, timeout_s)
    items: list[StreamingItem] = []
    for name, path in STREAMING_PLATFORMS:
        if on_progress:
            on_progress(name)
        article = find_agenda_article(fetch_page(BASE_URL + path, timeout_s), name) or \
            find_agenda_article(series_news, name)
        if article:
            items += parse_streaming_agenda(fetch_page(article, timeout_s), name)
    return items


def fetch_poster(item_url: str, timeout_s: float = 20) -> str:
    from gaming_news.featured import fetch_page

    return page_poster(fetch_page(item_url, timeout_s)) if item_url else ""
