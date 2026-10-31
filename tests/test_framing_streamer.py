"""Compilation de Squeezie (2026-10-31) : bandes noires incrustees dans la
source, plateau a cinq, et suivi d'une personne donnee par photo."""
from types import SimpleNamespace

from editing.framing import choose_targets
from video.face_detector import FaceBox, FaceSample
from video.face_identity import cosine, keep_target
from video.letterbox import content_span


def _box(cx, identity=-1.0, size=0.08):
    return FaceBox(x=cx - size / 2, y=0.3, w=size, h=size * 1.6, confidence=0.9,
                   identity=identity)


class TestBandesNoires:
    def test_bande_avec_logo_reste_une_bande(self):
        # 10 lignes : 3 noires (un petit logo = 5 % de pixels clairs), 4 d'image, 3 noires.
        frame = [0.05, 0.0, 0.0, 0.9, 0.8, 0.95, 0.7, 0.0, 0.05, 0.0]
        assert content_span([frame] * 6) == (3, 7)

    def test_scene_de_nuit_n_est_pas_une_bande(self):
        dark = [0.02] * 3 + [0.9] * 4 + [0.02] * 3
        bright = [0.6] * 10
        # Sombre sur une seule image sur six : ce n'est pas une bande.
        assert content_span([dark] + [bright] * 5) == (0, 10)


class TestPlateauLarge:
    FIVE = [FaceSample(t=t, faces=tuple(_box(cx) for cx in (0.12, 0.28, 0.45, 0.66, 0.82)),
                       mouth_activity=(0.0,) * 5) for t in (0.0, 0.5, 1.0)]

    def test_groupe_plus_large_que_le_cadre_jamais_le_vide_entre_deux(self):
        targets, _ = choose_targets(self.FIVE, crop_width_frac=0.16)
        centers = {round(k.cx, 2) for k in targets}
        assert centers <= {0.12, 0.28, 0.45, 0.66, 0.82}   # toujours sur un visage

    def test_sans_largeur_de_cadre_comportement_d_avant(self):
        targets, mode = choose_targets(self.FIVE)
        assert mode == "both" and round(targets[0].cx, 2) == 0.47


class TestPersonneASuivre:
    def test_suivie_quand_reconnue_sinon_inchange(self):
        samples = [FaceSample(t=0.0, faces=(_box(0.3, 0.1), _box(0.66, 0.7)),
                              mouth_activity=(0.1, 0.2)),
                   FaceSample(t=0.5, faces=(_box(0.3, 0.1), _box(0.5, 0.05)),
                              mouth_activity=(0.1, 0.2))]
        kept, presence = keep_target(samples)
        assert presence == 0.5
        assert [round(f.cx, 2) for f in kept[0].faces] == [0.66] and kept[0].mouth_activity == (0.2,)
        assert kept[1] is samples[1]                         # absente : la personne qui parle

    def test_faux_positif_isole_ignore(self):
        samples = [FaceSample(t=i * 0.5, faces=(_box(0.3, 0.1),), mouth_activity=(0.0,))
                   for i in range(10)]
        samples[3] = FaceSample(t=1.5, faces=(_box(0.3, 0.4),), mouth_activity=(0.0,))
        assert keep_target(samples) == (samples, 0.0)

    def test_cosinus(self):
        assert round(cosine([1, 0], [1, 0]), 3) == 1.0 and cosine([1, 0], [0, 1]) == 0.0


def test_photo_du_streamer_dans_la_production(tmp_path):
    from radar.weekly_top import with_face

    (tmp_path / "visages").mkdir()
    (tmp_path / "visages" / "squeezie.png").write_bytes(b"x")
    production = {"editing_overrides": {"framing": True, "intro": False}}
    config = {"faces": {"squeezie": ["visages/squeezie.png"], "joyca": ["visages/absent.png"]}}
    out = with_face(production, config, "squeezie", tmp_path)
    assert out["editing_overrides"]["framing"] == {
        "enabled": True, "subject_photos": [str(tmp_path / "visages" / "squeezie.png")]}
    assert with_face(production, config, "joyca", tmp_path) is production   # photo absente
    assert with_face(production, config, "anyme023", tmp_path) is production


def test_largeur_du_cadre():
    import pipeline
    from video import ffmpeg_utils

    settings = SimpleNamespace(target_size=lambda: (1080, 1920))
    original = ffmpeg_utils.video_resolution
    try:
        ffmpeg_utils.video_resolution = lambda p: (2560, 720)
        assert round(pipeline._crop_width_frac("x.mp4", settings), 3) == round(405 / 2560, 3)
        settings = SimpleNamespace(target_size=lambda: (1920, 1080))
        assert pipeline._crop_width_frac("x.mp4", settings) is None
    finally:
        ffmpeg_utils.video_resolution = original


