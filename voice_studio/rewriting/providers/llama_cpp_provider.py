"""Generation locale par llama.cpp (llama-cpp-python).

Aucun appel reseau pendant la generation : le modele est un fichier GGUF sur le
disque, telecharge une fois, volontairement, depuis le gestionnaire de modeles.

UN SEUL MODELE EN MEMOIRE. Le modele reste charge entre deux generations --
le charger coute plusieurs secondes et plusieurs gigaoctets -- mais changer de
modele libere le precedent avant d'ouvrir le suivant. Whisper, lui, n'est pas
concerne : il vit dans son propre module et n'est pas charge en meme temps.

L'annulation est verifiee entre les morceaux de texte produits : c'est le seul
moment ou l'on repasse pendant une generation.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from core.cancellation import CancelToken
from core.logging_setup import get_logger
from utils.errors import CancelledError
from voice_studio.rewriting.providers.base import ProviderError, ProviderUnavailable, RewriteProvider

logger = get_logger()

# Fenetre de contexte : un transcript de dix minutes fait environ 1 500 mots,
# soit largement moins que 8 192 jetons avec la consigne. Au-dela, le modele
# tronquerait le texte source sans le dire.
DEFAULT_CONTEXT = 8192
# Marge de jetons par mot demande : le francais compte environ 1,6 jeton par
# mot, et on laisse de la place plutot que de couper une phrase.
TOKENS_PER_WORD = 2.2


class LlamaCppProvider(RewriteProvider):
    """Un modele GGUF, execute localement."""

    name = "llama.cpp"

    # Le modele est garde au niveau de la CLASSE : deux pages ou deux
    # generations successives ne doivent pas ouvrir deux fois le meme fichier
    # de plusieurs gigaoctets.
    _shared_model = None
    _shared_path = ""

    def __init__(self, model_path: str = "", context: int = DEFAULT_CONTEXT,
                 threads: int | None = None, gpu_layers: int = 0):
        self.model_path = str(model_path or "")
        self.context = context
        self.threads = threads
        self.gpu_layers = gpu_layers

    # ------------------------------------------------------------ presence
    @staticmethod
    def library_available() -> bool:
        try:
            import llama_cpp  # noqa: F401
        except Exception:
            return False
        return True

    def available(self) -> bool:
        return self.library_available() and bool(self.model_path) and Path(self.model_path).is_file()

    def describe(self) -> dict:
        info = {"provider": self.name, "library": self.library_available()}
        if self.model_path:
            info["model"] = Path(self.model_path).name
            info["loaded"] = (LlamaCppProvider._shared_path == self.model_path
                              and LlamaCppProvider._shared_model is not None)
        return info

    # ------------------------------------------------------------ modele
    def _model(self):
        if not self.library_available():
            raise ProviderUnavailable(
                "Le moteur de réécriture n'est pas installé dans cette version de "
                "l'application.")
        if not self.model_path or not Path(self.model_path).is_file():
            raise ProviderUnavailable(
                "Aucun modèle de réécriture n'est installé. Ouvre « Gérer les modèles » "
                "pour en télécharger un.")

        if (LlamaCppProvider._shared_model is not None
                and LlamaCppProvider._shared_path == self.model_path):
            return LlamaCppProvider._shared_model

        self.unload()
        from llama_cpp import Llama

        logger.info(f"Chargement du modele de reecriture : {self.model_path}")
        try:
            model = Llama(
                model_path=self.model_path,
                n_ctx=self.context,
                n_threads=self.threads,
                n_gpu_layers=self.gpu_layers,
                verbose=False,
            )
        except MemoryError as error:
            raise ProviderError(
                "Mémoire insuffisante pour charger ce modèle. Essaie le modèle léger, "
                "ou ferme d'autres applications."
            ) from error
        except Exception as error:
            raise ProviderError(
                "Ce modèle n'a pas pu être chargé : le fichier est peut-être incomplet "
                "ou abîmé. Supprime-le puis réinstalle-le depuis « Gérer les modèles ». "
                f"Détail : {type(error).__name__}"
            ) from error

        LlamaCppProvider._shared_model = model
        LlamaCppProvider._shared_path = self.model_path
        return model

    def unload(self) -> None:
        if LlamaCppProvider._shared_model is not None:
            logger.info("Modele de reecriture libere.")
        LlamaCppProvider._shared_model = None
        LlamaCppProvider._shared_path = ""

    # --------------------------------------------------------- generation
    def generate(self, system_prompt: str, user_prompt: str, max_words: int = 400,
                 cancel_token: Optional[CancelToken] = None, on_progress=None) -> str:
        model = self._model()
        max_tokens = int(max(64, max_words * TOKENS_PER_WORD))
        pieces: list[str] = []
        try:
            stream = model.create_chat_completion(
                messages=[{"role": "system", "content": system_prompt},
                          {"role": "user", "content": user_prompt}],
                max_tokens=max_tokens,
                temperature=0.8,
                top_p=0.95,
                repeat_penalty=1.1,
                stream=True,
            )
            for chunk in stream:
                if cancel_token is not None:
                    cancel_token.check()
                delta = (chunk.get("choices") or [{}])[0].get("delta", {})
                piece = delta.get("content") or ""
                if piece:
                    pieces.append(piece)
                    if on_progress is not None:
                        on_progress(len("".join(pieces).split()), max_words)
        except CancelledError:
            raise
        except ProviderError:
            raise
        except Exception as error:
            raise ProviderError(
                "La génération s'est interrompue. "
                f"Détail : {type(error).__name__} — {str(error)[:200]}"
            ) from error

        text = "".join(pieces).strip()
        if not text:
            raise ProviderError(
                "Le modèle n'a produit aucun texte. Réessaie, ou choisis un autre modèle.")
        return text
