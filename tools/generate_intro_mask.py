#!/usr/bin/env python3
"""Genere le masque alpha de l'incrustation intro (assets/branding/
intro_follow_mask.png).

RUN A LA MAIN, PAS AU BUILD -- meme convention que generate_delire_assets.py :
le PNG produit est COMMITE, pas regenere a chaque build.

POURQUOI UN MASQUE ET NON UN colorkey AU RENDU. La video source
(intro_follow.mp4) a un fond NOIR PLEIN CADRE, mais le logo lui-meme contient
AUSSI du noir -- le disque a l'interieur de l'anneau neon, sous le texte
"ClipsOfStreams". Un colorkey supprime tout le noir sans distinction : le
disque du logo devenait transparent en meme temps que le fond, laissant voir
le clip a travers le logo au lieu de son propre fond noir.

Un simple REMPLISSAGE PAR PROPAGATION (flood-fill) depuis les bords de
l'image resout ca correctement : les pixels noirs ATTEIGNABLES depuis un bord
en ne traversant que du noir sont le vrai fond (on les rend transparents) ;
le disque interieur, lui, est ENTOURE par l'anneau neon (jamais noir), donc
jamais atteint par la propagation -- il reste opaque, exactement comme il
apparait dans la video source. C'est un remplissage sur UNE SEULE image de
reference : l'anneau ne bouge pas au fil de l'animation (seules des
etincelles decoratives orbitent autour, sans jamais recouvrir tout un cote),
donc un masque fixe reste correct sur les ~7 secondes du clip.

Usage :
    .venv/bin/python tools/generate_intro_mask.py
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
SOURCE_VIDEO = ROOT / "assets" / "branding" / "intro_follow.mp4"
OUT_PATH = ROOT / "assets" / "branding" / "intro_follow_mask.png"

# Meme cadre que video/intro_overlay.py (CROP_X/Y/W/H) -- les deux DOIVENT
# rester en phase, puisque le masque n'a de sens qu'aligne sur ce recadrage.
CROP_X, CROP_Y, CROP_W, CROP_H = 40, 330, 1000, 1140

# Image choisie au milieu de l'animation : l'anneau et le bouton "+ Follow"
# sont pleinement etablis (pas encore en train d'apparaitre/disparaitre), ce
# qui donne le contour le plus representatif.
REFERENCE_TIME_S = 3.5

# Seuil de "noir" : identique dans l'esprit a l'ancien colorkey (similarity
# 0.15 sur une echelle 0..255 ~ 38). Une marge un peu plus etroite ici : le
# flood-fill n'a pas besoin d'etre genereux, un pixel legerement grisatre du
# halo neon doit rester du cote "logo", pas "fond".
BLACK_THRESHOLD = 35

# Adoucit le contour du masque (pixelise sinon) -- meme raisonnement que le
# blend du colorkey qu'il remplace.
FEATHER_RADIUS = 2


def _extract_reference_frame(tmp_path: Path) -> None:
    cmd = [
        "ffmpeg", "-y", "-ss", str(REFERENCE_TIME_S), "-i", str(SOURCE_VIDEO),
        "-vf", f"crop={CROP_W}:{CROP_H}:{CROP_X}:{CROP_Y}",
        "-frames:v", "1", "-update", "1", str(tmp_path),
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        sys.exit(f"ffmpeg a echoue : {result.stderr.decode(errors='replace')}")


def _flood_fill_mask(arr: np.ndarray) -> np.ndarray:
    h, w, _ = arr.shape
    is_black = arr.max(axis=2) < BLACK_THRESHOLD

    visited = np.zeros((h, w), dtype=bool)
    dq: deque[tuple[int, int]] = deque()

    def _seed(y: int, x: int) -> None:
        if is_black[y, x] and not visited[y, x]:
            visited[y, x] = True
            dq.append((y, x))

    for x in range(w):
        _seed(0, x)
        _seed(h - 1, x)
    for y in range(h):
        _seed(y, 0)
        _seed(y, w - 1)

    while dq:
        y, x = dq.popleft()
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ny, nx = y + dy, x + dx
            if 0 <= ny < h and 0 <= nx < w and is_black[ny, nx] and not visited[ny, nx]:
                visited[ny, nx] = True
                dq.append((ny, nx))

    # 255 = garder (opaque), 0 = fond retire (transparent).
    return np.where(visited, 0, 255).astype(np.uint8)


def main() -> None:
    if not SOURCE_VIDEO.is_file():
        sys.exit(f"Introuvable : {SOURCE_VIDEO}")

    with tempfile.TemporaryDirectory() as tmp_dir:
        frame_path = Path(tmp_dir) / "reference.png"
        _extract_reference_frame(frame_path)
        arr = np.array(Image.open(frame_path).convert("RGB"))

    if arr.shape[:2] != (CROP_H, CROP_W):
        sys.exit(f"Image de reference {arr.shape[:2]} != cadre attendu {(CROP_H, CROP_W)}")

    mask = _flood_fill_mask(arr)
    mask_img = Image.fromarray(mask, mode="L").filter(ImageFilter.GaussianBlur(FEATHER_RADIUS))
    mask_img.save(OUT_PATH)
    print(f"Ecrit {OUT_PATH} ({mask_img.size[0]}x{mask_img.size[1]})")


if __name__ == "__main__":
    main()
