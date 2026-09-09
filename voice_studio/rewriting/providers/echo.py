"""Fournisseur d'essai, utilise UNIQUEMENT par les tests.

Il ne reecrit rien : il rend ce qu'on lui a dit de rendre. Cela permet de
tester tout le reste -- analyse, consignes, verification, variantes, cache,
annulation, sauvegarde, passage au TTS -- sans modele de plusieurs gigaoctets
et sans reseau.

Il n'est jamais propose dans l'interface : `available()` ne suffit pas, le
service ne l'instancie que si on le lui passe explicitement.
"""
from __future__ import annotations

from typing import Optional

from core.cancellation import CancelToken
from voice_studio.rewriting.providers.base import ProviderError, RewriteProvider


class EchoProvider(RewriteProvider):
    name = "echo"

    def __init__(self, replies=None, fail_with: Exception | None = None):
        self.replies = list(replies or [])
        self.fail_with = fail_with
        self.calls = []

    def available(self) -> bool:
        return True

    def describe(self) -> dict:
        return {"provider": self.name, "model": "essai"}

    def generate(self, system_prompt: str, user_prompt: str, max_words: int = 400,
                 cancel_token: Optional[CancelToken] = None, on_progress=None) -> str:
        if cancel_token is not None:
            cancel_token.check()
        self.calls.append({"system": system_prompt, "user": user_prompt,
                           "max_words": max_words})
        if self.fail_with is not None:
            raise self.fail_with
        if on_progress is not None:
            on_progress(max_words // 2, max_words)
        if not self.replies:
            raise ProviderError("Aucune réponse préparée pour ce test.")
        return self.replies.pop(0)
