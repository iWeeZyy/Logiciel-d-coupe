"""Nommage des clips (clip_XX_score_YY.mp4) et ecriture de output/results.json
(section 10 du cahier des charges)."""
from __future__ import annotations

import json
from pathlib import Path

from core.models import ClipResult
from utils.errors import OutputExistsError


def clip_filename(index: int, score: float) -> str:
    return f"clip_{index:02d}_score_{int(round(score))}.mp4"


def preflight_check(output_dir: str, overwrite: bool) -> None:
    out = Path(output_dir)
    if not out.exists():
        return
    existing = list(out.glob("clip_*.mp4"))
    if existing and not overwrite:
        raise OutputExistsError(
            f"'{output_dir}' contient deja {len(existing)} clip(s) (ex: {existing[0].name}). "
            "Utilise --overwrite pour les remplacer, ou choisis un autre --output."
        )


def ensure_output_dir(output_dir: str) -> None:
    Path(output_dir).mkdir(parents=True, exist_ok=True)


def write_results(output_dir: str, clip_results: list[ClipResult]) -> str:
    path = Path(output_dir) / "results.json"
    data = [c.to_dict() for c in clip_results]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return str(path)
