#!/usr/bin/env python3
"""Construit l'intro popcorn des bandes-annonces (assets/branding/
intro_popcorn.mp4 + son masque intro_popcorn_mask.mp4) a partir de
l'animation fournie par l'utilisateur (tools/source/popcorn_intro_source.mp4,
generee avec Kling, 1440x1440, 24 i/s, 5 s, avec son).

RUN A LA MAIN, PAS AU BUILD -- meme convention que tools/build_intro_asset.py :
les fichiers produits sont COMMITES.

Demande de l'utilisateur : ne garder que le rond brun et tout ce qu'il
contient (le seau, le pop-corn qui jaillit A L'INTERIEUR). Tout le reste
disparait : salle de cinema du fond, grains qui sortent du rond, mention
« KlingAI » en bas a droite.

Le rond n'est pas fixe : il se resserre pendant la premiere seconde (rayon
669 -> 511 px) et se decale un peu vers le haut. Il est donc retrouve IMAGE
PAR IMAGE : le bord du disque creme (248,242,217) est ajuste a un cercle par
moindres carres, en ecartant les points aberrants (grains de pop-corn
colles au bord) ; l'anneau brun (~60,37,22) fait 8 % de ce rayon, mesure
le long de 180 rayons sortants. Puis chaque image est RECENTREE sur le rond
(le zoom d'origine est garde) : l'incrustation se place par son centre.

La transparence passe par un MASQUE aligne image pour image, comme
l'intro « Follow » (video/intro_overlay.py) : un detourage par couleur
rendrait transparents les noirs du dessin. Hors du rond, la video est
peinte de la couleur de l'anneau, pour qu'aucun liseré sombre n'apparaisse
dans le degrade du bord.

Usage :
    .venv/bin/python tools/build_popcorn_intro.py [--webm apercu.webm]
--webm ecrit en plus une version transparente (VP9 + alpha), lisible dans
un navigateur ou un logiciel de montage.
"""
from __future__ import annotations

import argparse
import subprocess
import tempfile
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "tools" / "source" / "popcorn_intro_source.mp4"
OUT_VIDEO = ROOT / "assets" / "branding" / "intro_popcorn.mp4"
OUT_MASK = ROOT / "assets" / "branding" / "intro_popcorn_mask.mp4"

OUT_SIZE = 1080
RING_COLOR = (60, 37, 22)
_FEATHER = 1.5              # largeur du degrade du bord, en pixels source


def read_frames(path: Path) -> tuple[np.ndarray, float]:
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height,r_frame_rate", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True).stdout.strip().split(",")
    w, h = int(probe[0]), int(probe[1])
    num, den = probe[2].split("/")
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "rawvideo",
                          "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, h, w, 3), float(num) / float(den)


def fit_circle(pts: np.ndarray) -> tuple[float, float, float]:
    x, y = pts[:, 0].astype(float), pts[:, 1].astype(float)
    a = np.c_[2 * x, 2 * y, np.ones_like(x)]
    (cx, cy, c), *_ = np.linalg.lstsq(a, x ** 2 + y ** 2, rcond=None)
    return cx, cy, float(np.sqrt(c + cx ** 2 + cy ** 2))


def robust_fit(pts: np.ndarray) -> tuple[float, float, float]:
    """Cercle des moindres carres, en retirant a chaque passe les points
    loin du cercle (grains de pop-corn colles au bord du disque)."""
    cx, cy, r = fit_circle(pts)
    for _ in range(6):
        dist = np.abs(np.hypot(pts[:, 0] - cx, pts[:, 1] - cy) - r)
        cx, cy, r = fit_circle(pts[dist < max(4.0, 2.5 * np.median(dist))])
    return cx, cy, r


