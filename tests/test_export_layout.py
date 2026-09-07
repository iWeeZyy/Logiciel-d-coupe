"""Structure du dossier de sortie (clips/, thumbnails/, subtitles/, metadata/).

Verifie aussi la retro-compatibilite : un projet a l'ancienne disposition
(clips a plat, nom portant le score) doit rester detecte par le preflight.
"""
import json

import pytest

from core.models import ClipResult
from export.exporter import (
    clip_relative_path,
    clip_stem,
    ensure_output_dir,
    preflight_check,
    write_clip_metadata,
    write_results,
)
from utils.errors import OutputExistsError


def _clip(index=1):
    return ClipResult(
        index=index,
        file_name=clip_relative_path(index),
        start=10.0, end=40.0, duration=30.0,
        score=82.0,
        scores={"total": 80.0, "viral": 82.0, "rewatch": 70.0, "content": 60.0},
        transcript="un extrait",
        language="fr",
        reasons=["question detectee"],
        context={"applied": True, "confidence": 0.9, "category": "revelation", "reasons": []},
    )


def test_clip_paths_are_stable_and_score_free():
    # Le score n'est plus dans le nom : il change avec les poids, alors que le
    # nom est reference par results.json, les sous-titres et les miniatures.
    assert clip_stem(1) == "clip_01"
    assert clip_relative_path(12) == "clips/clip_12.mp4"


def test_ensure_output_dir_creates_the_whole_tree(tmp_path):
    ensure_output_dir(str(tmp_path / "projet"))

    base = tmp_path / "projet"
    for name in ("clips", "thumbnails", "subtitles", "metadata"):
        assert (base / name).is_dir()


def test_preflight_accepts_an_empty_or_missing_folder(tmp_path):
    preflight_check(str(tmp_path / "inexistant"), overwrite=False)
    ensure_output_dir(str(tmp_path / "vide"))
    preflight_check(str(tmp_path / "vide"), overwrite=False)


def test_preflight_detects_clips_in_the_new_layout(tmp_path):
    ensure_output_dir(str(tmp_path))
    (tmp_path / "clips" / "clip_01.mp4").write_bytes(b"")

    with pytest.raises(OutputExistsError):
        preflight_check(str(tmp_path), overwrite=False)
    preflight_check(str(tmp_path), overwrite=True)  # --overwrite passe


def test_preflight_still_detects_clips_from_an_older_project(tmp_path):
    # Ancienne disposition : clips a plat, avec le score dans le nom.
    (tmp_path / "clip_01_score_87.mp4").write_bytes(b"")

    with pytest.raises(OutputExistsError):
        preflight_check(str(tmp_path), overwrite=False)


def test_metadata_file_is_written_per_clip(tmp_path):
    ensure_output_dir(str(tmp_path))
    path = write_clip_metadata(str(tmp_path), _clip(3))

    assert path.endswith("metadata/clip_03.json") or path.endswith("metadata\\clip_03.json")
    data = json.loads((tmp_path / "metadata" / "clip_03.json").read_text(encoding="utf-8"))
    assert data["clip"] == "clips/clip_03.mp4"
    assert data["context"]["category"] == "revelation"


def test_results_index_lists_every_clip_with_its_relative_path(tmp_path):
    ensure_output_dir(str(tmp_path))
    write_results(str(tmp_path), [_clip(1), _clip(2)])

    data = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))
    assert [c["clip"] for c in data] == ["clips/clip_01.mp4", "clips/clip_02.mp4"]
    assert data[0]["scores"]["viral"] == 82.0
