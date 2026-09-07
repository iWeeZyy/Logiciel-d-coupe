"""Cadrage intelligent : trajectoire, lissage, locuteur actif."""
from editing.framing import (
    MODE_BOTH,
    MODE_STATIC,
    MODE_TRACK,
    FramingKeyframe,
    build_framing_plan,
    smooth_trajectory,
)
from editing.speaker import SpeakerDecision, detect_active_speaker
from video.face_detector import FaceBox, FaceSample


def _face(cx, cy, size=0.2, confidence=0.9):
    return FaceBox(x=cx - size / 2, y=cy - size / 2, w=size, h=size, confidence=confidence)


def _samples(positions, interval=0.5, mouth=None):
    """positions : liste de listes de (cx, cy) -- un element par instant."""
    out = []
    for i, faces in enumerate(positions):
        boxes = tuple(_face(cx, cy) for cx, cy in faces)
        activity = tuple(mouth[i]) if mouth else tuple(0.0 for _ in boxes)
        out.append(FaceSample(t=i * interval, faces=boxes, mouth_activity=activity))
    return out


# --------------------------------------------------------------- lissage

def test_smoothing_removes_detection_jitter():
    # Un visage immobile dont la detection tremble de +/-1 % ne doit pas faire
    # bouger le cadrage.
    jitter = [0.50, 0.51, 0.49, 0.505, 0.495, 0.50]
    targets = [FramingKeyframe(t=i * 0.5, cx=x, cy=0.4) for i, x in enumerate(jitter)]

    smoothed = smooth_trajectory(targets, alpha=0.25, deadzone_frac=0.02)

    assert max(k.cx for k in smoothed) - min(k.cx for k in smoothed) < 0.005


def test_speed_limit_prevents_a_brutal_jump():
    targets = [FramingKeyframe(t=0.0, cx=0.2, cy=0.4), FramingKeyframe(t=0.5, cx=0.9, cy=0.4)]

    smoothed = smooth_trajectory(targets, alpha=1.0, max_speed_frac_per_s=0.12, deadzone_frac=0.0)

    # 0.5 s a 0.12/s -> 0.06 d'ecart maximum, pas 0.7.
    assert abs(smoothed[1].cx - smoothed[0].cx) <= 0.06 + 1e-9


# ------------------------------------------------------------------ plan

def test_a_moving_subject_produces_a_tracking_plan():
    positions = [[(0.30 + i * 0.03, 0.40)] for i in range(12)]

    plan = build_framing_plan(_samples(positions), deadzone_frac=0.005)

    assert plan.mode == MODE_TRACK
    assert not plan.is_static
    assert len(plan.keyframes) >= 2


def test_a_still_subject_falls_back_to_a_static_plan():
    positions = [[(0.5, 0.4)] for _ in range(12)]

    plan = build_framing_plan(_samples(positions))

    assert plan.is_static
    assert plan.mode == MODE_STATIC
    assert any("immobile" in r for r in plan.reasons)


def test_too_few_detections_means_no_tracking_at_all():
    positions = [[(0.5, 0.4)], [], [], [], [], [], [], []]

    plan = build_framing_plan(_samples(positions), min_samples_ratio=0.35)

    assert plan.is_static
    assert plan.keyframes == ()
    assert any("cadrage fixe" in r for r in plan.reasons)


def test_no_detection_at_all_is_not_an_error():
    plan = build_framing_plan([])

    assert plan.is_static and plan.confidence == 0.0


def test_two_distant_faces_without_a_known_speaker_are_framed_together():
    positions = [[(0.25, 0.4), (0.75, 0.4)] for _ in range(10)]
    # On ajoute un leger mouvement pour ne pas retomber sur le plan statique.
    positions = [[(0.25, 0.4), (0.75 - i * 0.01, 0.4)] for i in range(10)]

    plan = build_framing_plan(_samples(positions), deadzone_frac=0.001,
                              static_movement_threshold=0.0)

    assert plan.mode == MODE_BOTH


def test_a_known_speaker_pulls_the_frame_onto_them():
    positions = [[(0.20, 0.4), (0.80, 0.4)] for _ in range(10)]
    decisions = [SpeakerDecision(t_start=0.0, t_end=10.0, face_index=1, confidence=0.8)]

    plan = build_framing_plan(_samples(positions), speaker_decisions=decisions,
                              static_movement_threshold=0.0, deadzone_frac=0.0)

    assert plan.mode == MODE_TRACK
    # Le cadrage converge vers le visage de droite, pas vers le milieu.
    assert plan.keyframes[-1].cx > 0.5


def test_keyframes_are_capped_so_the_ffmpeg_expression_stays_reasonable():
    positions = [[(0.30 + (i % 20) * 0.01, 0.4)] for i in range(300)]

    plan = build_framing_plan(_samples(positions, interval=0.2), max_keyframes=40,
                              deadzone_frac=0.0, static_movement_threshold=0.0)

    assert len(plan.keyframes) <= 40


# ------------------------------------------------------- locuteur actif

def test_the_face_moving_in_sync_with_the_sound_is_chosen():
    # Visage de gauche : bouche active quand le son monte. Visage de droite : plat.
    energies, mouths = [], []
    for i in range(24):
        loud = (i // 3) % 2 == 0
        energies.append(-15.0 if loud else -40.0)
        mouths.append([0.20 if loud else 0.01, 0.05])

    samples = _samples([[(0.25, 0.4), (0.75, 0.4)] for _ in range(24)], mouth=mouths)
    decisions = detect_active_speaker(samples, energies, window_s=3.0, min_hold_s=0.0)

    assert decisions
    assert any(d.face_index == 0 for d in decisions)


def test_no_speaker_is_chosen_when_both_faces_look_the_same():
    energies = [-20.0 if i % 2 else -35.0 for i in range(24)]
    mouths = [[0.1, 0.1] for _ in range(24)]

    samples = _samples([[(0.25, 0.4), (0.75, 0.4)] for _ in range(24)], mouth=mouths)
    decisions = detect_active_speaker(samples, energies)

    assert all(d.face_index is None for d in decisions)


def test_a_single_face_never_triggers_speaker_detection():
    samples = _samples([[(0.5, 0.4)] for _ in range(20)])

    assert detect_active_speaker(samples, [-20.0] * 20) == []
