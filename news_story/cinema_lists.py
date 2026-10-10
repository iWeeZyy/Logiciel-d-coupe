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
