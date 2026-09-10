"""Programme execute DANS l'environnement Chatterbox, jamais dans l'application.

Il n'importe rien du projet : il ne voit ni Qt, ni les autres modules, et
l'application ne voit ni torch ni chatterbox. C'est tout l'interet du
processus separe -- les versions exactes qu'exige Chatterbox (torch==2.6.0,
transformers==5.2.0, diffusers==0.29.0) restent chez lui, et une
incompatibilite de sa part ne peut pas empecher Faster-Whisper, Piper ou
l'interface de fonctionner.

Il parle en JSON, une ligne par evenement sur la sortie standard, pour que
l'appelant puisse afficher une progression reelle et annuler quand il veut.

Deux modes :
  --selftest            dit ce qui est installe et sur quel processeur
  --generate <job.json> genere la narration et ecrit un WAV
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import traceback


def emit(event: str, **fields) -> None:
    print(json.dumps({"event": event, **fields}, ensure_ascii=False), flush=True)


def selftest() -> int:
    import torch

    try:
        from chatterbox.mtl_tts import SUPPORTED_LANGUAGES
        import chatterbox

        version = getattr(chatterbox, "__version__", "?")
        languages = sorted(SUPPORTED_LANGUAGES)
    except Exception as error:                     # pragma: no cover - runtime incomplet
        emit("error", message=f"chatterbox indisponible : {error}")
        return 2

    emit("selftest", torch=torch.__version__,
         chatterbox=version,
         cuda=bool(torch.cuda.is_available()),
         device="cuda" if torch.cuda.is_available() else "cpu",
         languages=languages,
         threads=int(torch.get_num_threads()))
    return 0


def _normalise(wav, gain: float):
    """Volume demande, puis garde-fou contre l'ecretage.

    Aucune compression : on met a l'echelle, c'est tout. Un traitement plus
    agressif « pour faire propre » s'entendrait sur une voix de synthese.
    """
    import numpy as np

    wav = np.asarray(wav, dtype=np.float32).reshape(-1)
    if gain and abs(gain - 1.0) > 1e-3:
        wav = wav * float(gain)
    peak = float(np.max(np.abs(wav))) if wav.size else 0.0
    if peak > 0.97:
        wav = wav * (0.97 / peak)
    return wav


def _trim_silence(wav, sample_rate: int, threshold: float = 0.005,
                  keep_s: float = 0.08):
    """Enleve le silence en trop AU DEBUT ET A LA FIN, et rien d'autre.

    Les silences internes portent le rythme de la narration : les toucher
    reviendrait a rejouer le texte autrement que ce qui a ete genere.
    """
    import numpy as np

    if wav.size == 0:
        return wav
    loud = np.where(np.abs(wav) > threshold)[0]
    if loud.size == 0:
        return wav
    keep = int(keep_s * sample_rate)
    start = max(0, int(loud[0]) - keep)
    end = min(wav.size, int(loud[-1]) + keep)
    return wav[start:end]


def _seed_everything(seed: int, device: str) -> None:
    """Le modele n'expose PAS de graine : on la pose sur torch, numpy et
    random, exactement comme le fait l'application de reference du depot."""
    import random

    import numpy as np

    random.seed(seed)
    np.random.seed(seed % (2 ** 32))
    try:
        import torch
    except ImportError:                            # pragma: no cover - runtime incomplet
        return
    torch.manual_seed(seed)
    if device == "cuda" and torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def _to_numpy(wav):
    """Le modele rend un tenseur torch ; on n'exige pas torch pour autant.

    Utile a l'essai (un modele factice rend un tableau numpy) et sans risque :
    on lit ce qu'on a recu au lieu de supposer son type.
    """
    import numpy as np

    if hasattr(wav, "detach"):
        wav = wav.detach().cpu().numpy()
    array = np.asarray(wav, dtype=np.float32)
    return array.reshape(-1)


def generate(job: dict) -> int:
    import numpy as np
    from chatterbox.mtl_tts import ChatterboxMultilingualTTS

    device = job.get("device") or "cpu"
    params = job.get("params") or {}
    seed = int(params.get("seed") or 0)
    if seed:
        _seed_everything(seed, device)

    started = time.time()
    emit("loading", device=device, model_dir=job.get("model_dir", ""))
    model = ChatterboxMultilingualTTS.from_local(
        job["model_dir"], device, t3_model=job.get("t3_model") or None)
    emit("loaded", seconds=round(time.time() - started, 1))

    chunks = job.get("chunks") or []
    reference = job.get("reference") or None
    gap = float(job.get("chunk_gap_s") or 0.0)
    sample_rate = int(getattr(model, "sr", 24000))
    silence = np.zeros(int(gap * sample_rate), dtype=np.float32) if gap > 0 else None

    pieces = []
    for index, chunk in enumerate(chunks, start=1):
        emit("chunk", index=index, total=len(chunks), chars=len(chunk))
        chunk_started = time.time()
        wav = model.generate(
            chunk,
            language_id=params.get("language") or "fr",
            audio_prompt_path=reference,
            exaggeration=float(params.get("exaggeration", 0.5)),
            cfg_weight=float(params.get("cfg_weight", 0.5)),
            temperature=float(params.get("temperature", 0.8)),
        )
        piece = _to_numpy(wav)
        # La FIN du morceau, pas seulement son debut : c'est elle qui donne
        # une vitesse mesuree, donc une duree restante qui vaut quelque chose.
        emit("chunk_done", index=index, total=len(chunks), chars=len(chunk),
             seconds=round(time.time() - chunk_started, 2))
        if pieces and silence is not None:
            pieces.append(silence)
        pieces.append(piece)

    if not pieces:
        emit("error", message="Aucun texte à lire.")
        return 3

    audio = np.concatenate(pieces)
    audio = _trim_silence(audio, sample_rate)
    audio = _normalise(audio, float(job.get("volume", 1.0)))

    # Ecriture en deux temps : un fichier partiel ne doit jamais se retrouver
    # dans le cache sous le nom d'une narration terminee.
    import soundfile as sf

    out_path = job["out_wav"]
    temporary = out_path + ".part"
    # `format` explicite : le fichier temporaire s'appelle « ....wav.part »,
    # et soundfile, qui devine le format d'apres l'extension, refuse « .part ».
    sf.write(temporary, audio, sample_rate, subtype="PCM_16", format="WAV")
    import os

    os.replace(temporary, out_path)
    emit("done", seconds=round(time.time() - started, 1),
         duration=round(len(audio) / sample_rate, 2), sample_rate=sample_rate,
         path=out_path)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Chatterbox worker (ClipFarming)")
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--generate", metavar="JOB_JSON")
    args = parser.parse_args(argv)

    try:
        if args.selftest:
            return selftest()
        if args.generate:
            with open(args.generate, "r", encoding="utf-8") as handle:
                return generate(json.load(handle))
    except KeyboardInterrupt:                      # annulation par le parent
        emit("cancelled")
        return 130
    except Exception as error:
        # La trace complete part sur la sortie d'erreur, que l'appelant met
        # dans le journal ; l'utilisateur, lui, ne verra que `message`.
        traceback.print_exc(file=sys.stderr)
        emit("error", message=f"{type(error).__name__} : {error}")
        return 1

    parser.print_help()
    return 64


if __name__ == "__main__":
    raise SystemExit(main())
