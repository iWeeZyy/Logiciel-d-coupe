"""Orchestration du banc d'essai ZeroGPU : script -> un seul WAV, et des chiffres.

CE QUE FAIT CE MODULE, dans l'ordre : decouper le script, generer chaque
morceau sur le GPU distant, recoller les morceaux en UN fichier, mesurer.

LE RECOLLAGE EST FAIT ICI, PAS LAISSE A L'UTILISATEUR. La transcription
Faster-Whisper, les sous-titres et la video travaillent sur un fichier unique ;
livrer douze fichiers separes deplacerait le probleme au lieu de le resoudre.
Le decoupage doit rester invisible : c'est la contrainte posee, et c'est
pourquoi la jointure ajoute un silence court plutot que de coller deux
respirations bout a bout.

CE MODULE N'EST IMPORTE PAR AUCUN MODULE DU VOICE STUDIO EXISTANT. Il importe
`store` (pour savoir ou ecrire) et `transcription` (pour la transcription de
controle) mais rien ne l'importe en retour : tests/test_zerogpu.py le verifie.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from core.cancellation import CancelToken
from core.logging_setup import get_logger
from utils.errors import CancelledError
from voice_studio import zerogpu_catalogue as catalogue
from voice_studio import zerogpu_client
from voice_studio.zerogpu_client import ZeroGpuError

logger = get_logger()

# Silence insere entre deux morceaux. Assez pour que la jointure ne s'entende
# pas comme une coupure, assez court pour ne pas ralentir la parole.
JOIN_SILENCE_S = 0.18


@dataclass
class Outcome:
    wav_path: str = ""
    measure: catalogue.Measure = field(default_factory=catalogue.Measure)
    connection: Optional[zerogpu_client.Connection] = None
    chunks: list = field(default_factory=list)
    notes: list = field(default_factory=list)


def generate(script: str, params: catalogue.Params, out_wav: str,
             on_progress: Optional[Callable] = None,
             cancel_token: Optional[CancelToken] = None) -> Outcome:
    """Genere le script entier et rend un seul WAV, avec ses mesures."""
    pieces = catalogue.chunks(script)
    if not pieces:
        raise ZeroGpuError("Il n'y a pas de texte à lire.")

    def _say(event: dict) -> None:
        if on_progress is not None:
            on_progress(event)

    _say({"event": "plan", "chunks": len(pieces),
          "chars": sum(len(piece) for piece in pieces)})

    started = time.monotonic()
    client, connection = zerogpu_client.connect(cancel_token=cancel_token)
    _say({"event": "connected", "label": connection.label,
          "authenticated": connection.authenticated})

    limits = catalogue.limits()
    results = []
    for index, piece in enumerate(pieces, start=1):
        if cancel_token is not None:
            cancel_token.check()
        _say({"event": "chunk", "index": index, "total": len(pieces),
              "chars": len(piece)})

        result = _generate_with_retries(
            client, connection, piece, params, index, len(pieces),
            limits, _say, cancel_token)
        result.index, result.total = index, len(pieces)
        results.append(result)
        _say({"event": "chunk_done", "index": index, "total": len(pieces),
              "chars": len(piece), "seconds": round(result.total_s, 2),
              "queue_s": round(result.queue_s, 2), "gpu_s": round(result.gpu_s, 2)})

    audio_s, notes = _join([result.path for result in results], out_wav)
    total_s = time.monotonic() - started

    measure = catalogue.Measure(
        chars=sum(len(piece) for piece in pieces),
        words=catalogue.word_count(script),
        chunks=len(pieces),
        audio_s=audio_s,
        queue_s=sum(result.queue_s for result in results),
        gpu_s=sum(result.gpu_s for result in results),
        total_s=total_s,
    )
    _say({"event": "done", "seconds": round(total_s, 1), "duration": round(audio_s, 2)})
    return Outcome(wav_path=str(out_wav), measure=measure, connection=connection,
                   chunks=results, notes=notes)


def _generate_with_retries(client, connection, piece, params, index, total,
                           limits, say, cancel_token):
    """Retente un morceau refuse, mais pas indefiniment.

    Une file pleine ou un Space qui se reveille valent une seconde chance. Un
    quota journalier epuise n'en vaut aucune : insister ne le fait pas revenir,
    et l'utilisateur doit l'apprendre tout de suite."""
    attempts = int(limits["max_retries"]) + 1
    last: Optional[ZeroGpuError] = None
    for attempt in range(1, attempts + 1):
        try:
            return zerogpu_client.generate_chunk(
                client, connection, piece, params,
                on_status=lambda status: say({"event": "status", "index": index,
                                              "total": total, **status}),
                cancel_token=cancel_token)
        except CancelledError:
            raise
        except ZeroGpuError as error:
            if "Quota GPU épuisé" in str(error):
                raise
            last = error
            if attempt >= attempts:
                break
            pause = float(limits["retry_pause_s"]) * attempt
            logger.warning(f"ZeroGPU : morceau {index} en échec, nouvelle tentative "
                           f"dans {pause:.0f} s ({error})")
            say({"event": "retry", "index": index, "total": total,
                 "attempt": attempt, "pause_s": pause})
            _sleep(pause, cancel_token)
    raise last if last else ZeroGpuError("Échec inconnu du Space.")


def _sleep(seconds: float, cancel_token: Optional[CancelToken]) -> None:
    """Une pause qu'on peut interrompre : attendre quarante secondes sans
    pouvoir annuler serait une regression par rapport au reste de l'application."""
    deadline = time.monotonic() + max(0.0, seconds)
    while time.monotonic() < deadline:
        if cancel_token is not None:
            cancel_token.check()
        time.sleep(0.25)


