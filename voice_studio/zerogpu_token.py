"""Le jeton Hugging Face : ou il vit, et surtout ou il ne vit pas.

MEME MECANISME QUE LA CLE YOUTUBE (youtube/search.py), volontairement, plutot
qu'un second systeme de secrets : variable d'environnement d'abord, sinon un
fichier texte dans le dossier de donnees de l'utilisateur.

CE QU'IL NE FAUT JAMAIS FAIRE, et que ce module rend inutile : ecrire le jeton
dans le code, dans config/, ou dans l'executable. Le dossier de donnees
utilisateur n'est ni versionne, ni empaquete par PyInstaller, ni emporte par
une desinstallation.

Le jeton sert a deux choses : debiter le quota GPU sur TON compte plutot que
sur le quota anonyme (deux minutes par jour contre cinq), et obtenir de
meilleures limites de requetes sur un Space public.
"""
from __future__ import annotations

import os

from core.paths import user_data_dir

ENV_VAR = "HF_TOKEN"
TOKEN_FILE_NAME = "huggingface_token.txt"


def token_file() -> "os.PathLike":
    return user_data_dir() / TOKEN_FILE_NAME


def load_token() -> str:
    """Le jeton, ou une chaine vide. Ne leve jamais.

    Sans jeton, l'appel reste possible : Hugging Face applique alors le quota
    anonyme. C'est degrade, pas casse, et l'ecran le dit clairement plutot que
    de refuser de fonctionner.
    """
    from_env = os.environ.get(ENV_VAR, "").strip()
    if from_env:
        return from_env
    path = token_file()
    try:
        if path.is_file():
            # utf-8-sig comme pour la cle YouTube : Windows PowerShell 5.1
            # ecrit un BOM invisible que .strip() ne retire pas, et qui
            # corrompt silencieusement un secret.
            return path.read_text(encoding="utf-8-sig").strip()
    except OSError:
        return ""
    return ""


def describe() -> str:
    """D'ou vient le jeton, pour l'afficher sans jamais l'afficher lui-meme."""
    if os.environ.get(ENV_VAR, "").strip():
        return f"variable d'environnement {ENV_VAR}"
    if load_token():
        return f"fichier {token_file()}"
    return ""


def missing_token_help() -> str:
    return ("Aucun jeton Hugging Face trouvé : le quota anonyme s'applique "
            "(2 minutes de GPU par jour au lieu de 5).\n\n"
            "Pour utiliser ton compte, au choix :\n"
            f"• définir la variable d'environnement {ENV_VAR} ;\n"
            "• créer un fichier texte contenant uniquement le jeton, à cet "
            f"emplacement précis :\n{token_file()}\n\n"
            "Le jeton se crée sur https://huggingface.co/settings/tokens, "
            "en lecture seule (READ) : il n'a besoin d'aucun autre droit.")