class TestParoleEtPlanLarge:
    """Demande : cadrer la personne qui parle (bascule rapide), et quand
    personne ne parle, un plan large sur tous les participants."""

    FIVE = [FaceSample(t=t / 2, faces=tuple(_box(cx) for cx in (0.12, 0.28, 0.45, 0.66, 0.82)),
                       mouth_activity=(0.0,) * 5) for t in range(20)]

    TWO = [FaceSample(t=t / 2, faces=(_box(0.2), _box(0.8)), mouth_activity=(0.0, 0.0))
           for t in range(20)]

    def test_plus_de_deux_toujours_en_plan_large(self):
        from editing.framing import wide_intervals
        from editing.speaker import SpeakerDecision

        decisions = [SpeakerDecision(0.0, 10.0, 1, 0.8)]
        wide = wide_intervals(self.FIVE, decisions, [(0.0, 10.0)], crop_width_frac=0.16)
        assert len(wide) == 1 and wide[0][0] == 0.0 and wide[0][1] >= 9.5

    def test_a_deux_plan_large_quand_personne_ne_parle(self):
        from editing.framing import wide_intervals
        from editing.speaker import SpeakerDecision

        decisions = [SpeakerDecision(0.0, 10.0, 1, 0.8)]
        speech = [(0.0, 3.0), (7.0, 10.0)]                 # silence de 3 a 7 s
        wide = wide_intervals(self.TWO, decisions, speech, crop_width_frac=0.16)
        assert len(wide) == 1 and 3.0 <= wide[0][0] <= 3.5 and 6.0 <= wide[0][1] <= 7.0

    def test_seul_ou_groupe_qui_tient_dans_le_cadre_jamais_de_plan_large(self):
        from editing.framing import wide_intervals

        alone = [FaceSample(t=t / 2, faces=(_box(0.5),), mouth_activity=(0.0,)) for t in range(20)]
        assert wide_intervals(alone, [], [], crop_width_frac=0.16) == ()
        assert wide_intervals(self.FIVE, [], [], crop_width_frac=1.0) == ()

    def test_plans_trop_courts_absorbes(self):
        from editing.framing import wide_intervals
        from editing.speaker import SpeakerDecision

        decisions = [SpeakerDecision(0.0, 10.0, 1, 0.8)]
        speech = [(0.0, 4.0), (4.6, 10.0)]                 # silence de 0,6 s seulement
        assert wide_intervals(self.TWO, decisions, speech, crop_width_frac=0.16) == ()

    def test_changement_de_personne_est_un_cut(self):
        from editing.framing import FramingKeyframe, _decimate, smooth_trajectory

        targets = [FramingKeyframe(0.0, 0.2, 0.4), FramingKeyframe(0.5, 0.2, 0.4),
                   FramingKeyframe(1.0, 0.8, 0.4, cut=True), FramingKeyframe(1.5, 0.8, 0.4)]
        smoothed = smooth_trajectory(targets)
        jump = [k for k in smoothed if k.cut][0]
        before = smoothed[smoothed.index(jump) - 1]
        assert jump.cx == 0.8 and before.cx == 0.2 and jump.t - before.t < 0.05
        many = smoothed * 20
        assert sum(k.cut for k in _decimate(many, 10)) == sum(k.cut for k in many)

    def test_plan_large_cadre_le_groupe(self):
        from editing.framing import group_span

        x0, x1 = group_span(self.FIVE, [(0.0, 10.0)])
        assert x0 < 0.12 and x1 > 0.82 and 0.0 <= x0 and x1 <= 1.0

    def test_expression_ffmpeg(self):
        from video.filter_graph import wide_enable_expression

        class Same:
            def to_output_time_clamped(self, t):
                return t

        assert wide_enable_expression([(1, 2.5)], Same()) == "between(t\\,1.000\\,2.500)"