def cream_circle(frame: np.ndarray) -> tuple[float, float, float]:
    """Centre et rayon du disque creme (interieur de l'anneau)."""
    h, w, _ = frame.shape
    f = frame.astype(int)
    cream = ((f[..., 0] > 200) & (f[..., 1] > 190) & (f[..., 2] > 140)).astype(np.uint8)
    _, labels, stats, _ = cv2.connectedComponentsWithStats(cream, 8)
    disk = (labels == 1 + np.argmax(stats[1:, cv2.CC_STAT_AREA])).astype(np.uint8)
    contours, _ = cv2.findContours(disk, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    pts = max(contours, key=len)[:, 0, :]
    # Les points poses sur le bord de l'image (rond coupe par le cadre au
    # tout debut) ne sont pas le bord du disque.
    inside = (pts[:, 0] > 2) & (pts[:, 0] < w - 3) & (pts[:, 1] > 2) & (pts[:, 1] < h - 3)
    return robust_fit(pts[inside])


def ring_thickness(frame: np.ndarray, cx: float, cy: float, rc: float) -> float | None:
    """Epaisseur mediane de l'anneau brun, le long de rayons sortants."""
    h, w, _ = frame.shape
    found = []
    for angle in np.linspace(0, 2 * np.pi, 180, endpoint=False):
        for t in range(2, 140):
            x = int(round(cx + (rc + t) * np.cos(angle)))
            y = int(round(cy + (rc + t) * np.sin(angle)))
            if not (0 <= x < w and 0 <= y < h):
                break
            if frame[y, x, 0] < 38:              # fond sombre atteint
                found.append(t)
                break
    return float(np.median(found)) if len(found) > 10 else None


def _median_smooth(values: np.ndarray, k: int = 5) -> np.ndarray:
    pad = k // 2
    padded = np.pad(values, pad, mode="edge")
    return np.array([np.median(padded[i:i + k]) for i in range(len(values))])


def track(frames: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(cx, cy, rayon exterieur) par image, lisses sur 5 images."""
    circles = [cream_circle(f) for f in frames]
    ratios = [t / rc for f, (cx, cy, rc) in zip(frames, circles)
              if (t := ring_thickness(f, cx, cy, rc)) is not None]
    ratio = float(np.median(ratios))
    cx, cy, rc = (np.array(v) for v in zip(*circles))
    # -1 px : le bord du masque reste dans le brun, jamais dans le fond.
    return _median_smooth(cx), _median_smooth(cy), _median_smooth(rc * (1 + ratio) - 1)


def render(frames, cx, cy, r_out):
    """Images recentrees sur le rond (carre de cote 2 x rayon maximal) et
    masques correspondants, a OUT_SIZE."""
    side = int(np.ceil(2 * r_out.max())) + 4
    half = side / 2
    yy, xx = np.mgrid[0:side, 0:side]
    dist = np.hypot(xx + 0.5 - half, yy + 0.5 - half)
    for frame, x0, y0, r in zip(frames, cx, cy, r_out):
        move = np.float32([[1, 0, half - x0], [0, 1, half - y0]])
        moved = cv2.warpAffine(frame, move, (side, side), flags=cv2.INTER_LINEAR,
                               borderMode=cv2.BORDER_CONSTANT, borderValue=RING_COLOR)
        alpha = np.clip((r - dist) / _FEATHER + 0.5, 0.0, 1.0)
        moved = np.where(alpha[..., None] > 0, moved, np.uint8(RING_COLOR)).astype(np.uint8)
        yield (cv2.resize(moved, (OUT_SIZE, OUT_SIZE), interpolation=cv2.INTER_AREA),
               cv2.resize((alpha * 255).astype(np.uint8), (OUT_SIZE, OUT_SIZE),
                          interpolation=cv2.INTER_AREA))


def _encoder(out: Path, fps: float, pix_in: str, extra: list[str]):
    return subprocess.Popen(
        ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", pix_in,
         "-s", f"{OUT_SIZE}x{OUT_SIZE}", "-r", f"{fps:g}", "-i", "-", *extra, str(out)],
        stdin=subprocess.PIPE)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--webm", type=Path, help="apercu transparent (VP9 + alpha)")
    args = parser.parse_args()

    frames, fps = read_frames(SOURCE)
    cx, cy, r_out = track(frames)
    print(f"{len(frames)} images ; rayon exterieur {r_out.max():.0f} -> {r_out[-1]:.0f} px")

    with tempfile.TemporaryDirectory() as tmp:
        silent = Path(tmp) / "video.mp4"
        video = _encoder(silent, fps, "rgb24", ["-c:v", "libx264", "-preset", "slow", "-crf", "16",
                                                "-pix_fmt", "yuv420p"])
        mask = _encoder(OUT_MASK, fps, "gray", ["-c:v", "libx264", "-preset", "slow", "-crf", "12",
                                                "-pix_fmt", "yuv420p"])
        webm = (_encoder(args.webm, fps, "rgba", ["-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p",
                                                   "-b:v", "0", "-crf", "24", "-auto-alt-ref", "0"])
                if args.webm else None)
        for rgb, alpha in render(frames, cx, cy, r_out):
            video.stdin.write(rgb.tobytes())
            mask.stdin.write(alpha.tobytes())
            if webm:
                webm.stdin.write(np.dstack([rgb, alpha]).tobytes())
        for proc in (video, mask, webm):
            if proc:
                proc.stdin.close()
                if proc.wait():
                    raise SystemExit("ffmpeg en echec")
        # Le son d'origine (crepitement du pop-corn) est garde.
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(silent), "-i", str(SOURCE),
                        "-map", "0:v", "-map", "1:a?", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
                        "-movflags", "+faststart", str(OUT_VIDEO)], check=True)
    print(f"Ecrit : {OUT_VIDEO.name}, {OUT_MASK.name}" + (f", {args.webm}" if args.webm else ""))


if __name__ == "__main__":
    main()
