"""Choix de la meilleure image de miniature et placement du texte
(fonctionnalite 6) -- partie pure : notation et mise en page, aucune image.

L'extraction des images et la composition finale vivent dans
video/thumbnailer.py. Cette separation permet de tester la logique de selection
(quelle image, quel texte, ou le poser, quelle couleur) sans video ni
bibliotheque d'imagerie.

Rien n'est invente : le texte propose vient des titres extraits du clip
(editing/metadata.py), et si aucune image n'est vraiment bonne, la meilleure
disponible est proposee telle quelle plutot que "reparee" a coups de filtres.
"""
from __future__ import annotations

from dataclasses import dataclass

from video.face_detector import FaceBox

VARIANT_FACE = "a"
VARIANT_CONTEXT = "b"
VARIANT_EXPRESSION = "c"


@dataclass(frozen=True)
class FrameMetrics:
    """Mesures brutes d'une image candidate, toutes calculees par l'appelant."""

    t: float
    sharpness: float          # variance du laplacien : faible = flou
    brightness: float         # 0..255
    face: FaceBox | None = None
    eyes_open: bool | None = None
    change_from_previous: float = 0.0   # 0..1, fort = transition/coupe


@dataclass(frozen=True)
class FrameScore:
    t: float
    score: float
    metrics: FrameMetrics
    reasons: tuple[str, ...] = ()


def score_frames(
    metrics: list[FrameMetrics],
    *,
    min_sharpness: float = 40.0,
    ideal_face_height_frac: float = 0.35,
    transition_threshold: float = 0.45,
    dark_threshold: float = 40.0,
    bright_threshold: float = 225.0,
) -> list[FrameScore]:
    """Note chaque image. Le flou et les transitions sont eliminatoires : une
    miniature floue ou prise en plein fondu ne se rattrape pas."""
    if not metrics:
        return []

    max_sharpness = max(m.sharpness for m in metrics) or 1.0
    scored: list[FrameScore] = []

    for m in metrics:
        reasons: list[str] = []
        if m.sharpness < min_sharpness:
            scored.append(FrameScore(m.t, 0.0, m, ("image floue",)))
            continue
        if m.change_from_previous > transition_threshold:
            scored.append(FrameScore(m.t, 0.0, m, ("image prise pendant une transition",)))
            continue
        if m.brightness < dark_threshold or m.brightness > bright_threshold:
            scored.append(FrameScore(m.t, 0.0, m, ("image trop sombre ou brulee",)))
            continue

        score = 30.0 * (m.sharpness / max_sharpness)
        reasons.append("image nette")

        if m.face is not None:
            height_ratio = min(1.0, m.face.h / ideal_face_height_frac)
            score += 40.0 * height_ratio + 10.0 * m.face.confidence
            reasons.append("visage visible")
            if m.eyes_open:
                score += 12.0
                reasons.append("yeux ouverts")
            elif m.eyes_open is False:
                score -= 8.0
        else:
            score += 8.0
            reasons.append("plan de contexte (aucun visage)")

        # Une luminosite moyenne se lit mieux qu'une image aux extremes.
        score += 8.0 * (1.0 - abs(m.brightness - 128.0) / 128.0)

        scored.append(FrameScore(m.t, round(score, 2), m, tuple(reasons)))

    return scored


def choose_variants(scores: list[FrameScore], min_gap_s: float = 1.0) -> dict[str, FrameScore]:
    """Trois images distinctes : visage, contexte, meilleure expression.

    Une variante est absente si aucune image ne lui correspond -- on ne
    remplit pas les trois cases a tout prix."""
    usable = [s for s in scores if s.score > 0]
    if not usable:
        # Aucune image ne passe les eliminatoires : on propose quand meme la
        # moins mauvaise, sans traitement agressif, plutot que rien du tout.
        if scores:
            best = max(scores, key=lambda s: s.metrics.sharpness)
            return {VARIANT_FACE: best}
        return {}

    ranked = sorted(usable, key=lambda s: s.score, reverse=True)
    chosen: dict[str, FrameScore] = {}
    taken: list[float] = []

    def take(key: str, candidates: list[FrameScore]) -> None:
        for candidate in candidates:
            if all(abs(candidate.t - t) >= min_gap_s for t in taken):
                chosen[key] = candidate
                taken.append(candidate.t)
                return

    take(VARIANT_FACE, [s for s in ranked if s.metrics.face is not None] or ranked)
    take(VARIANT_CONTEXT, [s for s in ranked if s.metrics.face is None] or ranked)
    take(VARIANT_EXPRESSION,
         sorted((s for s in usable if s.metrics.face is not None and s.metrics.eyes_open),
                key=lambda s: s.metrics.sharpness, reverse=True) or ranked)

    return chosen


@dataclass(frozen=True)
class TextLayout:
    """Ou poser le texte dans le cadre 1080x1920 et comment le rendre lisible."""

    band: str            # "top" | "bottom"
    y_center_frac: float
    light_text: bool     # texte clair sur contour sombre, ou l'inverse
    max_width_frac: float = 0.86


def choose_text_layout(
    face_y_frac: float | None,
    band_luminance: dict[str, float] | None = None,
    *,
    face_avoid_frac: float = 0.18,
    top_center: float = 0.16,
    bottom_center: float = 0.84,
) -> TextLayout:
    """Place le texte dans la bande la plus eloignee du visage, et choisit la
    couleur en fonction de la luminosite reelle de cette bande.

    Le texte ne doit jamais couvrir le visage : c'est lui qui donne envie de
    cliquer."""
    band = "bottom"
    if face_y_frac is not None:
        distance_to_top = abs(face_y_frac - top_center)
        distance_to_bottom = abs(face_y_frac - bottom_center)
        if distance_to_bottom < face_avoid_frac <= distance_to_top:
            band = "top"
        elif distance_to_bottom < distance_to_top:
            band = "top"

    luminance = (band_luminance or {}).get(band, 0.0)
    return TextLayout(
        band=band,
        y_center_frac=top_center if band == "top" else bottom_center,
        light_text=luminance < 140.0,
    )


def choose_text(titles, max_words: int = 7, min_words: int = 2) -> str:
    """Texte de miniature : la proposition la plus courte des titres extraits.

    Aucune phrase n'est fabriquee ici -- si les titres sont vides, la miniature
    sort sans texte, ce qui reste une miniature valable."""
    candidates = []
    for title in titles or []:
        text = getattr(title, "text", None) or (title.get("text") if isinstance(title, dict) else None)
        if not text:
            continue
        words = text.replace("…", "").split()
        if len(words) < min_words:
            continue
        candidates.append(" ".join(words[:max_words]))

    if not candidates:
        return ""
    return min(candidates, key=lambda t: len(t.split())).upper()
