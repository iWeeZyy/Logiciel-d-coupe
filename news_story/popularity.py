"""Popularite d'un film sur AlloCine, pour choisir la bande-annonce du jour.

Constat de l'utilisateur (2026-11-01, « Arcobaleno ») : « Personne ne
regardera cette bande-annonce. » Le choix se faisait sur la seule date de
mise en ligne ; et les bandes-annonces des gros films (Dune, Jumanji, studios,
Netflix…) sont sur YouTube, qui refuse les serveurs GitHub -- il ne restait
que les videos AlloCine recentes, souvent de petits films.

L'autocompletion d'AlloCine (`/_/autocomplete/<titre>`, celle du champ de
recherche du site) donne pour chaque film : son nombre de vues sur AlloCine,
son RANG de popularite (`irankpopular`, 1 = le plus populaire), sa date de
sortie et sa derniere video. Releve du 2026-11-01 : Karma rang 1, Le Fantome
de l'Opera 2, Dune : Troisieme partie 12, Paper Tiger 72 -- Arcobaleno 1118,
Entretien avec un requin 1071.

Pour un gros film trouve sur YouTube, la video AlloCine du meme film est
utilisee a la place (servie en 1080p aux serveurs GitHub par Dailymotion).
"""
from __future__ import annotations

import difflib
import json
import unicodedata
from dataclasses import dataclass
from datetime import date, timedelta
from urllib.parse import quote

from core.logging_setup import get_logger

logger = get_logger()

AUTOCOMPLETE_URL = "https://www.allocine.fr/_/autocomplete/{query}"
PLAYER_URL = "https://www.allocine.fr/video/player_gen_cmedia={video}&cfilm={film}.html"
MAX_RANK = 150              # top 150 AlloCine...
MIN_VIEWS = 100_000         # ... ou au moins 100 000 vues sur la fiche
MAX_AGE_DAYS = 30           # deja en salle depuis plus d'un mois : plus une nouveaute
MIN_MATCH = 0.85            # ressemblance du titre (sinon ce n'est pas le meme film)


@dataclass(frozen=True)
class FilmInfo:
    film_id: str
    label: str
    views: int
    rank: int | None
    release: date | None
    video_id: str


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return " ".join("".join(ch if ch.isalnum() else " " for ch in text).split())


def same_title(query: str, label: str, original: str = "") -> bool:
    """Le resultat AlloCine est bien ce film (fonction pure) : « river » ne
    doit pas donner « Mystic River », ni « ali g 2 » « Alien Sexy Girls »."""
    q = _norm(query)
    return any(difflib.SequenceMatcher(None, q, _norm(name)).ratio() >= MIN_MATCH
               for name in (label, original) if name)


def parse(payload: dict, query: str) -> FilmInfo | None:
    """Le premier film (ou serie) du resultat qui correspond au titre (pure)."""
    for item in (payload or {}).get("results") or []:
        if item.get("entity_type") not in ("movie", "series"):
            continue
        if not same_title(query, item.get("label", ""), item.get("original_label", "")):
            continue
        raw = (item.get("last_release") or (item.get("data") or {}).get("last_release") or "")[:10]
        try:
            release = date.fromisoformat(raw) if raw else None
        except ValueError:
            release = None
        rank = item.get("irankpopular")
        video = (item.get("data") or {}).get("default_video")
        return FilmInfo(str(item.get("entity_id") or ""), item.get("label", ""),
                        int(item.get("viewcount") or 0), int(rank) if rank else None,
                        release, str(video) if video else "")
    return None


def lookup(title: str, timeout_s: float = 15.0) -> FilmInfo | None:
    """La fiche AlloCine du film `title`, ou None (introuvable, reseau)."""
    import requests

    if not title.strip():
        return None
    try:
        response = requests.get(AUTOCOMPLETE_URL.format(query=quote(title)), timeout=timeout_s,
                                headers={"User-Agent": "Mozilla/5.0"})
        response.raise_for_status()
        return parse(response.json(), title)
    except (requests.RequestException, ValueError, json.JSONDecodeError) as e:
        logger.warning(f"Popularité AlloCiné introuvable pour « {title} » : {e}")
        return None


def is_popular(info: FilmInfo | None, today: date) -> bool:
    """Assez connu pour qu'on regarde sa bande-annonce, et encore d'actualite
    (pure)."""
    if info is None:
        return False
    known = (info.rank is not None and info.rank <= MAX_RANK) or info.views >= MIN_VIEWS
    recent = info.release is None or info.release >= today - timedelta(days=MAX_AGE_DAYS)
    return known and recent


def popularity_key(info: FilmInfo) -> tuple:
    """Plus petit = plus populaire (pure)."""
    return (info.rank if info.rank is not None else 10_000, -info.views)


def allocine_video_url(info: FilmInfo) -> str:
    return PLAYER_URL.format(video=info.video_id, film=info.film_id) if info.video_id else ""
