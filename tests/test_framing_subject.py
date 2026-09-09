"""Choisir qui cadrer : la personne au premier plan, pas l'incrustation.

Les mesures utilisees ici viennent d'un VRAI clip Twitch fourni par
l'utilisateur, ou le cadrage 9:16 coupait le sujet en deux. Le detecteur y voit
deux personnes : le streamer au premier plan (aire ~2,9 % de l'image, vu par
intermittence car il se cache le visage et se retourne) et la vignette webcam
d'un invite (aire ~1,3 %, vue 90 % du temps).

Le cadrage moyennait ces deux positions et obtenait un point qui ne designait
personne. Ces tests verrouillent la regle qui corrige cela.
"""
from __future__ import annotations

import pytest

from editing.subject import (
    MIN_AREA_RATIO,
    build_tracks,
    keep_subject_faces,
    subject_tracks,
)
from video.face_detector import FaceBox, FaceSample


def face(cx: float, cy: float, area: float, confidence: float = 0.9) -> FaceBox:
    side = area ** 0.5
    return FaceBox(x=cx - side / 2, y=cy - side / 2, w=side, h=side, confidence=confidence)


def sample(t: float, *boxes: FaceBox) -> FaceSample:
    ordered = tuple(sorted(boxes, key=lambda b: b.area, reverse=True))
    return FaceSample(t=t, faces=ordered, mouth_activity=tuple(0.0 for _ in ordered))


# Reconstitution du clip reel : la vignette est presente presque partout, le
# sujet seulement par moments, mais il est plus de deux fois plus grand.
VIGNETTE = lambda: face(0.09, 0.56, 0.0129, 0.98)
SUJET = lambda: face(0.59, 0.45, 0.0291, 0.95)

CLIP_REEL = [
    sample(0.0, VIGNETTE()),
    sample(1.0, VIGNETTE()),
    sample(2.0, VIGNETTE(), face(0.59, 0.64, 0.0189, 0.63)),
    sample(3.0, VIGNETTE()),
    sample(4.0, VIGNETTE()),
    sample(5.0, VIGNETTE()),
    sample(6.0, VIGNETTE()),
    sample(7.0),
    sample(8.0),
    sample(9.0, VIGNETTE(), face(0.55, 0.46, 0.0167, 0.74)),
    sample(10.0, VIGNETTE(), face(0.54, 0.62, 0.0178, 0.79)),
    sample(11.0, VIGNETTE()),
    sample(12.0, VIGNETTE()),
    sample(13.0, VIGNETTE()),
    sample(14.0, VIGNETTE()),
    sample(15.0, VIGNETTE()),
    sample(16.0, VIGNETTE(), face(0.60, 0.34, 0.0244, 0.67)),
    sample(17.0, VIGNETTE()),
    sample(18.0, VIGNETTE(), face(0.58, 0.39, 0.0291, 0.98)),
    sample(19.0, VIGNETTE(), SUJET()),
    sample(20.0, VIGNETTE(), face(0.61, 0.46, 0.0292, 1.0)),
]


class TestPistes:
    def test_les_deux_personnes_sont_distinguees(self):
        tracks = build_tracks(CLIP_REEL)
        centres = sorted(round(t.cx, 1) for t in tracks if t.presence >= 0.10)
        assert 0.1 in centres and 0.6 in centres, centres

    def test_la_vignette_est_vue_bien_plus_souvent_que_le_sujet(self):
        """C'est tout le piege : trancher a la frequence choisirait la vignette."""
        tracks = {round(t.cx, 1): t for t in build_tracks(CLIP_REEL)}
        assert tracks[0.1].presence > tracks[0.6].presence

    def test_le_sujet_est_pourtant_bien_plus_grand(self):
        # Le sujet bouge : ses observations forment plusieurs petits groupes.
        # On compare donc le PLUS GRAND visage du cote droit a la vignette, et
        # non un sous-groupe pris au hasard par un arrondi.
        tracks = build_tracks(CLIP_REEL)
        vignette = next(t for t in tracks if t.cx < 0.3)
        sujet = max((t for t in tracks if t.cx > 0.4), key=lambda t: t.area)
        assert sujet.area > vignette.area * 1.5


