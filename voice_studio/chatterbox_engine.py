"""Chatterbox : le troisieme moteur de synthese, derriere le meme contrat.

Il expose exactement ce que les deux autres exposent -- `name`, `available()`,
`voices()`, `synthesize()`, `metadata()` -- et rien de plus : il n'y a pas de
deuxieme systeme de voix, seulement un moteur de plus dans la liste.

CE QU'IL FAIT DE DIFFERENT, ET POURQUOI.

- IL PARLE A UN AUTRE PROCESSUS. Le modele vit dans un environnement Python
  separe (chatterbox_runtime.py) : l'application n'importe jamais torch. Le
  cout est un lancement de processus par generation ; le gain est qu'aucune
  version epinglee par Chatterbox ne s'impose au reste du projet.
- IL NE SAIT PAS CHANGER LA VITESSE. Le modele n'expose aucun reglage de debit
  (verifie dans `generate()` du depot officiel). `rate` est donc IGNORE, et
  `supports_rate` le dit pour que l'interface grise le curseur au lieu de
  laisser croire qu'il agit.
- IL DECOUPE LES LONGS TEXTES. `max_new_tokens=1000` est code en dur dans la
  bibliotheque, soit environ 40 s de parole par appel : au-dela, la fin du
  texte ne serait pas prononcee, sans le moindre message.
- IL NE MONTRE AUCUNE VOIX TANT QUE RIEN N'EST INSTALLE. `voices()` renvoie une
  liste vide, jamais un nom qui echouerait a la generation.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Optional

from core.cancellation import CancelToken
from core.logging_setup import get_logger
from utils.errors import CancelledError
from voice_studio import chatterbox_catalogue as catalogue
from voice_studio import chatterbox_models as models
from voice_studio import chatterbox_runtime as runtime

logger = get_logger()

ENGINE_NAME = catalogue.ENGINE_NAME
VOICE_ID = "chatterbox-integree"

# Pause inseree entre deux morceaux d'un texte decoupe, quand l'appelant n'en
# demande pas d'autre. Assez courte pour ne pas s'entendre comme une coupure,
# assez longue pour que deux phrases ne se collent pas.
DEFAULT_CHUNK_GAP_S = 0.12


class ChatterboxEngine:
    """Moteur Chatterbox Multilingual."""

    name = ENGINE_NAME
    supports_rate = False

    def available(self) -> bool:
        """Utilisable sans rien telecharger de plus."""
        return runtime.is_ready() and models.is_installed()

    def voices(self) -> list:
        from voice_studio.tts import Voice

        if not self.available():
            return []
        language = catalogue.defaults().get("language", "fr")
        label = "Voix intégrée" + (" (français)" if language == "fr" else f" ({language})")
        return [Voice(id=VOICE_ID, label=label, language=language,
                      engine=ENGINE_NAME, quality="chatterbox")]

    def metadata(self) -> dict:
        """Ce que l'interface affiche pour expliquer l'etat du moteur."""
        model_state = models.describe()
        runtime_state = runtime.describe()
        return {
            "label": model_state["label"],
            "runtime_ready": runtime_state["ready"],
            "runtime_outdated": runtime_state["outdated"],
            "model_installed": model_state["installed"],
            "missing_files": model_state["missing"],
            "directory": model_state["directory"],
            "runtime_directory": runtime_state["directory"],
            "languages": model_state["languages"],
            "code_license": model_state["code_license"],
            "watermark": model_state["watermark"],
            "supports_rate": self.supports_rate,
            "sample_rate": catalogue.sample_rate(),
        }

    def unavailable_reason(self) -> str:
        """Pourquoi il n'est pas utilisable, en une phrase pour l'utilisateur."""
        if not runtime.is_ready():
            if runtime.is_outdated():
                return ("L'environnement Chatterbox installé ne correspond plus à la "
                        "version demandée : réinstalle-le depuis « Gérer les voix ».")
            return ("Chatterbox n'est pas installé sur cet ordinateur. "
                    "Ouvre « Gérer les voix » pour l'installer.")
        if not models.is_installed():
            missing = len(models.missing_files())
            return (f"Le modèle Chatterbox n'est pas complet ({missing} fichier(s) "
                    "manquant(s)). Ouvre « Gérer les voix » pour le télécharger.")
        return ""

    # ------------------------------------------------------------ synthese
    def synthesize(self, text: str, out_wav: str, voice_id: str = "", rate: float = 1.0,
                   volume: float = 1.0, sentence_pause_s: float = 0.0,
                   params=None, on_progress: Optional[Callable] = None,
                   cancel_token: Optional[CancelToken] = None) -> str:
        from voice_studio.tts import TtsError, clamp

        if not (text or "").strip():
            raise TtsError("Il n'y a pas de texte à lire.")
        reason = self.unavailable_reason()
        if reason:
            raise TtsError(reason)

        settings = params if isinstance(params, catalogue.Params) else catalogue.params_for(
            **(params or {}))
        settings = catalogue.clamp(settings)
        if settings.reference and not Path(settings.reference).is_file():
            raise TtsError(
                "Le fichier de voix de référence est introuvable :\n"
                f"{settings.reference}")

        pieces = catalogue.chunks(text)
        if not pieces:
            raise TtsError("Il n'y a pas de texte à lire.")

        job = {
            "model_dir": str(models.models_dir()),
            "t3_model": catalogue.model_spec().get("t3_model") or None,
            "device": "cpu",
            "chunks": pieces,
            "reference": settings.reference or "",
            "params": settings.to_dict(),
            "volume": clamp(volume, 0.0, 1.0),
            # La pause demandee entre phrases sert d'ecart entre les morceaux :
            # c'est le meme reglage, au meme endroit, que pour les autres
            # moteurs.
            "chunk_gap_s": max(0.0, float(sentence_pause_s or 0.0)) or DEFAULT_CHUNK_GAP_S,
            "out_wav": str(out_wav),
        }
        Path(out_wav).parent.mkdir(parents=True, exist_ok=True)
        if on_progress:
            # Le decompte AVANT de lancer le processus : demarrer un Python et
            # charger le modele prend du temps, et l'ecran doit pouvoir dire
            # tout de suite ce qui l'attend plutot que rester vide.
            on_progress({"event": "plan", "chunks": len(pieces),
                         "chars": sum(len(piece) for piece in pieces)})
        self._run_worker(job, on_progress=on_progress, cancel_token=cancel_token)
        if not Path(out_wav).is_file():
            raise TtsError(
                "Chatterbox s'est terminé sans produire de fichier audio. "
                "Le détail technique est dans le journal de l'application.")
        return out_wav

    def _run_worker(self, job: dict, on_progress: Optional[Callable],
                    cancel_token: Optional[CancelToken]) -> None:
        """Lance le processus, suit ses evenements, et le tue a l'annulation.

        Le tuer et non lui demander d'arreter : une generation en cours ne
        rend la main qu'a la fin d'un morceau, et laisser tourner une minute
        de calcul apres un clic sur Annuler serait mentir sur ce bouton. Le
        fichier n'etant ecrit qu'a la toute fin, rien d'incomplet ne reste.
        """
        from voice_studio.tts import TtsError

        handle, job_path = tempfile.mkstemp(prefix="chatterbox_job_", suffix=".json")
        os.close(handle)
        Path(job_path).write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")

        command = [str(runtime.python_executable()), str(runtime.worker_script()),
                   "--generate", job_path]
        creation = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        logger.info(f"Chatterbox : génération de {len(job['chunks'])} morceau(x)…")

        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, bufsize=1, creationflags=creation)
        failure = ""
        try:
            for line in process.stdout or []:
                event = self._parse(line)
                if not event:
                    continue
                if event.get("event") == "error":
                    failure = str(event.get("message") or "")
                elif on_progress:
                    on_progress(event)
                if cancel_token is not None:
                    try:
                        cancel_token.check()
                    except CancelledError:
                        process.kill()
                        raise
        finally:
            if process.stdout:
                process.stdout.close()
            code = process.wait()
            stderr = (process.stderr.read() if process.stderr else "") or ""
            if process.stderr:
                process.stderr.close()
            Path(job_path).unlink(missing_ok=True)

        if code != 0 or failure:
            if stderr.strip():
                logger.warning(f"Chatterbox (détail technique) : {stderr.strip()[-2000:]}")
            raise TtsError(failure or _explain_exit(code, stderr))

    @staticmethod
    def _parse(line: str) -> dict:
        line = (line or "").strip()
        if not line.startswith("{"):
            return {}
        try:
            data = json.loads(line)
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}


def _explain_exit(code: int, stderr: str) -> str:
    """Traduit un echec du processus en phrase utilisable.

    Les cas listes sont ceux qu'on peut reconnaitre a coup sur dans la sortie ;
    pour tout le reste on dit que le detail est dans le journal, plutot que de
    coller une trace Python a l'ecran.
    """
    lowered = (stderr or "").lower()
    if "modulenotfounderror" in lowered and "chatterbox" in lowered:
        return ("L'environnement Chatterbox est incomplet : réinstalle-le depuis "
                "« Gérer les voix ».")
    if "cuda" in lowered and "out of memory" in lowered:
        return "La carte graphique n'a plus assez de mémoire pour cette génération."
    if "killed" in lowered or code == 137:
        return ("La génération a été interrompue par le système, probablement faute de "
                "mémoire vive. Essaie un texte plus court.")
    if "no such file or directory" in lowered and "safetensors" in lowered:
        return ("Un fichier du modèle Chatterbox manque : relance le téléchargement "
                "depuis « Gérer les voix ».")
    return (f"La génération Chatterbox a échoué (code {code}). "
            "Le détail technique est dans le journal de l'application.")
