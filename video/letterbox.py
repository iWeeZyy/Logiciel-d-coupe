"""Bandes noires incrustees DANS la source (flux « cinema » d'un streamer).

Signale sur une compilation de Squeezie (2026-10-31) : son flux 2560x1440 ne
montre la camera que dans une bande au milieu, avec du noir et des logos de
sponsors au-dessus et en dessous. Recadre en 9:16 sur l'image entiere, le
clip gardait ces bandes : une petite image au milieu d'un ecran noir.

On cherche donc la zone utile avant de recadrer. `cropdetect` de ffmpeg ne
convient pas : un logo de sponsor dans la bande la fait compter comme image.
Ici, une ligne est une BANDE quand presque tous ses pixels sont sombres (un
logo n'en couvre qu'une petite part), et ce sur la plupart des images
echantillonnees -- une scene de nuit n'est pas une bande. Rien n'est coupe
si les bandes ne sont pas franches (au moins 8 % de la hauteur au total).
"""
from __future__ import annotations

from dataclasses import dataclass

from core.logging_setup import get_logger

logger = get_logger()

DARK_LUMA = 40              # au-dessous : pixel « noir » (bande)
BAR_MAX_BRIGHT = 0.12       # une ligne de bande : moins de 12 % de pixels clairs
BAR_MIN_FRAMES = 0.75       # ... sur au moins 75 % des images echantillonnees
MIN_REMOVED = 0.08          # en dessous, on ne touche a rien
SAMPLES = 9


@dataclass(frozen=True)
class Box:
    x: int
    y: int
    w: int
    h: int


def _run(bars: list[bool]) -> tuple[int, int]:
    """(debut, fin exclue) de la plus longue suite de lignes « image »."""
    best, start = (0, 0), None
    for i, bar in enumerate(list(bars) + [True]):
        if not bar and start is None:
            start = i
        elif bar and start is not None:
            if i - start > best[1] - best[0]:
                best = (start, i)
            start = None
    return best


def content_span(bright_fractions: list[list[float]]) -> tuple[int, int]:
    """Zone utile le long d'un axe (fonction pure). `bright_fractions[f][i]` :
    part de pixels clairs de la ligne i sur l'image f. (debut, fin exclue)."""
    if not bright_fractions:
        return 0, 0
    size = len(bright_fractions[0])
    frames = len(bright_fractions)
    bars = [sum(1 for f in bright_fractions if f[i] < BAR_MAX_BRIGHT) >= BAR_MIN_FRAMES * frames
            for i in range(size)]
    return _run(bars)


def detect_content_box(video_path: str, samples: int = SAMPLES) -> Box | None:
    """La zone utile de la video, ou None s'il n'y a pas de bandes franches."""
    import cv2
    import numpy as np

    cap = cv2.VideoCapture(str(video_path))
    try:
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        if total <= 0 or width <= 0 or height <= 0:
            return None
        rows, cols = [], []
        for k in range(samples):
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(total * (k + 0.5) / samples))
            ok, frame = cap.read()
            if not ok:
                continue
            gray = cv2.cvtColor(cv2.resize(frame, (width // 4, height // 4)), cv2.COLOR_BGR2GRAY)
            bright = gray > DARK_LUMA
            rows.append(np.repeat(bright.mean(axis=1), 4)[:height].tolist())
            cols.append(np.repeat(bright.mean(axis=0), 4)[:width].tolist())
    finally:
        cap.release()
    if len(rows) < 3:
        return None
    y0, y1 = content_span(rows)
    x0, x1 = content_span(cols)
    if y1 - y0 <= 0 or x1 - x0 <= 0:
        return None
    removed_h = 1 - (y1 - y0) / height
    removed_w = 1 - (x1 - x0) / width
    if removed_h < MIN_REMOVED:
        y0, y1 = 0, height
    if removed_w < MIN_REMOVED:
        x0, x1 = 0, width
    if (y0, y1, x0, x1) == (0, height, 0, width):
        return None
    # Dimensions paires (libx264), sans deborder.
    w, h = (x1 - x0) // 2 * 2, (y1 - y0) // 2 * 2
    return Box(x0, y0, w, h)


def crop_to_content(video_path: str, out_path: str, box: Box, cancel_token=None) -> str:
    """La video recadree sur sa zone utile (son intact). Qualite quasi sans
    perte : c'est une etape intermediaire, le montage reencode ensuite."""
    from video.ffmpeg_utils import run_ffmpeg

    run_ffmpeg(["-i", str(video_path),
                "-vf", f"crop={box.w}:{box.h}:{box.x}:{box.y}",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "14",
                "-c:a", "copy", str(out_path)],
               "retrait des bandes noires de la source", cancel_token=cancel_token)
    return str(out_path)
