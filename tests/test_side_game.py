"""Webcam collee a un cote (Kamet0, 2026-11-05) : le jeu ENTIER en bas, du
bord reel de la webcam au bord oppose ; la webcam mesuree, seule, en haut."""
from __future__ import annotations

import numpy as np

from editing.framing import FramingKeyframe, FramingPlan
from editing.subject import SIDE_GAME_INSET, side_game_box
from editing.timeline import EditList
from video.cropper import PORTRAIT_SIZE
from video.filter_graph import FIT_SPLIT_WEBCAM, WEBCAM_HEIGHT_FRAC, build_video_chain

BAND = 1080 / (1920 * (1 - WEBCAM_HEIGHT_FRAC))


def test_webcam_a_gauche_le_jeu_va_de_son_bord_au_bord_droit():
    box = side_game_box((0.0, 0.54, 0.21, 0.75), 1920, 1080, BAND)
    assert box == (0.21 + SIDE_GAME_INSET, 0.0, 1.0, 1.0)


def test_webcam_a_droite_le_jeu_va_du_bord_gauche_a_son_bord():
    box = side_game_box((0.8, 0.0, 1.0, 0.3), 1920, 1080, BAND)
    assert box == (0.0, 0.0, 0.8 - SIDE_GAME_INSET, 1.0)


def test_webcam_au_milieu_ou_non_mesuree_rien_ne_change():
    assert side_game_box((0.3, 0.2, 0.6, 0.5), 1920, 1080, BAND) is None
    assert side_game_box(None, 1920, 1080, BAND) is None


def test_zone_trop_etroite_pour_remplir_la_bande_garde_le_cadrage_habituel():
    # 1920x1080, webcam jusqu'a 60 % : 768 px de jeu, plus etroit que la bande
    assert side_game_box((0.0, 0.0, 0.6, 0.3), 1920, 1080, 0.9) is None


def _chain(webcam_box=None, game_box=None):
    wc = FramingPlan((FramingKeyframe(t=0.0, cx=0.14, cy=0.67),), "static", 0.9,
                     window_h=0.41, box=webcam_box)
    gp = FramingPlan((FramingKeyframe(t=0.0, cx=0.5, cy=0.5),), "static", 1.0, box=game_box)
    return build_video_chain(edit_list=EditList.identity(0.0, 10.0), framing_plan=gp,
                             zoom_track=None, src_w=1920, src_h=1080, fps=30.0,
                             target_size=PORTRAIT_SIZE, fit=FIT_SPLIT_WEBCAM, webcam_plan=wc)


def test_le_jeu_entier_est_pose_sur_fond_flou_sans_rien_couper():
    produced = _chain(game_box=(0.2149, 0.0, 1.0, 1.0))
    import re

    w, h, x, y = map(int, re.search(r"\[gpsrc\]crop=(\d+):(\d+):(\d+):(\d+),", produced).groups())
    assert (h, y) == (1080, 0) and 410 <= x <= 414 and x + w >= 1916   # jusqu'au bord droit
    assert "force_original_aspect_ratio=decrease" in produced
    assert "vstack=inputs=2" in produced


def test_la_webcam_mesuree_est_prise_entiere_et_agrandie_deux_fois_au_plus():
    produced = _chain(webcam_box=(0.0, 0.5417, 0.2063, 0.7472))
    assert "[wcsrc]crop=396:222:0:584," in produced
    assert "[wcfgsrc]scale=792:444" in produced


def test_le_bord_d_une_webcam_ne_se_voyant_qu_en_couleur_est_mesure():
    from video.motion import _jump

    a = np.array([[40.0, 20.0, 90.0]])      # violet sombre
    b = np.array([[40.0, 70.0, 30.0]])      # vert de meme luminance a peu pres
    assert float(_jump(a, b)[0]) == 60.0


def test_hauteur_et_centre_d_une_webcam_prolongee_jusqu_au_bas():
    from pipeline import _webcam_center_y, _webcam_height

    rect = (0.0, 576 / 1080, 405 / 1920, 1.0)
    h = _webcam_height(rect, 1080, 1920)
    assert abs(h * 1080 - 405 * 10 / 16) < 1
    assert abs(_webcam_center_y(rect, h) - (576 / 1080 + h / 2)) < 1e-9


def test_le_bandeau_evite_l_image_du_jeu_et_va_dans_le_flou():
    from video.filter_graph import split_game_zone
    from video.hook_text import INTRO_ZONE, choose_top

    lo, hi = split_game_zone(1080, 1920, 1920, 1080, (0.2149, 0.0, 1.0, 1.0))
    assert 0.46 < lo < 0.48 and 0.86 < hi < 0.89
    top = choose_top([[]], 0.08, 0.37, avoid=(INTRO_ZONE, (lo, hi, 1.0)))
    assert top + 0.08 <= lo + 1e-9


def test_sans_jeu_entier_aucune_zone_ajoutee():
    from pipeline import _hook_avoid_zones

    assert _hook_avoid_zones(None, None, 1080, 1920, 1920, 1080) == ()


