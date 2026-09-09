"""Contrat commun a tous les moteurs de generation.

Trois methodes, pas une de plus : est-il utilisable, comment s'appelle-t-il,
et produis un texte. C'est ce qui permet de remplacer llama.cpp demain sans
toucher au service, a l'analyse, a la verification ni a l'interface.
"""
from __future__ import annotations

from typing import Optional

from core.cancellation import CancelToken


class ProviderError(Exception):
    """Echec de generation, formule pour l'utilisateur."""


class ProviderUnavailable(ProviderError):
    """Le moteur ne peut pas fonctionner ici (bibliotheque ou modele absent)."""


class RewriteProvider:
    """Interface d'un moteur de reecriture."""

    name = "base"

    def available(self) -> bool:
        raise NotImplementedError

    def describe(self) -> dict:
        """Ce qu'on peut dire du moteur SANS mentir : nom, modele charge,
        etat. Les valeurs inconnues sont absentes, jamais devinees."""
        return {"provider": self.name}

    def generate(self, system_prompt: str, user_prompt: str, max_words: int = 400,
                 cancel_token: Optional[CancelToken] = None,
                 on_progress=None) -> str:
        raise NotImplementedError

    def unload(self) -> None:
        """Libere la memoire. Sans effet par defaut."""
