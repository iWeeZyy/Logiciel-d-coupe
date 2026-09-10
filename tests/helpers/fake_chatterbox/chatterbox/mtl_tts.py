"""Modele factice : meme interface que ChatterboxMultilingualTTS, son previsible."""
from __future__ import annotations

import json
import os

import numpy as np

SUPPORTED_LANGUAGES = {"fr": "French", "en": "English"}

# Chaque appel est enregistre ici quand CHATTERBOX_FAKE_LOG designe un fichier :
# c'est ainsi que les tests verifient ce que le worker a reellement demande.
LOG = os.environ.get("CHATTERBOX_FAKE_LOG", "")

SR = 24000
SECONDS_PER_CHAR = 0.02


class ChatterboxMultilingualTTS:
    def __init__(self, sr: int = SR):
        self.sr = sr

    @classmethod
    def from_local(cls, ckpt_dir, device, t3_model=None):
        _log({"call": "from_local", "ckpt_dir": str(ckpt_dir), "device": str(device),
              "t3_model": t3_model})
        if os.environ.get("CHATTERBOX_FAKE_FAIL") == "load":
            raise RuntimeError("modèle factice : chargement refusé")
        return cls()

    def generate(self, text, language_id, audio_prompt_path=None, exaggeration=0.5,
                 cfg_weight=0.5, temperature=0.8, **extra):
        _log({"call": "generate", "text": text, "language_id": language_id,
              "audio_prompt_path": audio_prompt_path, "exaggeration": exaggeration,
              "cfg_weight": cfg_weight, "temperature": temperature})
        if os.environ.get("CHATTERBOX_FAKE_FAIL") == "generate":
            raise RuntimeError("modèle factice : génération refusée")
        samples = max(1, int(len(text) * SECONDS_PER_CHAR * SR))
        t = np.arange(samples, dtype=np.float32) / SR
        wav = (0.3 * np.sin(2 * np.pi * 220.0 * t)).astype(np.float32)
        return wav.reshape(1, -1)


def _log(entry: dict) -> None:
    if not LOG:
        return
    with open(LOG, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
