"""Localise les fichiers de l'application : ceux qui viennent de l'installation
(lecture seule) et ceux que l'application ecrit (projets, caches, reglages).

Trois emplacements, et la distinction compte :

- app_base_dir()   : les fichiers POSES PAR L'INSTALLATEUR (config/ par defaut,
  assets/, ffmpeg.exe). En mode fige c'est le dossier de ClipFarming.exe, donc
  Program Files -- un dossier que Windows protege en ecriture et que le
  desinstalleur vide entierement.
- user_data_dir()  : ce que l'application ECRIT et qui ne regarde qu'elle
  (caches de transcription, modeles telecharges, reglages, cle API). Sous
  %LOCALAPPDATA%.
- default_projects_dir() : les projets de l'utilisateur, c'est-a-dire SON
  travail. Sous Documents, la ou il ira les chercher lui-meme.

Ecrire tout cela dans Program Files, comme c'etait le cas, pose trois
problemes reels : Windows protege ce dossier en ecriture, une desinstallation
emporte le travail de l'utilisateur avec l'application, et les fichiers crees
apres l'installation ne sont pas connus du desinstalleur -- ils survivent et
laissent le dossier debout.

En mode script (developpement), rien ne change : tout reste dans le depot, ou
.gitignore couvre deja .cache/ et user_projects/.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_DIR_NAME = "ClipFarming"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_base_dir() -> Path:
    """Racine des fichiers d'installation (lecture seule en mode fige).

    En mode normal, __file__ pointe dans le depot -> parent.parent = racine du
    projet. En mode fige (PyInstaller --onedir), sys.executable est le .exe
    genere et tous les fichiers de donnees (config/, ffmpeg.exe...) sont copies
    a cote de lui par le build -> son dossier EST la racine "projet".
    """
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def user_data_dir() -> Path:
    """Dossier inscriptible propre a l'utilisateur (caches, reglages, cle API).

    En developpement, c'est la racine du depot : le comportement d'avant est
    conserve a l'identique, y compris pour les tests.
    """
    if not is_frozen():
        return app_base_dir()
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            return Path(local) / APP_DIR_NAME
        return Path.home() / "AppData" / "Local" / APP_DIR_NAME
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / APP_DIR_NAME


def _windows_documents_dir() -> Path | None:
    """Dossier Documents tel que Windows le connait reellement.

    Path.home()/"Documents" est faux des que le dossier est redirige (OneDrive,
    profil sur un autre disque, dossier renomme) : on demande donc son chemin a
    Windows plutot que de le deviner. Toute erreur renvoie None et l'appelant
    retombe sur une solution simple -- une exception ici empecherait
    l'application de demarrer, pour un dossier.
    """
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        import ctypes.wintypes
        import uuid

        class GUID(ctypes.Structure):
            _fields_ = [("d1", ctypes.c_uint32), ("d2", ctypes.c_uint16),
                        ("d3", ctypes.c_uint16), ("d4", ctypes.c_ubyte * 8)]

        # FOLDERID_Documents
        raw = uuid.UUID("FDD39AD0-238F-46AF-ADB4-6C85480369C7")
        guid = GUID(raw.time_low, raw.time_mid, raw.time_hi_version,
                    (ctypes.c_ubyte * 8)(*raw.bytes[8:]))
        out = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(
            ctypes.byref(guid), 0, None, ctypes.byref(out)
        ) != 0:
            return None
        try:
            return Path(out.value) if out.value else None
        finally:
            ctypes.windll.ole32.CoTaskMemFree(out)
    except Exception:
        return None


def default_projects_dir() -> Path:
    """Dossier des projets : le travail de l'utilisateur, sous Documents.

    Reste dans le depot en developpement. Le dossier est configurable depuis la
    page Parametres ; ceci n'est que la valeur par defaut.
    """
    if not is_frozen():
        return app_base_dir() / "user_projects"
    documents = _windows_documents_dir()
    if documents is None:
        candidate = Path.home() / "Documents"
        documents = candidate if candidate.is_dir() else Path.home()
    return documents / APP_DIR_NAME
