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
