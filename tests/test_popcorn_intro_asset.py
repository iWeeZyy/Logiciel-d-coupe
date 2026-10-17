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


def test_grains_hors_du_rond_gardes_mention_lointaine_ecartee():
    import importlib.util

    import cv2

    spec = importlib.util.spec_from_file_location("build_popcorn_intro",
                                                  app_base_dir() / "tools" / "build_popcorn_intro.py")
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)

    frame = np.full((600, 600, 3), 12, np.uint8)                  # fond de salle
    cv2.circle(frame, (300, 300), 200, (60, 37, 22), -1)           # rond brun
    cv2.circle(frame, (300, 70), 14, (60, 37, 22), -1)             # contour du grain
    cv2.circle(frame, (300, 70), 10, (250, 245, 220), -1)          # grain, 30 px au-dessus
    cv2.rectangle(frame, (520, 560), (590, 575), (255, 255, 255), -1)   # mention lointaine
    mask = tool.kernels_mask(frame, 300, 300, 200)
    assert mask[70, 300] > 0.9 and mask[70, 287] > 0.5            # grain et son contour
    assert mask[567, 555] == 0 and mask[20, 20] == 0
