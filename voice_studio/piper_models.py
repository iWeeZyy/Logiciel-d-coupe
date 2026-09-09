"""Gestionnaire des voix Piper : catalogue, installation, suppression.

Une voix Piper est une PAIRE de fichiers : le modele (.onnx, quelques dizaines
de megaoctets) et sa configuration (.onnx.json, quelques kilooctets). Les deux
doivent etre presents pour que la voix existe : un modele sans configuration
n'est pas chargeable, et c'est exactement l'etat dans lequel un telechargement
interrompu laisse le dossier.

Regles tenues ici :

- RIEN N'EST TELECHARGE SANS DEMANDE EXPLICITE. Aucune installation
  automatique au premier lancement : plusieurs dizaines de megaoctets ne
  partent pas sans un clic.
- UNE VOIX INSTALLEE EST UNE VOIX UTILISABLE. Le telechargement ecrit dans un
  fichier .part, verifie la taille annoncee par le serveur, puis renomme. Un
  fichier a moitie ecrit n'est jamais vu comme une voix installee.
- APRES INSTALLATION, PLUS RIEN NE SORT DE LA MACHINE. Le reseau ne sert qu'a
  recuperer les fichiers ; la synthese, elle, est entierement locale.
"""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from core.cancellation import CancelToken
from core.config_loader import CONFIG_DIR
from core.logging_setup import get_logger
from core.paths import user_data_dir
from voice_studio import downloads
from utils.errors import CancelledError

logger = get_logger()

CATALOGUE_FILE = "piper_voices.json"
MODEL_SUFFIX = ".onnx"
CONFIG_SUFFIX = ".onnx.json"
FREE_SPACE_MARGIN = downloads.FREE_SPACE_MARGIN


class PiperModelError(Exception):
    """Probleme d'installation d'une voix, formule pour l'utilisateur."""


@dataclass(frozen=True)
class CatalogueVoice:
    """Une voix proposee au telechargement."""

    key: str
    label: str
    language: str = "fr_FR"
    quality: str = ""
    gender: str = ""
    path: str = ""

    @property
    def language_label(self) -> str:
        return {"fr_FR": "🇫🇷 Français", "en_US": "🇬🇧 Anglais",
                "es_ES": "🇪🇸 Espagnol", "de_DE": "🇩🇪 Allemand",
                "it_IT": "🇮🇹 Italien"}.get(self.language, self.language)


def models_dir() -> Path:
    """Dossier des voix installees.

    Sous les donnees de l'application, jamais dans le dossier d'installation :
    Program Files est protege en ecriture, et une desinstallation emporterait
    des fichiers que l'utilisateur a explicitement telecharges.
    """
    from gui import settings_store

    configured = None
    try:
        configured = settings_store.get("piper_models_dir")
    except Exception:                                  # pragma: no cover - reglages illisibles
        configured = None
    path = Path(configured) if configured else user_data_dir() / "voice_studio_data" / "piper"
    return path


def catalogue() -> list[CatalogueVoice]:
    """Voix proposees, lues dans config/piper_voices.json.

    Un catalogue illisible n'empeche pas l'application de fonctionner : elle
    n'a alors simplement rien a proposer au telechargement, et les voix deja
    installees continuent de marcher.
    """
    path = CONFIG_DIR / CATALOGUE_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        logger.warning(f"Catalogue des voix Piper illisible ({path}) : {error}")
        return []
    return [CatalogueVoice(key=entry.get("key", ""), label=entry.get("label", ""),
                           language=entry.get("language", ""), quality=entry.get("quality", ""),
                           gender=entry.get("gender", ""), path=entry.get("path", ""))
            for entry in data.get("voices", []) if entry.get("key")]


def base_url() -> str:
    path = CONFIG_DIR / CATALOGUE_FILE
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("base_url", "").rstrip("/")
    except (OSError, json.JSONDecodeError):
        return ""


def model_path(key: str) -> Path:
    return models_dir() / f"{key}{MODEL_SUFFIX}"


def config_path(key: str) -> Path:
    return models_dir() / f"{key}{CONFIG_SUFFIX}"


def is_installed(key: str) -> bool:
    """Les DEUX fichiers sont la, et le modele n'est pas vide."""
    model, config = model_path(key), config_path(key)
    return model.is_file() and config.is_file() and model.stat().st_size > 1024


def installed_keys() -> list[str]:
    """Voix reellement presentes sur le disque, catalogue ou pas.

    Une voix posee a la main dans le dossier compte : le catalogue n'est qu'une
    liste de propositions, il ne decide pas de ce qui existe.
    """
    directory = models_dir()
    if not directory.is_dir():
        return []
    keys = []
    for model in sorted(directory.glob(f"*{MODEL_SUFFIX}")):
        if model.name.endswith(CONFIG_SUFFIX):
            continue
        key = model.name[: -len(MODEL_SUFFIX)]
        if is_installed(key):
            keys.append(key)
    return keys


def installed_size_bytes() -> int:
    directory = models_dir()
    if not directory.is_dir():
        return 0
    return sum(p.stat().st_size for p in directory.glob("*") if p.is_file())


def describe(key: str) -> CatalogueVoice:
    """Fiche d'une voix : celle du catalogue, ou une fiche minimale batie sur
    le nom du fichier pour une voix posee a la main."""
    for voice in catalogue():
        if voice.key == key:
            return voice
    language = key.split("-")[0] if "-" in key else ""
    return CatalogueVoice(key=key, label=key, language=language)


