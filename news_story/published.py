"""Memoire des news deja exportees (visuel, video, Top news cine du jour).

Demande explicite de l'utilisateur : ne pas republier deux fois la meme
news. Chaque export reussi enregistre l'adresse de l'article ; la liste des
news l'indique (« ✅ Déjà publiée le … ») et le Top du jour ne la
pre-coche plus. Rien n'est bloque : on peut toujours la reprendre a la main.

Un fichier JSON dans le dossier de donnees de l'utilisateur
({adresse normalisee: date ISO}), purge des entrees de plus de 90 jours.
"""
from __future__ import annotations

import json
import threading
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlparse

from core.logging_setup import get_logger
from core.paths import user_data_dir

logger = get_logger()

FILE = user_data_dir() / "news_publiees.json"
KEEP_DAYS = 90
_lock = threading.Lock()


def url_key(url: str) -> str:
    """Hote sans www, chemin sans « / » final, sans parametres de suivi :
    la meme news vue depuis le flux ou la page d'accueil donne la meme cle."""
    parsed = urlparse(url or "")
    host = (parsed.netloc or "").lower().removeprefix("www.")
    path = parsed.path.rstrip("/")
    # Les videos YouTube / pages AlloCine se distinguent par leur requete.
    query = parsed.query if host.endswith(("youtube.com", "allocine.fr")) else ""
    return f"{host}{path}" + (f"?{query}" if query else "")


def _read(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def load() -> dict[str, str]:
    with _lock:
        return _read(FILE)


def published_on(url: str, data: dict | None = None) -> str:
    """Date ISO du premier export de cette news, ou ""."""
    data = load() if data is None else data
    return str(data.get(url_key(url), ""))


def mark(urls, day: date | None = None) -> None:
    """Enregistre les news exportees (la date d'un export plus ancien est
    gardee). Jamais d'exception : au pire la memoire n'est pas a jour."""
    day = day or date.today()
    limit = (day - timedelta(days=KEEP_DAYS)).isoformat()
    with _lock:
        data = _read(FILE)
        for url in urls:
            if url:
                data.setdefault(url_key(url), day.isoformat())
        data = {k: v for k, v in data.items() if str(v) >= limit}
        try:
            FILE.parent.mkdir(parents=True, exist_ok=True)
            FILE.write_text(json.dumps(data, ensure_ascii=False, indent=0), encoding="utf-8")
        except OSError as e:
            logger.warning(f"Mémoire des news publiées non écrite : {e}")


def badge(url: str, data: dict | None = None) -> str:
    """« ✅ Déjà publiée le 9/10 », ou ""."""
    day = published_on(url, data)
    if not day:
        return ""
    try:
        d = date.fromisoformat(day)
    except ValueError:
        return "✅ Déjà publiée"
    return f"✅ Déjà publiée le {d.day}/{d.month}"
