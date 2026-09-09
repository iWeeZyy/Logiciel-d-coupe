"""Persistance des preferences GUI (gui_settings.json) -- distinct de
config/settings.json (poids/parametres du moteur, partages avec la CLI).
Ce fichier ne contient que des choix d'interface : modele par defaut, style
de sous-titres par defaut, peripherique prefere, dossier des projets.

Ecrit sous user_data_dir() (%LOCALAPPDATA% en .exe) et non a cote du binaire :
Program Files est protege en ecriture, et une desinstallation ne doit pas
emporter les reglages avec elle.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.paths import user_data_dir

GUI_SETTINGS_PATH = user_data_dir() / "gui_settings.json"

_DEFAULTS = {
    "default_model": "small",
    # None -> le style par defaut de config/subtitles.json (source unique).
    "default_subtitle_style": None,
    "default_device": "auto",
    "projects_dir": None,  # None -> projects/store.DEFAULT_PROJECTS_DIR
    # None -> user_data_dir()/clips. Deplacable : un clip pese quelques dizaines
    # de megaoctets et ils s'accumulent, le disque systeme n'est pas toujours le
    # bon endroit pour les garder.
    "clips_dir": None,
}


def load() -> dict:
    if not GUI_SETTINGS_PATH.exists():
        return dict(_DEFAULTS)
    try:
        with open(GUI_SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        merged = dict(_DEFAULTS)
        merged.update(data)
        return merged
    except (json.JSONDecodeError, OSError):
        return dict(_DEFAULTS)


def save(values: dict) -> None:
    GUI_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    merged = load()
    merged.update(values)
    with open(GUI_SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)


def get(key: str) -> Any:
    return load().get(key, _DEFAULTS.get(key))


def projects_dir() -> Path:
    from projects.store import DEFAULT_PROJECTS_DIR

    value = get("projects_dir")
    return Path(value) if value else DEFAULT_PROJECTS_DIR


def clips_dir() -> Path:
    """Ou sont gardes les clips telecharges depuis le Radar."""
    from core.paths import user_data_dir

    value = get("clips_dir")
    return Path(value) if value else user_data_dir() / "clips"
