"""Identifiants d'application des plateformes sociales.

Meme convention que Twitch et YouTube dans ce projet : variables
d'environnement d'abord, puis un fichier texte a un emplacement precis, que les
messages NOMMENT -- deviner cet emplacement a deja fait perdre du temps ici.

Ce sont les identifiants de l'APPLICATION developpeur, pas ceux d'un compte.
Le jeton d'un compte, lui, va dans le coffre de Windows (publishing/tokens.py).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from core.paths import user_data_dir

FILES = {
    "instagram": "instagram_credentials.txt",
    "tiktok": "tiktok_credentials.txt",
}

ENV_VARS = {
    "instagram": ("INSTAGRAM_APP_ID", "INSTAGRAM_APP_SECRET"),
    "tiktok": ("TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET"),
}


@dataclass(frozen=True)
class AppCredentials:
    client_id: str = ""
    client_secret: str = ""

    @property
    def complete(self) -> bool:
        return bool(self.client_id and self.client_secret)


def credentials_path(platform: str) -> Path:
    return user_data_dir() / FILES.get(platform, f"{platform}_credentials.txt")


def load(platform: str) -> AppCredentials:
    """Identifiants de l'application, vides si rien n'est configure.

    Ne leve jamais : une absence d'identifiants est un etat normal -- la
    publication directe est optionnelle, l'export manuel fonctionne sans.
    """
    id_var, secret_var = ENV_VARS.get(platform, ("", ""))
    client_id = os.environ.get(id_var, "").strip() if id_var else ""
    client_secret = os.environ.get(secret_var, "").strip() if secret_var else ""
    if client_id and client_secret:
        return AppCredentials(client_id, client_secret)

    path = credentials_path(platform)
    if path.exists():
        try:
            # utf-8-sig : la console PowerShell de Windows ecrit un BOM invisible
            # que strip() ne retire pas. Piege deja rencontre avec la cle YouTube.
            lines = [line.strip() for line in path.read_text(encoding="utf-8-sig").splitlines()
                     if line.strip() and not line.strip().startswith("#")]
        except OSError:
            return AppCredentials()
        if len(lines) >= 2:
            return AppCredentials(lines[0], lines[1])
    return AppCredentials()
