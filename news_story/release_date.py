"""Date de sortie d'un film, pour l'afficher sous le titre d'une bande-annonce.

Demande explicite de l'utilisateur : « ajouter la date de sortie du film dans
les bandes-annonces, juste sous le titre ». Trois sources, dans l'ordre de
fiabilite, et rien d'invente -- sans date trouvee, rien n'est affiche :
1. la fiche AlloCine du film quand la bande-annonce vient d'AlloCine (son
   adresse porte l'identifiant « cfilm=… ») ;
2. le texte de la video (titre, description) : « au cinéma le 12 novembre »,
   « sur Netflix le 3 décembre 2026 »… ;
3. la recherche AlloCine par titre, seulement si un resultat porte EXACTEMENT
   ce titre (un film homonyme d'une autre epoque serait une fausse date).
"""
from __future__ import annotations

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


def parse_search(page_html: str, title: str) -> Release | None:
    """Premier resultat de recherche dont le titre est EXACTEMENT `title`."""
    wanted = _fold(title)
    for card in (page_html or "").split('<li class="mdl')[1:]:
        name = re.search(r'meta-title-link[^"]*"[^>]*>(.*?)</', card, re.S)
        if not name or _fold(re.sub(r"<[^>]+>", " ", name.group(1))) != wanted:
            continue
        day = re.search(r'class="[^"]*\bdate\b[^"]*"[^>]*>\s*([^<]+?)\s*<', card)
        if day and _DATE_RE.search(day.group(1)):
            return Release(" ".join(unescape(day.group(1)).split()), "en salle")
        return None
    return None


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


def find_release(url: str, title: str, text: str = "", timeout_s: float = 15) -> Release | None:
    """La date de sortie d'un film : fiche AlloCine, texte, puis recherche.
    Jamais d'exception (None si rien de sur)."""
    from gaming_news.featured import fetch_page

    try:
        match = _CFILM_RE.search(url or "")
        if match:
            release = parse_film_page(fetch_page(
                f"https://www.allocine.fr/film/fichefilm_gen_cfilm={match.group(1)}.html", timeout_s))
            if release:
                return release
        release = from_text(text)
        if release:
            return release
        if title:
            return parse_search(fetch_page(
                f"https://www.allocine.fr/rechercher/movie/?q={quote(title)}", timeout_s), title)
    except Exception as e:  # noqa: BLE001 -- une date manquante ne bloque rien
        logger.warning(f"Date de sortie introuvable pour « {title} » : {e}")
    return None
