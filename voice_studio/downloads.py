"""Telechargement de fichiers volumineux, partage par les gestionnaires de
modeles (voix Piper, modeles de langue).

Ecrit ici plutot qu'en double : les deux gestionnaires ont exactement les memes
exigences -- reprise apres coupure, verification de la taille recue, controle
de l'espace disque avant d'ecrire, annulation, et messages comprehensibles. Une
deuxieme implementation aurait fini par ne plus se comporter pareil.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable, Optional

from core.cancellation import CancelToken
from utils.errors import CancelledError

# Marge exigee en plus de la taille annoncee : un disque rempli a l'octet pres
# fait echouer l'ecriture au dernier moment.
FREE_SPACE_MARGIN = 50 * 1024 * 1024


class DownloadError(Exception):
    """Echec de telechargement, formule pour l'utilisateur."""


def free_space(directory: Path) -> int:
    try:
        return shutil.disk_usage(directory).free
    except OSError:                                    # pragma: no cover - chemin illisible
        return 0


def download_file(url: str, target: Path, on_progress: Optional[Callable] = None,
                   cancel_token: Optional[CancelToken] = None, opener=None,
                   allow_resume: bool = True) -> None:
    """Telecharge une adresse vers un fichier, en reprenant si possible.

    L'ecriture se fait dans `<cible>.part` : tant que le transfert n'est pas
    fini, rien ne porte le nom du fichier final, donc rien ne peut etre pris
    pour une voix installee.
    """
    import urllib.error
    import urllib.request

    opener = opener or urllib.request.urlopen
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".part")
    already = partial.stat().st_size if (allow_resume and partial.is_file()) else 0
    if not allow_resume and partial.is_file():
        partial.unlink()

    request = urllib.request.Request(url, headers={"User-Agent": "ClipFarming"})
    if already:
        request.add_header("Range", f"bytes={already}-")

    try:
        response = opener(request)
    except urllib.error.HTTPError as error:
        if already and error.code in (416, 400):
            # Le serveur refuse la reprise : on repart de zero plutot que de
            # rester bloque sur un fichier partiel.
            partial.unlink(missing_ok=True)
            return download_file(url, target, on_progress, cancel_token, opener,
                                  allow_resume=False)
        raise DownloadError(explain_http(error, url)) from error
    except urllib.error.URLError as error:
        raise DownloadError(
            "Impossible de joindre le serveur des voix. Vérifie ta connexion internet, "
            f"puis réessaie. Détail : {error.reason}"
        ) from error

    resuming = getattr(response, "status", 200) == 206
    if already and not resuming:
        already = 0                                    # le serveur renvoie tout depuis le debut

    total = None
    length = response.headers.get("Content-Length") if hasattr(response, "headers") else None
    if length:
        total = int(length) + (already if resuming else 0)

    if total and free_space(target.parent) < (total - already) + FREE_SPACE_MARGIN:
        raise DownloadError(
            f"Espace disque insuffisant : il faut environ {total / 1_000_000:.0f} Mo "
            f"libres dans {target.parent}."
        )

    mode = "ab" if (already and resuming) else "wb"
    done = already if resuming else 0
    try:
        with open(partial, mode) as handle:
            while True:
                if cancel_token is not None:
                    cancel_token.check()
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
                done += len(chunk)
                if on_progress:
                    on_progress(done / total if total else None, done, total)
    except CancelledError:
        # Le .part est conserve : la reprise repartira de la.
        raise
    finally:
        close = getattr(response, "close", None)
        if close:
            close()

    if total and partial.stat().st_size != total:
        partial.unlink(missing_ok=True)
        raise DownloadError(
            "Le fichier reçu est incomplet (transfert interrompu). Relance le "
            "téléchargement : il reprendra depuis le début."
        )
    partial.replace(target)


def explain_http(error, url: str) -> str:
    if getattr(error, "code", None) == 404:
        return ("Ce fichier n'existe plus à cette adresse (erreur 404). "
                "Le catalogue, dans le dossier config de l'application, doit "
                "être mis à jour.")
    if getattr(error, "code", None) in (401, 403):
        return "Le serveur refuse le téléchargement de cette voix."
    return f"Téléchargement refusé par le serveur ({getattr(error, 'code', '?')})."


