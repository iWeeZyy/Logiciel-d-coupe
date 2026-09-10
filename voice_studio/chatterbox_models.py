"""Les poids de Chatterbox sur le disque : etat, telechargement, suppression.

Meme forme que voice_studio/piper_models.py, et le MEME telechargeur
(voice_studio/downloads.py) : reprise apres coupure, annulation, verification
de l'espace disque, progression. Il n'y avait aucune raison d'en ecrire un
second.

DEUX CHOIX EXPLIQUES.

- LES FICHIERS SONT TELECHARGES UN PAR UN, aux adresses du depot Hugging Face
  indiquees dans le catalogue, plutot que par `snapshot_download` de la
  bibliotheque officielle. Cette derniere fonctionne, mais elle vit dans
  l'environnement Chatterbox (qui n'est pas encore installe au moment ou l'on
  veut telecharger), elle n'offre ni reprise visible ni annulation dans notre
  interface, et elle irait ecrire dans le cache Hugging Face plutot que dans le
  dossier de l'application.
- RIEN N'EST TELECHARGE TOUT SEUL. Aucun appel ici n'est declenche par le
  demarrage de l'application : il faut une action explicite dans la fenetre
  Chatterbox.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from core.cancellation import CancelToken
from core.logging_setup import get_logger
from core.paths import user_data_dir
from voice_studio import chatterbox_catalogue as catalogue
from voice_studio import downloads

logger = get_logger()

# Un fichier de poids plus petit que cela est forcement un fichier d'erreur
# (page HTML, message JSON) plutot qu'un modele.
MIN_WEIGHT_BYTES = 1024


class ChatterboxModelError(Exception):
    """Message deja ecrit pour un humain."""


def models_dir() -> Path:
    """Dossier des poids. Deplacable par les reglages, comme les voix Piper."""
    try:
        from gui import settings_store

        configured = settings_store.get("chatterbox_models_dir")
    except Exception:                                  # pragma: no cover - reglages illisibles
        configured = None
    if configured:
        return Path(configured)
    return user_data_dir() / "voice_studio_data" / "chatterbox"


def file_path(name: str) -> Path:
    return models_dir() / name


def expected_files() -> list[str]:
    return [name for name, _ in catalogue.file_urls()]


def missing_files() -> list[str]:
    """Fichiers annonces par le catalogue qui manquent ou sont manifestement
    vides. C'est ce qui distingue « pas installe » de « installe a moitie »."""
    missing = []
    for name in expected_files():
        path = file_path(name)
        if not path.is_file() or path.stat().st_size < MIN_WEIGHT_BYTES:
            missing.append(name)
    return missing


def is_installed() -> bool:
    files = expected_files()
    return bool(files) and not missing_files()


def installed_size_bytes() -> int:
    total = 0
    for name in expected_files():
        path = file_path(name)
        if path.is_file():
            total += path.stat().st_size
    return total


def free_space_bytes() -> int:
    directory = models_dir()
    probe = directory
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    return downloads.free_space(probe)


def install(on_progress: Optional[Callable] = None,
            cancel_token: Optional[CancelToken] = None,
            opener=None) -> Path:
    """Telecharge les poids manquants et renvoie le dossier.

    Seuls les fichiers manquants sont repris : relancer apres une coupure ne
    retelecharge pas ce qui est deja la, et un fichier interrompu reprend a
    l'octet ou il s'etait arrete (`.part`, gere par downloads.py).

    `on_progress(fraction, fichier, index, total)` : la fraction est celle du
    fichier en cours, pas de l'ensemble -- on ne connait pas la taille totale
    avant d'avoir demande chaque fichier, et afficher une progression globale
    inventee serait pire que d'afficher « fichier 2 sur 6 ».
    """
    urls = catalogue.file_urls()
    if not urls:
        raise ChatterboxModelError(
            "Le catalogue Chatterbox (config/chatterbox.json) ne donne aucune adresse "
            "de téléchargement : impossible de récupérer le modèle.")

    directory = models_dir()
    directory.mkdir(parents=True, exist_ok=True)
    todo = [(name, url) for name, url in urls if name in set(missing_files())]
    total = len(todo)
    if not todo:
        return directory

    for index, (name, url) in enumerate(todo, start=1):
        if cancel_token is not None:
            cancel_token.check()

        def _progress(fraction, done, size, _name=name, _index=index):
            if on_progress:
                on_progress(fraction, _name, _index, total)

        logger.info(f"Chatterbox : téléchargement de {name} ({index}/{total})")
        try:
            downloads.download_file(url, file_path(name), on_progress=_progress,
                                    cancel_token=cancel_token, opener=opener)
        except downloads.DownloadError as error:
            raise ChatterboxModelError(str(error)) from error
    return directory


def remove() -> int:
    """Supprime les poids. Renvoie le nombre de fichiers effaces."""
    removed = 0
    directory = models_dir()
    if not directory.is_dir():
        return 0
    for name in expected_files():
        path = directory / name
        if path.is_file():
            path.unlink()
            removed += 1
        partial = path.with_suffix(path.suffix + ".part")
        if partial.is_file():
            partial.unlink()
    return removed


def describe() -> dict:
    """Etat lisible, pour l'interface et pour les journaux."""
    spec = catalogue.model_spec()
    missing = missing_files()
    return {
        "label": spec.get("label", "Chatterbox"),
        "key": spec.get("key", ""),
        "directory": str(models_dir()),
        "installed": is_installed(),
        "missing": missing,
        "files_total": len(expected_files()),
        "size_bytes": installed_size_bytes(),
        # Taille annoncee : inconnue tant qu'on n'a pas telecharge. Le
        # catalogue la laisse nulle plutot que de porter un chiffre invente.
        "expected_size_bytes": spec.get("size_bytes"),
        "free_bytes": free_space_bytes(),
        "languages": catalogue.languages(),
        "code_license": spec.get("code_license", ""),
        "weights_license": spec.get("weights_license", ""),
        "watermark": spec.get("watermark", ""),
        "notes": spec.get("notes", ""),
    }
