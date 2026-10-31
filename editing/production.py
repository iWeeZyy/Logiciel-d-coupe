"""Options de production d'un traitement, sans interface.

La fenetre (gui/widgets/production_options.py) et la compilation automatique
de la semaine (radar/weekly_top.py) construisent les memes reglages : les
cases, leurs valeurs par defaut et la facon d'en tirer les surcharges de
config/editing.json vivent ici, une seule fois. Deux copies finiraient par
diverger, et la compilation du dimanche ne ressemblerait plus a celle faite a
la main.
"""
from __future__ import annotations

from editing import delire, presets
from video import watermark
from video.cropper import ASPECT_PORTRAIT
from video.filter_graph import FIT_CROP, FIT_WHOLE

QUICK_MODULES = [
    ("context_detection", "Contexte"),
    ("framing", "Cadrage intelligent"),
    ("montage", "Montage auto"),
    ("captions", "Sous-titres"),
    ("metadata", "Titres et descriptions"),
    ("thumbnails", "Miniatures"),
    ("watermark", "Filigrane"),
    ("intro", "Intro ClipsOfStreams"),
    ("delire", "Délire"),
]

DEFAULT_OFF = {"delire"}


def watermark_config() -> dict:
    """La section « watermark » de config/editing.json, ou {}.

    Un fichier illisible ne doit rien empecher : le module se contente
    alors de son repli."""
    try:
        from core.config_loader import _load_editing_config

        return (_load_editing_config() or {}).get("watermark") or {}
    except Exception:                                  # pragma: no cover
        return {}


def default_watermark_choice() -> str:
    """La premiere chaine du catalogue, comme le menu de la fenetre."""
    entries = watermark.choices(watermark_config())
    return str(entries[0]["key"]) if entries else ""


def keeps_whole_image(aspect: str, fit_mode: str) -> bool:
    """Vrai quand aucun recadrage n'aura lieu, quel que soit le format."""
    return aspect != ASPECT_PORTRAIT or fit_mode == FIT_WHOLE


def build_overrides(modules: dict, *, aspect: str, fit_mode: str, watermark_choice: str,
                    delire_level: str, theme: str, preset: str) -> dict:
    """Surcharges des modules pour CE traitement (fonction pure).

    Le cadrage intelligent est force a l'arret quand l'image entiere est
    gardee : il n'y a rien a cadrer. Filigrane et delire portent plus qu'un
    interrupteur (quelle chaine signe, quel cran, quel theme). Le style de
    montage ajoute ce que les cases ne savent pas exprimer (zooms), sans
    jamais contredire la case du delire.
    """
    overrides = {key: bool(value) for key, value in modules.items()}
    if keeps_whole_image(aspect, fit_mode):
        overrides["framing"] = False
    if watermark_choice:
        overrides["watermark"] = {"enabled": bool(overrides.get("watermark")),
                                  "choice": watermark_choice}
    overrides["delire"] = {"enabled": bool(overrides.get("delire")),
                           "level": delire_level, "theme": theme}
    for module, override in presets.editing_overrides(preset).items():
        if module == "delire":
            continue
        overrides[module] = override
    return overrides


def default_production(**changes) -> dict:
    """Les reglages d'une fenetre qu'on n'a pas touchee : 9:16, recadrage
    sur le sujet, fond flou, toutes les cases sauf le Delire, premier
    filigrane, style de montage par defaut. `changes` remplace une partie :
    aspect, fit_mode, fill_mode, modules ({cle: bool}), watermark, preset,
    delire_level, theme, subtitle_style.

    Renvoie le dictionnaire attendu par radar/top_compilation.produce_clip.
    """
    aspect = changes.get("aspect") or ASPECT_PORTRAIT
    fit_mode = changes.get("fit_mode") or FIT_CROP
    preset = changes.get("preset") or presets.DEFAULT_KEY
    modules = {key: key not in DEFAULT_OFF for key, _label in QUICK_MODULES}
    modules.update({k: bool(v) for k, v in (changes.get("modules") or {}).items()
                    if k in modules})
    choice = changes.get("watermark")
    overrides = build_overrides(
        modules, aspect=aspect, fit_mode=fit_mode,
        watermark_choice=default_watermark_choice() if choice is None else str(choice),
        delire_level=changes.get("delire_level") or delire.DEFAULT_LEVEL,
        theme=changes.get("theme") or "", preset=preset)
    return {"aspect": aspect, "fill_mode": changes.get("fill_mode") or "flou",
            "fit_mode": fit_mode,
            "subtitle_style": changes.get("subtitle_style")
            or presets.get(preset).subtitle_style or "",
            "editing_overrides": overrides}
