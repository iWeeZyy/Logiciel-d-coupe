"""Surcharges des modules de montage pour UN traitement, sans interface.

Utilise par la fenetre d'analyse (gui/controller.py) et par la compilation
des plus vus (radar/top_compilation.py), y compris sans ecran : ce module
ne depend pas de Qt.
"""
from __future__ import annotations


def _deep_copy_block(value):
    """Copie un bloc de configuration sans jamais partager un sous-dictionnaire.

    Une copie de surface suffisait tant qu'un module etait plat. Les styles de
    montage surchargent montage.dynamic_zoom, donc un niveau plus bas : sans
    copie profonde, la surcharge d'un run ecrirait dans la configuration
    chargee et contaminerait le run suivant.
    """
    if isinstance(value, dict):
        return {k: _deep_copy_block(v) for k, v in value.items()}
    return value


def _deep_update(target: dict, override: dict) -> None:
    """Fusionne `override` dans `target` en DESCENDANT dans les sous-blocs.

    Un `dict.update` remplacerait montage.dynamic_zoom en entier : demander un
    zoom plus ample ferait perdre du meme coup son attaque, sa tenue et son
    relachement. On ne remplace donc que les feuilles reellement fournies.
    """
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_update(target[key], value)
        else:
            target[key] = value


def apply_editing_overrides(settings, editing_overrides) -> None:
    """Modules choisis pour CE traitement, par-dessus config/editing.json.
    Partage par start_analysis et la compilation des plus vus du Radar."""
    if not editing_overrides:
        return
    editing = {k: _deep_copy_block(v) for k, v in settings.editing.items()}
    for module, override in editing_overrides.items():
        if not isinstance(editing.get(module), dict):
            continue
        if isinstance(override, dict):
            # Un module peut porter plus que son interrupteur : le
            # filigrane doit dire AUSSI quelle chaine signe la video.
            # Les cles inconnues du module sont ignorees par lui, donc
            # cette fusion ne peut rien casser de ce qu'il lit deja.
            _deep_update(editing[module], override)
        else:
            editing[module]["enabled"] = bool(override)
    settings.editing = editing
