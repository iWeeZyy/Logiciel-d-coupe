"""Chargement et fusion de la configuration : config/*.json + overrides CLI.

Un seul objet Settings est construit ici et transmis a pipeline.run() -- aucun
module ne relit les fichiers JSON lui-meme, pour eviter des lectures/validations
dupliquees et pour que --clip-duration etc. gagnent toujours sur les JSON.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from core.paths import app_base_dir
from utils.errors import ConfigError

CONFIG_DIR = app_base_dir() / "config"


def _load_json(name: str) -> dict:
    path = CONFIG_DIR / name
    if not path.exists():
        raise ConfigError(f"Fichier de configuration manquant : {path}")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise ConfigError(f"JSON invalide dans {path} : {e}") from e


# Interrupteurs de production : nom du module d'edition -> champ de Settings.
_MODULE_SWITCHES = {
    "framing": "smart_framing",
    "montage": "auto_montage",
    "captions": "subtitles_enabled",
    "watermark": "watermark_enabled",
}


@dataclass
class Settings:
    # Options CLI
    input: str = ""
    output: str = "output"
    clip_duration: int = 45
    nb_clips: int = 5
    language: Optional[str] = None
    pre_roll: int = 5
    post_roll: int = 3
    min_gap: int = 20
    subtitle_style: str = "progressive"
    model: str = "small"
    device: str = "auto"
    overwrite: bool = False
    no_cache: bool = False
    debug_scores: bool = False

    # Options de production, choisies avant de lancer un traitement. Elles ne
    # remplacent pas config/editing.json : elles ne peuvent que DESACTIVER un
    # module deja actif. Un module coupe dans la configuration ne se rallume pas
    # en cochant une case -- la configuration reste la source, la case est un
    # interrupteur par-dessus.
    subtitles_enabled: bool = True
    aspect_ratio: str = "9:16"
    smart_framing: bool = True
    auto_montage: bool = True
    watermark_enabled: bool = True

    # Chargés depuis settings.json
    weights: dict = field(default_factory=dict)
    scoring_params: dict = field(default_factory=dict)
    hook_detection: dict = field(default_factory=dict)
    audio_analysis: dict = field(default_factory=dict)
    face_detection: dict = field(default_factory=dict)
    export: dict = field(default_factory=dict)

    # Chargés depuis hooks_keywords.json / subtitles.json / editing.json
    keywords_config: dict = field(default_factory=dict)
    subtitles_config: dict = field(default_factory=dict)
    editing: dict = field(default_factory=dict)

    def editing_module(self, name: str) -> dict:
        """Bloc de config d'un module d'edition automatique (editing.json).
        Renvoie un dict vide si le bloc n'existe pas -- un module dont la
        config est absente est simplement considere comme desactive."""
        block = self.editing.get(name, {})
        return block if isinstance(block, dict) else {}

    def editing_module_enabled(self, name: str) -> bool:
        """Un module d'edition est-il actif pour CE traitement ?

        Point de passage unique : la configuration d'abord, puis l'interrupteur
        choisi par l'utilisateur. Le faire ici plutot qu'a chaque appel evite
        qu'un endroit du pipeline honore la case et un autre l'ignore.
        """
        if not self.editing_module(name).get("enabled", False):
            return False
        switch = _MODULE_SWITCHES.get(name)
        return True if switch is None else bool(getattr(self, switch, True))

    def target_size(self) -> tuple[int, int]:
        """Definition de sortie correspondant au format demande."""
        from video.cropper import target_size

        return target_size(self.aspect_ratio)

    def max_clip_duration(self) -> float:
        """Duree maximale autorisee pour un clip, marge de depassement comprise.
        Une seule definition, reutilisee par selector.py et par la detection de
        contexte -- deux plafonds concurrents finiraient par diverger."""
        overshoot = self.hook_detection.get("max_overshoot_ratio", 0.2)
        return float(self.clip_duration) * (1.0 + float(overshoot))

    def subtitle_style_params(self) -> dict:
        styles = self.subtitles_config.get("styles", {})
        if self.subtitle_style not in styles:
            available = ", ".join(styles.keys())
            raise ConfigError(
                f"Style de sous-titres inconnu : '{self.subtitle_style}'. "
                f"Disponibles : {available}"
            )
        return styles[self.subtitle_style]


def _validate_weights(weights: dict) -> None:
    total = sum(weights.values())
    if not (0.98 <= total <= 1.02):
        raise ConfigError(
            f"La somme des poids de scoring (config/settings.json -> weights) "
            f"doit valoir 1.0, trouve {total:.3f}."
        )


def _load_editing_config() -> dict:
    """config/editing.json -- modules d'edition automatique.

    Absent, le fichier n'est pas une erreur : l'application retombe alors sur
    le comportement d'avant l'edition automatique (tous les modules eteints),
    ce qui garde une installation ancienne ou incomplete parfaitement
    fonctionnelle plutot que de la faire planter au demarrage.
    """
    path = CONFIG_DIR / "editing.json"
    if not path.exists():
        return {}
    data = _load_json("editing.json")
    clip_scores = data.get("clip_scores", {})
    if clip_scores.get("weights"):
        _validate_weights(clip_scores["weights"])
    return data


def load_settings(cli_args: Any) -> Settings:
    """cli_args : objet argparse.Namespace. Les valeurs non fournies sur la CLI
    (None) sont completees par config/settings.json -> defaults."""
    settings_json = _load_json("settings.json")
    defaults = settings_json.get("defaults", {})
    weights = settings_json.get("weights", {})
    _validate_weights(weights)

    keywords_config = _load_json("hooks_keywords.json")
    subtitles_config = _load_json("subtitles.json")
    editing_config = _load_editing_config()

    def pick(cli_value, key):
        return cli_value if cli_value is not None else defaults.get(key)

    settings = Settings(
        input=cli_args.input,
        output=pick(getattr(cli_args, "output", None), "output"),
        clip_duration=pick(getattr(cli_args, "clip_duration", None), "clip_duration"),
        nb_clips=pick(getattr(cli_args, "nb_clips", None), "nb_clips"),
        language=pick(getattr(cli_args, "language", None), "language"),
        pre_roll=pick(getattr(cli_args, "pre_roll", None), "pre_roll"),
        post_roll=pick(getattr(cli_args, "post_roll", None), "post_roll"),
        min_gap=pick(getattr(cli_args, "min_gap", None), "min_gap"),
        subtitle_style=pick(getattr(cli_args, "subtitle_style", None), "subtitle_style")
        or subtitles_config.get("default_style", "progressive"),
        model=pick(getattr(cli_args, "model", None), "model"),
        device=pick(getattr(cli_args, "device", None), "device"),
        overwrite=bool(getattr(cli_args, "overwrite", False)),
        no_cache=bool(getattr(cli_args, "no_cache", False)),
        debug_scores=bool(getattr(cli_args, "debug_scores", False)),
        # Options de production. Formulees en NEGATIF sur la ligne de commande
        # (--no-subtitles) : sans option, le comportement reste exactement celui
        # d'avant, ce qui evite de casser une commande deja ecrite quelque part.
        subtitles_enabled=not bool(getattr(cli_args, "no_subtitles", False)),
        aspect_ratio=(getattr(cli_args, "aspect", None)
                      or defaults.get("aspect_ratio") or "9:16"),
        smart_framing=not bool(getattr(cli_args, "no_smart_framing", False)),
        auto_montage=not bool(getattr(cli_args, "no_auto_montage", False)),
        watermark_enabled=not bool(getattr(cli_args, "no_watermark", False)),
        weights=weights,
        scoring_params=settings_json.get("scoring_params", {}),
        hook_detection=settings_json.get("hook_detection", {}),
        audio_analysis=settings_json.get("audio_analysis", {}),
        face_detection=settings_json.get("face_detection", {}),
        export=settings_json.get("export", {}),
        keywords_config=keywords_config,
        subtitles_config=subtitles_config,
        editing=editing_config,
    )

    if settings.clip_duration <= 0:
        raise ConfigError("--clip-duration doit etre > 0")
    if settings.nb_clips <= 0:
        raise ConfigError("--nb-clips doit etre > 0")
    if settings.pre_roll < 0 or settings.post_roll < 0:
        raise ConfigError("--pre-roll / --post-roll doivent etre >= 0")
    if settings.min_gap < 0:
        raise ConfigError("--min-gap doit etre >= 0")

    return settings


def load_youtube_config() -> dict:
    """config/youtube.json -- poids du Video Potential Score + parametres de
    quota. Separe de Settings/settings.json : la recherche est independante
    du moteur de traitement video (voir youtube/README dans le code)."""
    data = _load_json("youtube.json")
    _validate_weights(data.get("weights", {}))
    return data


def load_radar_config() -> dict:
    """Reglages du Radar (config/radar.json).

    Absent ou illisible -> dict vide : chaque module retombe alors sur ses
    valeurs par defaut. Un fichier de config casse ne doit pas empecher le
    Radar de fonctionner.
    """
    path = CONFIG_DIR / "radar.json"
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def load_clip_analysis_config() -> dict:
    """Marqueurs de l'analyse de contenu (config/clip_analysis.json).

    Meme regle que load_radar_config : absent ou illisible -> dict vide, et
    radar/analysis/lexicon.py retombe sur ses valeurs par defaut. Une analyse
    doit rester possible sur une installation dont la config a ete abimee.
    """
    path = CONFIG_DIR / "clip_analysis.json"
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def load_keywords_config() -> dict:
    """config/hooks_keywords.json, sans lever d'exception.

    load_settings() charge deja ce fichier pour le pipeline video, mais il exige
    des arguments de ligne de commande. Le Radar n'en a pas : il lui faut juste
    les amorces de question et les mots-cles forts, et une absence de fichier ne
    doit pas empecher une analyse de tourner.
    """
    path = CONFIG_DIR / "hooks_keywords.json"
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}
