"""Cache disque pour la transcription : evite de retranscrire la meme video si
on relance juste avec --clip-duration ou --nb-clips differents.

Cle de cache = hash(chemin absolu + taille + date de modif + modele + langue).
Volontairement pas un hash du contenu complet du fichier : sur une video de
plusieurs Go ca prendrait aussi longtemps que l'extraction audio elle-meme,
pour un gain marginal (taille+mtime suffit a detecter un fichier different
dans l'usage normal de cet outil).
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from core.models import Transcript
from core.paths import app_base_dir

CACHE_DIR = app_base_dir() / ".cache"


def _cache_key(video_path: str, model_name: str, language: str | None) -> str:
    st = os.stat(video_path)
    raw = f"{os.path.abspath(video_path)}|{st.st_size}|{st.st_mtime}|{model_name}|{language or 'auto'}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def load(video_path: str, model_name: str, language: str | None) -> Transcript | None:
    CACHE_DIR.mkdir(exist_ok=True)
    key = _cache_key(video_path, model_name, language)
    path = CACHE_DIR / f"{key}.json"
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return Transcript.from_dict(data)
    except (json.JSONDecodeError, KeyError):
        # Cache corrompu -- on ignore plutot que de faire planter le pipeline.
        return None


def save(video_path: str, model_name: str, language: str | None, transcript: Transcript) -> None:
    CACHE_DIR.mkdir(exist_ok=True)
    key = _cache_key(video_path, model_name, language)
    path = CACHE_DIR / f"{key}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(transcript.to_dict(), f, ensure_ascii=False, indent=2)
