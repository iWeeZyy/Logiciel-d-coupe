"""Construit le filtre ffmpeg de recadrage vers 1080x1920 (9:16).

Centre sur le visage detecte (face_detector.py) quand disponible et fiable,
sinon crop centre pur -- jamais d'intervention manuelle necessaire (section 8
du cahier des charges).

La geometrie du recadrage est calculee separement du filtre lui-meme
(`compute_crop_rect`) : le placement des sous-titres a besoin de savoir ou le
visage se retrouve DANS le cadre final, et refaire ce calcul de son cote
finirait par diverger de celui-ci.
"""
from __future__ import annotations

from dataclasses import dataclass

from video.face_detector import FaceCropHint

TARGET_W = 1080
TARGET_H = 1920
_TARGET_ASPECT = TARGET_W / TARGET_H


def _even(n: int) -> int:
    return n - (n % 2)


@dataclass(frozen=True)
class CenterHint:
    """Point de visee minimal accepte par compute_crop_rect.

    Le suivi de cadrage et le generateur de miniatures visent un centre calcule
    eux-memes, sans passer par une detection de visage complete : ce petit
    adaptateur evite que chacun refabrique un FaceCropHint de circonstance."""

    x_center_frac: float
    y_center_frac: float


@dataclass(frozen=True)
class CropRect:
    """Zone retenue dans l'image SOURCE, en pixels."""

    x: int
    y: int
    w: int
    h: int


def compute_crop_rect(src_w: int, src_h: int, hint: FaceCropHint | None) -> CropRect:
    src_aspect = src_w / src_h

    if src_aspect > _TARGET_ASPECT:
        # Source plus large que la cible : on rogne en largeur, hauteur pleine.
        crop_h = src_h
        crop_w = _even(min(src_w, round(src_h * _TARGET_ASPECT)))
        y = 0
        if hint is not None:
            x = hint.x_center_frac * src_w - crop_w / 2
        else:
            x = (src_w - crop_w) / 2
        x = _even(int(max(0, min(x, src_w - crop_w))))
    else:
        # Source plus etroite (ou deja proche de 9:16) : on rogne en hauteur.
        crop_w = src_w
        crop_h = _even(min(src_h, round(src_w / _TARGET_ASPECT)))
        x = 0
        if hint is not None:
            y = hint.y_center_frac * src_h - crop_h / 2
        else:
            y = (src_h - crop_h) / 2
        y = _even(int(max(0, min(y, src_h - crop_h))))

    return CropRect(x=x, y=y, w=crop_w, h=crop_h)


def build_crop_filter(src_w: int, src_h: int, hint: FaceCropHint | None) -> str:
    rect = compute_crop_rect(src_w, src_h, hint)
    return f"crop={rect.w}:{rect.h}:{rect.x}:{rect.y},scale={TARGET_W}:{TARGET_H}"


def face_center_in_output(
    hint: FaceCropHint | None, src_w: int, src_h: int, rect: CropRect
) -> tuple[float, float] | None:
    """Position du visage dans le cadre FINAL 9:16, en fractions (0..1).

    Renvoie None si aucun visage n'a ete detecte ou si son centre tombe hors du
    recadrage (cas possible quand le crop a ete rabattu sur un bord de l'image) :
    mieux vaut alors ne pas deplacer les sous-titres que de les deplacer sur une
    position fausse.
    """
    if hint is None or rect.w <= 0 or rect.h <= 0:
        return None

    x_frac = (hint.x_center_frac * src_w - rect.x) / rect.w
    y_frac = (hint.y_center_frac * src_h - rect.y) / rect.h
    if not (0.0 <= x_frac <= 1.0 and 0.0 <= y_frac <= 1.0):
        return None
    return x_frac, y_frac