class TestChoixDuSujet:
    def test_c_est_le_premier_plan_qui_est_cadre(self):
        kept = subject_tracks(build_tracks(CLIP_REEL))
        assert len(kept) == 1
        assert round(kept[0].cx, 1) == 0.6, "le cadrage doit viser le streamer"

    def test_la_vignette_est_ecartee(self):
        kept = subject_tracks(build_tracks(CLIP_REEL))
        assert all(round(t.cx, 1) != 0.1 for t in kept)

    def test_deux_personnes_cote_a_cote_sont_toutes_les_deux_gardees(self):
        """Le cas legitime ne doit pas etre casse : deux visages de taille
        comparable restent deux participants, et le cadrage groupe s'applique."""
        duo = [sample(float(i), face(0.30, 0.5, 0.024), face(0.70, 0.5, 0.021))
               for i in range(6)]
        kept = subject_tracks(build_tracks(duo))
        assert len(kept) == 2

    def test_une_personne_un_peu_plus_loin_reste_gardee(self):
        proche = [sample(float(i), face(0.35, 0.5, 0.024), face(0.65, 0.5, 0.0138))
                  for i in range(6)]
        kept = subject_tracks(build_tracks(proche))
        assert len(kept) == 2, "0,0138 / 0,024 = 0,58, au-dessus du seuil"

    def test_le_seuil_est_bien_celui_annonce(self):
        assert 0.4 < MIN_AREA_RATIO < 0.7

    def test_un_faux_positif_isole_ne_vole_pas_le_cadrage(self):
        avec_bruit = [sample(float(i), face(0.3, 0.5, 0.02)) for i in range(20)]
        avec_bruit.append(sample(20.0, face(0.9, 0.1, 0.05)))
        kept = subject_tracks(build_tracks(avec_bruit))
        assert len(kept) == 1
        assert round(kept[0].cx, 1) == 0.3

    def test_aucun_visage_ne_donne_aucune_piste(self):
        assert subject_tracks(build_tracks([sample(0.0), sample(1.0)])) == []


class TestFiltrage:
    def test_les_visages_ecartes_disparaissent_des_echantillons(self):
        filtres = keep_subject_faces(CLIP_REEL)
        for s in filtres:
            for box in s.faces:
                assert box.cx > 0.3, "aucune vignette ne doit subsister"

    def test_un_echantillon_sans_sujet_devient_vide_et_non_faux(self):
        """Le cadrage sait quoi faire quand il ne voit personne ; un visage faux
        le ferait partir au mauvais endroit."""
        filtres = {s.t: s for s in keep_subject_faces(CLIP_REEL)}
        assert filtres[0.0].faces == ()
        assert filtres[19.0].faces

    def test_l_activite_de_bouche_reste_alignee_sur_les_visages(self):
        for s in keep_subject_faces(CLIP_REEL):
            assert len(s.mouth_activity) == len(s.faces)

    def test_une_scene_sans_incrustation_est_rendue_telle_quelle(self):
        duo = [sample(float(i), face(0.30, 0.5, 0.024), face(0.70, 0.5, 0.021))
               for i in range(6)]
        assert keep_subject_faces(duo) == duo


class TestCadrageResultant:
    def test_le_centre_du_cadrage_vise_le_sujet_et_non_le_milieu(self):
        """Avant, la moyenne des deux positions donnait ~0,28 : le clip vertical
        coupait le streamer en deux et gardait la vignette."""
        from video.face_detector import crop_hint_from_track

        hint = crop_hint_from_track(CLIP_REEL)
        assert hint is not None
        assert hint.x_center_frac > 0.5, hint.x_center_frac

    def test_le_cadrage_suivi_ne_cadre_plus_le_vide_entre_les_deux(self):
        from editing.framing import MODE_BOTH, choose_targets

        targets, mode = choose_targets(CLIP_REEL)
        assert mode != MODE_BOTH, "la vignette ne doit pas declencher un cadrage de groupe"
        assert all(k.cx > 0.4 for k in targets), [round(k.cx, 2) for k in targets]