def _join(paths: list, out_wav: str) -> tuple[float, list]:
    """Recolle les morceaux en un seul WAV et rend sa duree reelle.

    La duree est LUE dans le fichier produit, jamais additionnee a partir des
    morceaux : c'est la meme regle que video_service, ou la duree de la
    narration vient de ffprobe et non d'une estimation.
    """
    import numpy as np
    import soundfile as sf

    usable = [path for path in paths if path and Path(path).is_file()]
    if not usable:
        raise ZeroGpuError("Aucun morceau audio n'a été récupéré du Space.")

    notes = []
    pieces = []
    rate = 0
    for path in usable:
        data, sample_rate = sf.read(path, dtype="float32", always_2d=False)
        if data.ndim > 1:
            data = data.mean(axis=1)
        if rate == 0:
            rate = int(sample_rate)
        elif int(sample_rate) != rate:
            # Rééchantillonner demanderait librosa, absent de l'application.
            # Mieux vaut le dire que produire un fichier au tempo faux.
            raise ZeroGpuError(
                "Le Space a renvoyé des morceaux à des fréquences "
                f"d'échantillonnage différentes ({rate} Hz puis {sample_rate} Hz). "
                "L'assemblage est impossible sans rééchantillonnage.")
        if pieces:
            pieces.append(np.zeros(int(JOIN_SILENCE_S * rate), dtype="float32"))
        pieces.append(data)

    audio = np.concatenate(pieces) if pieces else np.zeros(0, dtype="float32")
    target = Path(out_wav)
    target.parent.mkdir(parents=True, exist_ok=True)

    # Ecriture en deux temps, comme le worker Chatterbox : un fichier partiel ne
    # doit jamais porter le nom d'une narration terminee.
    temporary = target.with_suffix(target.suffix + ".part")
    sf.write(str(temporary), audio, rate, subtype="PCM_16", format="WAV")
    import os

    os.replace(str(temporary), str(target))

    duration = len(audio) / rate if rate else 0.0
    if len(usable) < len(paths):
        notes.append(f"{len(paths) - len(usable)} morceau(x) manquant(s) à l'assemblage.")
    return duration, notes
