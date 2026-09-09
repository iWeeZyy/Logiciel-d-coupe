"""Cadrage intelligent 9:16 : trajectoire de recadrage lissee (fonctionnalite 2).

Transforme une suite de detections de visages (video/face_detector.py) en une
trajectoire de points de visee, lissee pour que le cadrage ne tremble jamais :

1. cible brute a chaque instant (visage seul, locuteur actif, ou barycentre
   du groupe quand personne ne se detache) ;
2. zone morte : sous un certain deplacement, on ne bouge pas du tout -- c'est
   ce qui elimine le tremblement de la detection, pas le lissage ;
3. moyenne exponentielle : le cadrage suit le sujet avec un peu d'inertie ;
4. limite de vitesse : meme sur un saut brutal de detection, le cadrage ne peut
   pas se deplacer plus vite qu'une fraction d'image par seconde.

Si les detections sont trop rares ou trop instables, la fonction renvoie un
plan STATIQUE : mieux vaut un cadrage fixe correct qu'un cadrage mobile qui se
trompe (section 13 du cahier des charges).

Module pur : il ne lit ni la video ni ffmpeg, seulement des positions.
"""
from __future__ import annotations

from dataclasses import dataclass

from editing.speaker import SpeakerDecision, order_faces_left_to_right, speaker_at
from video.face_detector import FaceSample

MODE_STATIC = "static"
MODE_TRACK = "track"
MODE_BOTH = "both"


@dataclass(frozen=True)
class FramingKeyframe:
    """Point de visee a un instant SOURCE, en fractions de l'image d'origine."""

    t: float
    cx: float
    cy: float


@dataclass(frozen=True)
class FramingPlan:
    keyframes: tuple[FramingKeyframe, ...]
    mode: str
    confidence: float
    reasons: tuple[str, ...] = ()

    @property
    def is_static(self) -> bool:
        return len(self.keyframes) <= 1

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "confidence": round(self.confidence, 3),
            "keyframes": len(self.keyframes),
            "reasons": list(self.reasons),
        }


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def choose_targets(
    samples: list[FaceSample],
    speaker_decisions: list[SpeakerDecision] | None = None,
    two_faces_min_distance_frac: float = 0.18,
    vertical_bias: float = 0.42,
) -> tuple[list[FramingKeyframe], str]:
    """Cible brute a chaque echantillon, plus le mode retenu.

    `vertical_bias` place le visage un peu au-dessus du centre du cadre (0.42 =
    aux deux cinquiemes) : un visage exactement centre en 9:16 laisse un vide
    au-dessus de la tete et coupe le buste.
    """
    # Meme regle que pour le cadrage fixe : une incrustation webcam n'est pas un
    # participant. Sans ce filtre, deux visages eloignes -- le sujet et la
    # vignette -- declenchaient le cadrage "groupe", qui cadrait le vide entre
    # les deux.
    from editing.subject import keep_subject_faces

    samples = keep_subject_faces(samples)

    targets: list[FramingKeyframe] = []
    modes: list[str] = []

    for sample in samples:
        if not sample.faces:
            continue

        ordered = order_faces_left_to_right(sample)
        if len(ordered) >= 2:
            boxes = [face for face, _ in ordered]
            # Etalement de TOUS les visages, pas seulement des deux premiers :
            # sur un plateau a trois ou quatre personnes, les deux plus a gauche
            # peuvent etre cote a cote alors que le groupe occupe tout le cadre.
            spread = max(b.cx for b in boxes) - min(b.cx for b in boxes)
            focus = speaker_at(speaker_decisions or [], sample.t)

            if spread >= two_faces_min_distance_frac and focus is None:
                # Plusieurs personnes distinctes, aucun locuteur identifie de
                # facon sure : on cadre le groupe plutot que de parier. Avec
                # deux visages, ce barycentre est exactement leur milieu --
                # le comportement d'avant est donc conserve tel quel.
                cx = sum(b.cx for b in boxes) / len(boxes)
                cy = sum(b.cy for b in boxes) / len(boxes)
                modes.append(MODE_BOTH)
            else:
                chosen = ordered[focus][0] if focus is not None and focus < len(ordered) else ordered[0][0]
                cx, cy = chosen.cx, chosen.cy
                modes.append(MODE_TRACK)
        else:
            cx, cy = ordered[0][0].cx, ordered[0][0].cy
            modes.append(MODE_TRACK)

        targets.append(FramingKeyframe(t=sample.t, cx=_clamp01(cx), cy=_clamp01(cy - (0.5 - vertical_bias))))

    mode = MODE_BOTH if modes.count(MODE_BOTH) > len(modes) / 2 else MODE_TRACK
    return targets, (mode if targets else MODE_STATIC)