class TestPersonneACadrer:
    """Demande : sans personne a cadrer, l'image entiere sur fond flou."""

    def test_clip_sans_visage_image_entiere(self):
        from editing.framing import build_framing_plan

        empty = [FaceSample(t=t / 2, faces=(), mouth_activity=()) for t in range(10)]
        plan = build_framing_plan(empty, crop_width_frac=0.3, speech_spans=[],
                                  clip_span=(0.0, 30.0))
        assert plan.wide == ((0.0, 30.0),) and plan.wide_span == (0.0, 1.0)
        assert build_framing_plan([], crop_width_frac=0.3, clip_span=(0.0, 5.0)).wide == ((0.0, 5.0),)

    def test_paysage_inchange(self):
        from editing.framing import build_framing_plan

        empty = [FaceSample(t=t / 2, faces=(), mouth_activity=()) for t in range(10)]
        assert build_framing_plan(empty).wide == ()

    def test_passage_sans_visage_au_milieu(self):
        from editing.framing import group_span, wide_intervals

        samples = ([FaceSample(t=t / 2, faces=(_box(0.5),), mouth_activity=(0.0,)) for t in range(6)]
                   + [FaceSample(t=3 + t / 2, faces=(), mouth_activity=()) for t in range(6)]
                   + [FaceSample(t=6 + t / 2, faces=(_box(0.5),), mouth_activity=(0.0,))
                      for t in range(6)])
        wide = wide_intervals(samples, [], [(0.0, 9.0)], crop_width_frac=0.3)
        assert len(wide) == 1 and 2.5 <= wide[0][0] <= 3.0 and 5.5 <= wide[0][1] <= 6.0
        assert group_span(samples, wide) == (0.0, 1.0)


class TestWebcamDansUnCoin:
    """Jeu ou evenement + webcam dans un coin -> « Webcam en haut, jeu en
    bas » (choix de l'utilisateur) ; visages du contenu ignores."""

    @staticmethod
    def _clip(extra=(), n=20, webcam=(0.10, 0.71, 0.045), presence=1.0):
        cx, cy, size = webcam
        out = []
        for i in range(n):
            faces = []
            if i < n * presence:
                faces.append(FaceBox(x=cx - size / 2, y=cy - size / 2, w=size, h=size,
                                     confidence=0.9))
            for fx, fy, fs, every in extra:
                if i % every == 0:
                    faces.append(FaceBox(x=fx - fs / 2, y=fy - fs / 2, w=fs, h=fs, confidence=0.9))
            out.append(FaceSample(t=i / 2, faces=tuple(faces), mouth_activity=(0.0,) * len(faces)))
        return out

    def test_webcam_reconnue_malgre_les_cameras_des_joueurs(self):
        from editing.subject import corner_webcam

        hud = [(0.81, 0.83, 0.04, 2), (0.20, 0.84, 0.04, 2), (0.5, 0.4, 0.3, 7)]
        track = corner_webcam(self._clip(extra=hud))
        assert track is not None and abs(track.cx - 0.10) < 0.02

    def test_pas_de_webcam(self):
        from editing.subject import corner_webcam

        assert corner_webcam(self._clip(webcam=(0.5, 0.5, 0.2))) is None          # au centre, grand
        assert corner_webcam(self._clip(webcam=(0.9, 0.3, 0.02))) is None         # minuscule
        assert corner_webcam(self._clip(presence=0.3)) is None                    # trop rare

    def test_visage_passager_du_contenu_ignore(self):
        from editing.subject import keep_persistent_faces

        samples = self._clip(extra=[(0.5, 0.4, 0.3, 10)])          # gros plan 1 image sur 10
        kept = keep_persistent_faces(samples)
        assert all(all(f.w < 0.3 for f in s.faces) for s in kept)

    def test_jeu_sans_la_webcam(self):
        from editing.subject import FaceTrack, game_window

        aspect = 1080 / (1920 * 0.65)
        corner = FaceTrack(cx=0.09, cy=0.71, area=0.002, peak_area=0.002, count=9, presence=1.0)
        assert game_window(corner, 1280, 720, aspect, 0.06) == (0.5, 0.5, 1.0)   # deja hors champ
        middle = FaceTrack(cx=0.5, cy=0.85, area=0.004, peak_area=0.004, count=9, presence=1.0)
        cx, cy, h = game_window(middle, 1920, 1080, aspect, 0.084)
        width = h * 1080 * aspect / 1920
        assert h < 1.0                                              # zoom
        webcam_left = 0.5 - (3.5 * 0.084 + 0.01) * 1080 * 1.6 / 1920 / 2
        assert cx + width / 2 <= webcam_left + 1e-6 or cx - width / 2 >= 1 - webcam_left - 1e-6


def test_plateau_en_bande_large_jamais_webcam(monkeypatch):
    import pipeline
    from video import ffmpeg_utils

    monkeypatch.setattr(ffmpeg_utils, "video_resolution", lambda p: (2560, 720))
    assert pipeline._ultra_wide("x.mp4")
    monkeypatch.setattr(ffmpeg_utils, "video_resolution", lambda p: (1920, 1080))
    assert not pipeline._ultra_wide("x.mp4")
