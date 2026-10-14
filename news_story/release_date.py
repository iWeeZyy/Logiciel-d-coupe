"""Date de sortie et synopsis d'un film, pour les bandes-annonces.

Demande explicite de l'utilisateur : « ajouter la date de sortie du film dans
les bandes-annonces, juste sous le titre ». Trois sources, dans l'ordre de
fiabilite, et rien d'invente -- sans date trouvee, rien n'est affiche :
1. la fiche AlloCine du film quand la bande-annonce vient d'AlloCine (son
   adresse porte l'identifiant « cfilm=… ») ;
2. le texte de la video (titre, description) : « au cinéma le 12 novembre »,
   « sur Netflix le 3 décembre 2026 »… ;
3. la recherche AlloCine par titre, seulement si un resultat porte EXACTEMENT
   ce titre (un film homonyme d'une autre epoque serait une fausse date).

Le synopsis (legende des bandes-annonces, autre demande de l'utilisateur)
vient de la meme fiche AlloCine : celle de l'adresse « cfilm=… », sinon
celle du resultat de recherche au titre exact (film, puis serie). Jamais
reformule ; aucun synopsis plutot que celui d'un homonyme.
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from html import unescape
from urllib.parse import quote

from core.logging_setup import get_logger

logger = get_logger()

_MONTHS = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
           "septembre", "octobre", "novembre", "décembre")
_MONTH_RE = "|".join(_MONTHS + ("fevrier", "aout", "decembre"))
_DATE_RE = re.compile(rf"\b(1er|\d{{1,2}})\s+({_MONTH_RE})(?:\s+(\d{{4}}))?\b", re.I)
# « au cinéma le 12 novembre », « en salles le … », « sur Netflix le … »,
# « sortie le … », « dès le … », « à partir du … ».
_TEXT_RE = re.compile(
    rf"\b(au cin[ée]ma|en salles?|sur [A-Z][\w+ ]{{1,20}}?|sortie(?: pr[ée]vue)?|d[èe]s|"
    rf"[àa] partir du|disponible)\s+(?:le\s+|du\s+)?((?:1er|\d{{1,2}})\s+(?:{_MONTH_RE})"
    rf"(?:\s+\d{{4}})?)", re.I)


@dataclass
class Release:
    day: str                 # « 14 octobre 2026 », tel que lu
    where: str = ""          # « en salle », « sur Netflix », ""


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", unescape(text or "")).encode("ascii", "ignore").decode()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


def parse_date(text: str, today: date | None = None) -> date | None:
    """« 14 octobre 2026 » -> date ; sans annee : l'occurrence a venir."""
    match = _DATE_RE.search(text or "")
    if not match:
        return None
    day = 1 if match.group(1).lower() == "1er" else int(match.group(1))
    month_name = _fold(match.group(2))
    month = [_fold(m) for m in _MONTHS].index(month_name) + 1
    today = today or date.today()
    year = int(match.group(3)) if match.group(3) else today.year
    try:
        found = date(year, month, day)
    except ValueError:
        return None
    if not match.group(3) and found < today.replace(day=1):
        found = found.replace(year=year + 1)
    return found


def parse_film_page(page_html: str) -> Release | None:
    """Fiche film AlloCine : la date et le type de sortie (« en salle »)."""
    info = re.search(r'meta-body-info">(.*?)</div>', page_html or "", re.S)
    if not info:
        return None
    day = re.search(r'class="[^"]*\bdate\b[^"]*"[^>]*>\s*([^<]+?)\s*<', info.group(1))
    if not day or not _DATE_RE.search(day.group(1)):
        return None
    where = re.search(r'meta-release-type">\s*([^<]+?)\s*<', info.group(1))
    return Release(" ".join(unescape(day.group(1)).split()),
                   " ".join(unescape(where.group(1)).split()) if where else "")


def _exact_card(page_html: str, title: str) -> str:
    """La carte du premier resultat de recherche dont le titre est EXACTEMENT
    `title` ("" sinon)."""
    wanted = _fold(title)
    if not wanted:
        return ""
    for card in (page_html or "").split('<li class="mdl')[1:]:
        name = re.search(r'meta-title-link[^"]*"[^>]*>(.*?)</', card, re.S)
        if name and _fold(re.sub(r"<[^>]+>", " ", name.group(1))) == wanted:
            return card
    return ""


def parse_search(page_html: str, title: str) -> Release | None:
    """Date du premier resultat de recherche dont le titre est EXACTEMENT `title`."""
    card = _exact_card(page_html, title)
    day = re.search(r'class="[^"]*\bdate\b[^"]*"[^>]*>\s*([^<]+?)\s*<', card)
    if day and _DATE_RE.search(day.group(1)):
        return Release(" ".join(unescape(day.group(1)).split()), "en salle")
    return None


