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

    # Chargés depuis settings.json
    weights: dict = field(default_factory=dict)
    scoring_params: dict = field(default_factory=dict)
    hook_detection: dict = field(default_factory=dict)
    audio_analysis: dict = field(default_factory=dict)
    face_detection: dict = field(default_factory=dict)
    export: dict = field(default_factory=dict)

    # Chargés depuis hooks_keywords.json / subtitles.json
    keywords_config: dict = field(default_factory=dict)
    subtitles_config: dict = field(default_factory=dict)

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


def load_settings(cli_args: Any) -> Settings:
    """cli_args : objet argparse.Namespace. Les valeurs non fournies sur la CLI
    (None) sont completees par config/settings.json -> defaults."""
    settings_json = _load_json("settings.json")
    defaults = settings_json.get("defaults", {})
    weights = settings_json.get("weights", {})
    _validate_weights(weights)

    keywords_config = _load_json("hooks_keywords.json")
    subtitles_config = _load_json("subtitles.json")

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
        weights=weights,
        scoring_params=settings_json.get("scoring_params", {}),
        hook_detection=settings_json.get("hook_detection", {}),
        audio_analysis=settings_json.get("audio_analysis", {}),
        face_detection=settings_json.get("face_detection", {}),
        export=settings_json.get("export", {}),
        keywords_config=keywords_config,
        subtitles_config=subtitles_config,
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
