"""Cache disque de l'image originale d'un article, par URL -- ne jamais
retelecharger la meme image deux fois pour la meme selection d'actualite
(contrainte explicite de la spec).

Meme patron que transcription/cache.py : cle = hash de l'identifiant naturel
(ici l'URL, pas un chemin de fichier), un fichier de donnees brutes et un
sidecar JSON pour les metadonnees derivees (dimensions), sous
user_data_dir()/.cache -- pas un nouveau repertoire racine invente.

La resolution insuffisante (`is_low_resolution`) n'est jamais corrigee ici
par un agrandissement/etirement silencieux : ce module se contente de
mesurer et d'exposer le fait, a charge de l'appelant (story_composer) de
choisir quoi en faire (avertir l'utilisateur, laisser generer quand meme --
jamais etirer sans le dire, contrainte explicite de la spec).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from core.logging_setup import get_logger
from core.paths import user_data_dir

logger = get_logger()

CACHE_DIR = user_data_dir() / ".cache" / "news_images"

DEFAULT_TIMEOUT_S = 10

# Sous ce seuil (dans au moins une dimension), l'image est jugee de trop
# faible resolution pour remplir proprement une Story 1080x1920 -- valeur
# volontairement generouse (pas 1080/1920 pile) puisque le compositeur
# recadre et peut agrandir moderement une image un peu petite ; seule une
# image nettement sous-dimensionnee doit declencher l'avertissement.
MIN_DIMENSION_PX = 400


@dataclass(frozen=True)
class CachedImage:
    """Une image telechargee et mise en cache, avec ses dimensions
    mesurees -- jamais devinees."""

    path: Path
    width: int
    height: int

    @property
    def is_low_resolution(self) -> bool:
        return self.width < MIN_DIMENSION_PX or self.height < MIN_DIMENSION_PX


class ImageFetchError(Exception):
    """L'image n'a pas pu etre recuperee ou n'est pas exploitable
    (timeout, erreur HTTP, format non supporte, fichier corrompu)."""


def _cache_key(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]


def _paths_for(url: str) -> tuple[Path, Path]:
    key = _cache_key(url)
    return CACHE_DIR / f"{key}.img", CACHE_DIR / f"{key}.meta.json"


def get_cached(url: str) -> CachedImage | None:
    """L'image deja en cache pour cette URL, ou None si jamais telechargee
    (ou si le cache est corrompu/incomplet -- traite alors comme absent,
    un nouveau telechargement le regenerera)."""
    img_path, meta_path = _paths_for(url)
    if not (img_path.exists() and meta_path.exists()):
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        return CachedImage(path=img_path, width=int(meta["width"]), height=int(meta["height"]))
    except (json.JSONDecodeError, KeyError, ValueError, OSError):
        return None


def download(url: str, timeout_s: float = DEFAULT_TIMEOUT_S) -> CachedImage:
    """L'image pour cette URL : servie depuis le cache si deja presente,
    sinon telechargee puis mise en cache. Leve `ImageFetchError` sur timeout,
    erreur HTTP, format d'image non supporte/corrompu -- jamais une image
    partielle ou invalide silencieusement acceptee."""
    cached = get_cached(url)
    if cached is not None:
        return cached

    import requests
    from PIL import Image, UnidentifiedImageError

    try:
        response = requests.get(
            url, timeout=timeout_s,
            headers={"User-Agent": "ClipFarming/1.0 (+lecteur d'actualites gaming)"},
        )
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise ImageFetchError(f"Image injoignable ({url}) : {e}") from e

    raw = response.content
    if not raw:
        raise ImageFetchError(f"Reponse vide pour l'image ({url})")

    from io import BytesIO
    try:
        with Image.open(BytesIO(raw)) as im:
            im.load()  # force le decodage complet -- detecte un fichier tronque/corrompu
            width, height = im.size
    except UnidentifiedImageError as e:
        raise ImageFetchError(f"Format d'image non supporte ({url})") from e
    except OSError as e:
        raise ImageFetchError(f"Image illisible/corrompue ({url}) : {e}") from e

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    img_path, meta_path = _paths_for(url)
    img_path.write_bytes(raw)
    meta_path.write_text(
        json.dumps({"url": url, "width": width, "height": height}, ensure_ascii=False),
        encoding="utf-8",
    )
    logger.info(f"Image mise en cache ({width}x{height}) : {url}")
    return CachedImage(path=img_path, width=width, height=height)
