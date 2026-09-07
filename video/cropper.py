"""Construit le filtre ffmpeg de recadrage vers 1080x1920 (9:16).

Centre sur le visage detecte (face_detector.py) quand disponible et fiable,
sinon crop centre pur -- jamais d'intervention manuelle necessaire (section 8
du cahier des charges).
"""
from __future__ import annotations

from video.face_detector import FaceCropHint

TARGET_W = 1080
TARGET_H = 1920
_TARGET_ASPECT = TARGET_W / TARGET_H


def _even(n: int) -> int:
    return n - (n % 2)


def build_crop_filter(src_w: int, src_h: int, hint: FaceCropHint | None) -> str:
    src_aspect = src_w / src_h

    if src_aspect > _TARGET_ASPECT:
        # Source plus large que la cible : on rogne en largeur, hauteur pleine.
        crop_h = src_h
        crop_w = _even(min(src_w, round(src_h * _TARGET_ASPECT)))
        y = 0
        if hint is not None:
            center_x = hint.x_center_frac * src_w
            x = center_x - crop_w / 2
        else:
            x = (src_w - crop_w) / 2
        x = _even(int(max(0, min(x, src_w - crop_w))))
    else:
        # Source plus etroite (ou deja proche de 9:16) : on rogne en hauteur.
        crop_w = src_w
        crop_h = _even(min(src_h, round(src_w / _TARGET_ASPECT)))
        x = 0
        if hint is not None:
            center_y = hint.y_center_frac * src_h
            y = center_y - crop_h / 2
        else:
            y = (src_h - crop_h) / 2
        y = _even(int(max(0, min(y, src_h - crop_h))))

    return f"crop={crop_w}:{crop_h}:{x}:{y},scale={TARGET_W}:{TARGET_H}"
