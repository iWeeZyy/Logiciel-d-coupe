"""Intro popcorn detouree (tools/build_popcorn_intro.py) : la video et son
masque sont livres, alignes image pour image, transparents hors du rond."""
import shutil
import subprocess

import numpy as np
import pytest

from core.paths import app_base_dir

_DIR = app_base_dir() / "assets" / "branding"
pytestmark = pytest.mark.skipif(shutil.which("ffprobe") is None, reason="ffmpeg requis")


def _frames(name: str) -> int:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
                          "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0",
                          str(_DIR / name)], capture_output=True, text=True).stdout
    return int(out.strip())


def test_video_et_masque_alignes():
    assert _frames("intro_popcorn.mp4") == _frames("intro_popcorn_mask.mp4") == 121


def test_transparent_hors_du_rond_opaque_au_centre():
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(_DIR / "intro_popcorn_mask.mp4"),
                          "-frames:v", "1", "-ss", "4", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                         capture_output=True).stdout
    mask = np.frombuffer(raw, np.uint8).reshape(1080, 1080)
    assert mask[540, 540] > 240                                  # le seau
    assert max(mask[5, 5], mask[5, 1074], mask[1074, 5], mask[1074, 1074]) < 10
    assert mask[1074, 1000] < 10                                 # mention « KlingAI » retiree