def remove(key: str) -> bool:
    """Desinstalle une voix. Les deux fichiers partent ensemble."""
    removed = False
    for path in (model_path(key), config_path(key)):
        if path.is_file():
            path.unlink()
            removed = True
    return removed


# Le telechargement lui-meme vit dans voice_studio/downloads.py : le
# gestionnaire des modeles de langue en a besoin a l'identique, et deux copies
# auraient fini par diverger. Les noms locaux sont conserves : ils sont
# utilises par les tests et par le reste du module.
_free_space = downloads.free_space
_explain_http = downloads.explain_http


def _download_file(url: str, target: Path, on_progress=None, cancel_token=None,
                   opener=None, allow_resume: bool = True) -> None:
    try:
        downloads.download_file(url, target, on_progress=on_progress,
                                cancel_token=cancel_token, opener=opener,
                                allow_resume=allow_resume)
    except downloads.DownloadError as error:
        raise PiperModelError(str(error)) from error


def install(key: str, on_progress: Optional[Callable] = None,
            cancel_token: Optional[CancelToken] = None, opener=None) -> Path:
    """Telecharge la voix et renvoie le chemin du modele.

    Les deux fichiers sont recuperes : d'abord la configuration (petite, elle
    valide l'adresse en une seconde), puis le modele.
    """
    voice = None
    for entry in catalogue():
        if entry.key == key:
            voice = entry
            break
    if voice is None or not voice.path:
        raise PiperModelError(f"Voix inconnue au catalogue : {key}")

    root = base_url()
    if not root:
        raise PiperModelError(
            "Le catalogue des voix ne contient aucune adresse de téléchargement."
        )

    if on_progress:
        on_progress(None, 0, None)
    _download_file(f"{root}/{voice.path}{CONFIG_SUFFIX}", config_path(key),
                   on_progress=None, cancel_token=cancel_token, opener=opener)
    try:
        _download_file(f"{root}/{voice.path}{MODEL_SUFFIX}", model_path(key),
                       on_progress=on_progress, cancel_token=cancel_token, opener=opener)
    except (PiperModelError, CancelledError):
        # Une configuration seule ne doit pas rester : elle ferait croire a une
        # voix a moitie installee.
        config_path(key).unlink(missing_ok=True)
        raise

    if not is_installed(key):
        raise PiperModelError(
            "La voix a été téléchargée mais l'un des deux fichiers manque. "
            "Relance l'installation."
        )
    logger.info(f"Voix Piper installee : {key}")
    return model_path(key)


# --------------------------------------------------------------- moteur

def engine_dir() -> Path:
    """Ou est range le programme piper quand il est installe par l'application."""
    return models_dir().parent / "piper-bin"


def engine_binary() -> Path | None:
    """Programme piper installe ici, ou None."""
    directory = engine_dir()
    if not directory.is_dir():
        return None
    for candidate in ("piper.exe", "piper"):
        for path in directory.rglob(candidate):
            if path.is_file():
                return path
    return None


def engine_url(platform_name: str | None = None) -> str:
    """Adresse du programme piper pour ce systeme, lue dans le catalogue."""
    import sys

    key = platform_name or sys.platform
    if key.startswith("win"):
        key = "win32"
    elif key.startswith("linux"):
        key = "linux"
    elif key.startswith("darwin"):
        key = "darwin"
    path = CONFIG_DIR / CATALOGUE_FILE
    try:
        engine = json.loads(path.read_text(encoding="utf-8")).get("engine", {})
    except (OSError, json.JSONDecodeError):
        return ""
    return engine.get(key, "")


def install_engine(on_progress: Optional[Callable] = None,
                   cancel_token: Optional[CancelToken] = None, opener=None,
                   platform_name: str | None = None) -> Path:
    """Telecharge et installe le programme piper.

    Sert quand la bibliotheque Python n'est pas presente -- c'est le cas de
    l'application compilee, qui ne l'embarque pas. L'archive est extraite dans
    un dossier a part des modeles : supprimer une voix ne doit pas emporter le
    moteur, et reciproquement.
    """
    import shutil as _shutil
    import tarfile
    import tempfile
    import zipfile

    url = engine_url(platform_name)
    if not url:
        raise PiperModelError(
            "Aucune adresse de téléchargement du moteur Piper n'est renseignée "
            "pour ce système dans config/piper_voices.json."
        )

    with tempfile.TemporaryDirectory(prefix="piper_engine_") as workdir:
        archive = Path(workdir) / Path(url).name
        _download_file(url, archive, on_progress=on_progress, cancel_token=cancel_token,
                       opener=opener)

        target = engine_dir()
        if target.exists():
            _shutil.rmtree(target)
        target.mkdir(parents=True, exist_ok=True)
        try:
            if archive.suffix == ".zip":
                with zipfile.ZipFile(archive) as bundle:
                    bundle.extractall(target)
            else:
                with tarfile.open(archive) as bundle:
                    bundle.extractall(target)
        except (zipfile.BadZipFile, tarfile.TarError) as error:
            _shutil.rmtree(target, ignore_errors=True)
            raise PiperModelError(
                "L'archive du moteur Piper est illisible (téléchargement abîmé). "
                "Relance l'installation."
            ) from error

    binary = engine_binary()
    if binary is None:
        raise PiperModelError(
            "Le moteur a été téléchargé mais le programme piper est introuvable "
            "dans l'archive : son contenu a peut-être changé."
        )
    try:
        binary.chmod(binary.stat().st_mode | 0o111)    # sans effet sous Windows
    except OSError:                                    # pragma: no cover
        pass
    logger.info(f"Moteur Piper installe : {binary}")
    return binary