def test_le_bord_retenu_est_celui_ou_le_jeu_bouge_au_dela():
    """Dobby (Kamet0, 2026-11-05) : coin de mur (fort saut, decor des deux
    cotes), veste (la personne bouge en deca), puis le vrai bord (jeu au-dela)."""
    from video.motion import side_edge_index

    scores = [45, 90, 58]
    inside = [35.2, 10.8, 3.3]
    outside = [15.6, 4.3, 28.6]
    assert side_edge_index(scores, inside, outside) == 2


def test_un_contour_faible_devant_le_jeu_ne_suffit_pas():
    """Squeezie : a 282 px le jeu ne bouge qu'a peine plus (10,7 contre 5,3)."""
    from video.motion import side_edge_index

    assert side_edge_index([60, 61], [5.3, 8.0], [10.7, 52.7]) == 1


def test_rien_ne_bouge_au_dela_aucun_bord():
    from video.motion import side_edge_index

    assert side_edge_index([80, 70], [3.0, 3.0], [4.0, 5.0]) is None
    assert side_edge_index([], [], []) is None


def _fake_overlay(monkeypatch, edge=None, rect=None, floating=None, motion=10.0):
    import video.motion as motion_mod
    from video import ffmpeg_utils

    monkeypatch.setattr(ffmpeg_utils, "video_resolution", lambda p: (1920, 1080))
    monkeypatch.setattr(motion_mod, "webcam_side_edge", lambda *a, **k: edge)
    monkeypatch.setattr(motion_mod, "webcam_rect", lambda *a, **k: rect)
    monkeypatch.setattr(motion_mod, "floating_webcam_rect", lambda *a, **k: floating)
    monkeypatch.setattr(motion_mod, "background_motion", lambda *a, **k: motion)


def _face():
    from types import SimpleNamespace

    return SimpleNamespace(cx=0.21, cy=0.36, area=0.005)


def test_un_visage_dans_un_coin_sans_incrustation_n_est_pas_une_webcam(monkeypatch):
    """Une seule camera IRL, quatre personnes sur un canape (Kamet0,
    2026-11-06) : aucun bord, une scene qui bouge -- pas de mode webcam+jeu."""
    from types import SimpleNamespace

    from pipeline import _overlay_seen

    _fake_overlay(monkeypatch)
    assert not _overlay_seen("v.mp4", SimpleNamespace(start=0.0, end=10.0), _face())


def test_un_bord_mesure_ou_un_fond_fixe_confirment_la_webcam(monkeypatch):
    from types import SimpleNamespace

    from pipeline import _overlay_seen

    clip = SimpleNamespace(start=0.0, end=10.0)
    _fake_overlay(monkeypatch, edge=0.21)
    assert _overlay_seen("v.mp4", clip, _face())
    _fake_overlay(monkeypatch, rect=(0.0, 0.5, 0.2, 1.0))
    assert _overlay_seen("v.mp4", clip, _face())
    _fake_overlay(monkeypatch, motion=0.1)                 # ecran de chargement fixe
    assert _overlay_seen("v.mp4", clip, _face())


def test_image_entiere_le_bandeau_va_dans_le_flou():
    """Mode « entier » (un match diffuse, KC-T1, 2026-11-07) : l'image 16:9
    occupe 34-66 % de la hauteur, le bandeau ne doit pas la couvrir."""
    from pipeline import _hook_avoid_zones

    zones = _hook_avoid_zones(None, None, 1080, 1920, 1920, 1080, whole=True)
    (lo, hi, weight), = zones
    assert abs(lo - 0.342) < 0.01 and abs(hi - 0.658) < 0.01 and weight == 1.0
    assert _hook_avoid_zones(None, None, 1080, 1920, 1920, 1080) == ()


class TestAfficheAuMur:
    """Kamet0 (2026-11-13) : un visage dessine sur une affiche, immobile et
    detecte a 0,62, faisait passer le clip en plan large a deux."""

    @staticmethod
    def _face(cx, conf, cy=0.4):
        from video.face_detector import FaceBox
        return FaceBox(x=cx - 0.09, y=cy - 0.1, w=0.18, h=0.2, confidence=conf)

    def _samples(self, poster_conf=0.62, real_conf=0.95, real_moves=True):
        from video.face_detector import FaceSample
        out = []
        for i in range(20):
            dx = (i % 5) * 0.015 if real_moves else 0.0
            out.append(FaceSample(t=i / 2, faces=(self._face(0.57 + dx, real_conf),
                                                  self._face(0.36, poster_conf)),
                                  mouth_activity=(0.03, 0.01)))
        return out

    def test_affiche_ecartee(self):
        from editing.subject import keep_persistent_faces
        kept = keep_persistent_faces(self._samples())
        assert all(len(s.faces) == 1 and s.faces[0].cx > 0.5 for s in kept)

    def test_deux_vraies_personnes_gardees(self):
        from editing.subject import keep_persistent_faces
        kept = keep_persistent_faces(self._samples(poster_conf=0.93))
        assert all(len(s.faces) == 2 for s in kept)

    def test_sans_personne_de_reference_rien_n_est_ecarte(self):
        from editing.subject import keep_persistent_faces
        kept = keep_persistent_faces(self._samples(real_moves=False))
        assert all(len(s.faces) == 2 for s in kept)