def smooth_trajectory(
    targets: list[FramingKeyframe],
    alpha: float = 0.25,
    max_speed_frac_per_s: float = 0.12,
    deadzone_frac: float = 0.02,
) -> list[FramingKeyframe]:
    """Lissage : zone morte, puis moyenne exponentielle, puis limite de vitesse."""
    if not targets:
        return []

    smoothed = [targets[0]]
    for previous_target, target in zip(targets, targets[1:]):
        last = smoothed[-1]
        dt = max(1e-3, target.t - previous_target.t)

        cx, cy = target.cx, target.cy
        if abs(cx - last.cx) < deadzone_frac:
            cx = last.cx
        if abs(cy - last.cy) < deadzone_frac:
            cy = last.cy

        cx = alpha * cx + (1.0 - alpha) * last.cx
        cy = alpha * cy + (1.0 - alpha) * last.cy

        max_step = max_speed_frac_per_s * dt
        cx = last.cx + max(-max_step, min(max_step, cx - last.cx))
        cy = last.cy + max(-max_step, min(max_step, cy - last.cy))

        smoothed.append(FramingKeyframe(t=target.t, cx=_clamp01(cx), cy=_clamp01(cy)))

    return smoothed


def _decimate(keyframes: list[FramingKeyframe], max_keyframes: int) -> list[FramingKeyframe]:
    """Ramene la trajectoire sous une limite de points : l'expression ffmpeg
    construite ensuite grandit lineairement avec leur nombre, et une expression
    de plusieurs milliers de caracteres devient couteuse a evaluer pour chaque
    image."""
    if len(keyframes) <= max_keyframes:
        return keyframes
    step = len(keyframes) / max_keyframes
    kept = [keyframes[int(i * step)] for i in range(max_keyframes)]
    if kept[-1] is not keyframes[-1]:
        kept[-1] = keyframes[-1]
    return kept


def build_framing_plan(
    samples: list[FaceSample],
    *,
    speaker_decisions: list[SpeakerDecision] | None = None,
    min_samples_ratio: float = 0.35,
    two_faces_min_distance_frac: float = 0.18,
    vertical_bias: float = 0.42,
    smoothing_alpha: float = 0.25,
    max_speed_frac_per_s: float = 0.12,
    deadzone_frac: float = 0.02,
    max_keyframes: int = 60,
    static_movement_threshold: float = 0.03,
) -> FramingPlan:
    """Trajectoire de cadrage, ou plan statique si le suivi n'est pas fiable."""
    if not samples:
        return FramingPlan((), MODE_STATIC, 0.0, ("aucune detection de visage",))

    with_faces = [s for s in samples if s.faces]
    ratio = len(with_faces) / len(samples)
    if ratio < min_samples_ratio or len(with_faces) < 3:
        return FramingPlan(
            (), MODE_STATIC, round(ratio, 3),
            (f"visage detecte sur seulement {ratio:.0%} des images analysees -- cadrage fixe",),
        )

    targets, mode = choose_targets(
        with_faces, speaker_decisions, two_faces_min_distance_frac, vertical_bias
    )
    if not targets:
        return FramingPlan((), MODE_STATIC, 0.0, ("aucune cible exploitable",))

    smoothed = smooth_trajectory(targets, smoothing_alpha, max_speed_frac_per_s, deadzone_frac)

    span_x = max(k.cx for k in smoothed) - min(k.cx for k in smoothed)
    span_y = max(k.cy for k in smoothed) - min(k.cy for k in smoothed)
    if max(span_x, span_y) < static_movement_threshold:
        # Le sujet ne bouge pas : un cadrage fixe donne exactement le meme
        # resultat, sans expression temporelle a evaluer image par image.
        middle = smoothed[len(smoothed) // 2]
        return FramingPlan(
            (FramingKeyframe(t=smoothed[0].t, cx=middle.cx, cy=middle.cy),),
            MODE_STATIC, round(ratio, 3), ("sujet immobile -- cadrage fixe",),
        )

    reasons = [f"visage suivi sur {ratio:.0%} des images analysees"]
    if mode == MODE_BOTH:
        reasons.append("deux personnes cadrees ensemble (locuteur non identifie de facon sure)")
    elif speaker_decisions and any(d.face_index is not None for d in speaker_decisions):
        reasons.append("cadrage centre sur le locuteur actif")

    return FramingPlan(
        tuple(_decimate(smoothed, max_keyframes)), mode, round(ratio, 3), tuple(reasons)
    )