def search_page_link(page_html: str, title: str) -> str:
    """Adresse de la fiche du resultat au titre exact ("" sinon). AlloCine
    chiffre ses liens (classe « ACr… »)."""
    from gaming_news.featured import decode_allocine_link

    card = _exact_card(page_html, title)
    link = re.search(r'class="(ACr[A-Za-z0-9+/=]+) [^"]*meta-title-link', card) or \
        re.search(r'href="(/(?:film|series)/[^"]+)"', card)
    if not link:
        return ""
    path = link.group(1)
    if path.startswith("ACr"):
        path = decode_allocine_link(path)
    return f"https://www.allocine.fr{path}" if path.startswith("/") else path


_MAX_SYNOPSIS = 700


def parse_synopsis(page_html: str) -> str:
    """Synopsis d'une fiche AlloCine (donnees structurees « Movie » /
    « TVSeries »), tel quel ; coupe en fin de phrase s'il est tres long."""
    for block in re.findall(r'<script type="application/ld\+json">(.*?)</script>',
                            page_html or "", re.S):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        for item in data if isinstance(data, list) else [data]:
            if isinstance(item, dict) and item.get("@type") in ("Movie", "TVSeries"):
                text = " ".join(unescape(re.sub(r"<[^>]+>", " ", item.get("description") or "")).split())
                if len(text) > _MAX_SYNOPSIS:
                    cut = text[:_MAX_SYNOPSIS]
                    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
                    text = cut[:end + 1] if end > 200 else cut.rsplit(" ", 1)[0] + "…"
                if text:
                    return text
    return ""


def from_text(text: str) -> Release | None:
    match = _TEXT_RE.search(text or "")
    if not match:
        return None
    where = match.group(1).strip()
    lowered = _fold(where)
    if lowered.startswith(("au cinema", "en salle")):
        where = "en salle"
    elif not lowered.startswith("sur "):
        where = ""
    return Release(" ".join(match.group(2).split()), where)


def line(release: Release | None, today: date | None = None) -> str:
    """« Au cinéma le 14 octobre 2026 », « Sur Netflix le 3 décembre »,
    « Au cinéma depuis le … » pour une date passee ; "" sans date."""
    if release is None or not release.day:
        return ""
    today = today or date.today()
    when = parse_date(release.day, today)
    past = when is not None and when < today
    where = release.where.lower()
    if "salle" in where or "cin" in where:
        head = "Au cinéma"
    elif where.startswith("sur "):
        head = release.where[0].upper() + release.where[1:]
    else:
        head = "Sorti" if past else "Sortie"
        return f"{head} le {release.day}"
    return f"{head} {'depuis' if past else ''} le {release.day}".replace("  ", " ")


# ---------------------------------------------------------------- reseau

_CFILM_RE = re.compile(r"cfilm=(\d+)")


@dataclass
class FilmInfo:
    release: Release | None = None
    synopsis: str = ""


def find_film(url: str, title: str, text: str = "", timeout_s: float = 15) -> FilmInfo:
    """Date de sortie et synopsis : fiche AlloCine de l'adresse, sinon fiche
    du resultat de recherche au titre exact ; la date du texte de la video
    passe avant celle d'une recherche. Jamais d'exception."""
    from gaming_news.featured import fetch_page

    info = FilmInfo()
    try:
        match = _CFILM_RE.search(url or "")
        if match:
            page = fetch_page(
                f"https://www.allocine.fr/film/fichefilm_gen_cfilm={match.group(1)}.html", timeout_s)
            info.release, info.synopsis = parse_film_page(page), parse_synopsis(page)
        if info.release is None:
            info.release = from_text(text)
        if title and not (info.release and info.synopsis):
            for kind in ("movie", "series"):
                found = fetch_page(
                    f"https://www.allocine.fr/rechercher/{kind}/?q={quote(title)}", timeout_s)
                link = search_page_link(found, title)
                if not link:
                    continue
                page = fetch_page(link, timeout_s)
                if info.release is None:
                    info.release = parse_film_page(page) or parse_search(found, title)
                if not info.synopsis:
                    info.synopsis = parse_synopsis(page)
                break
    except Exception as e:  # noqa: BLE001 -- une info manquante ne bloque rien
        logger.warning(f"Fiche du film introuvable pour « {title} » : {e}")
    return info


def find_release(url: str, title: str, text: str = "", timeout_s: float = 15) -> Release | None:
    """La date de sortie seule (voir find_film)."""
    return find_film(url, title, text, timeout_s).release
