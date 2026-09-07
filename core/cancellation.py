"""Jeton d'annulation cooperatif, partage entre le thread GUI (qui l'appelle
.cancel()) et le thread de traitement (qui appelle .check() entre les etapes).
Ni Qt ni asyncio ici : juste un threading.Event, reutilisable par la CLI comme
par la GUI sans dependance croisee."""
from __future__ import annotations

import threading

from utils.errors import CancelledError


class CancelToken:
    def __init__(self):
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def check(self) -> None:
        if self._event.is_set():
            raise CancelledError("Analyse annulee.")
