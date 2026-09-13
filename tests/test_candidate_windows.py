"""Ou commencent et finissent les fenetres candidates, et ce que ca coute.

LE DEFAUT CORRIGE. Les fenetres etaient posees sur une grille reguliere : avec
un clip de 45 s et stride_ratio 0,33, le pas vaut 14,85 s, donc une fenetre ne
pouvait commencer qu'a 0, 14,85, 29,7... quelle que soit la parole. Or deux
composantes du score lisent directement ces bornes -- le silence AVANT la
fenetre (plancher a 10 sur 100 si elle demarre en pleine phrase, 100 apres une
pause) et la completude de la derniere phrase (100 ou 40).

MESURE sur 759 s de parole (1702 mots, 220 phrases, clip de 45 s) :

                                      grille seule   + phrases
  fenetres sans silence devant          45/50 (90 %)  45/263 (17 %)
  fenetres finissant sur une phrase     17/50 (34 %)  230/263 (87 %)

Et sur une transcription ou un moment fort est place expres entre deux points
de grille, a 22 s : la grille choisissait un passage de bavardage a 104 s
(viral 59,2), l'ancrage choisit 17,5 -> 59,1 s (viral 68,8), qui contient le
moment fort.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from analysis.hook_detector import anchored_windows, generate_candidates, grid_windows


@dataclass
class Phrase:
    """Le strict minimum de SentenceSpan qu'utilise anchored_windows."""
    start: float
    end: float


def phrases(bornes) -> list[Phrase]:
    return [Phrase(a, b) for a, b in bornes]


class TestLaGrille:
    def test_le_pas_suit_le_ratio(self):
        fenetres = grid_windows(759.0, 45.0, 0.33)
        assert len(fenetres) == 50
        assert fenetres[1][0] == pytest.approx(14.85, abs=0.01)

    def test_une_source_plus_courte_que_le_clip_donne_une_fenetre(self):
        assert grid_windows(30.0, 45.0, 0.33) == [(0.0, 30.0)]

    def test_une_fin_trop_courte_est_ecartee(self):
        """Une fenetre de moins de la moitie de la duree demandee ne fait pas
        un clip exploitable."""
        for start, end in grid_windows(100.0, 45.0, 0.33):
            assert end - start >= 45.0 * 0.5


class TestLesFenetresCalees:
    def test_chaque_fenetre_commence_sur_un_debut_de_phrase(self):
        bornes = [(0.0, 3.0), (3.5, 8.0), (9.0, 14.0), (15.0, 22.0), (23.0, 30.0)]
        debuts = {a for a, _ in bornes}
        for start, _end in anchored_windows(phrases(bornes), 40.0, 20.0, 0.3):
            assert start in debuts

    def test_chaque_fenetre_finit_sur_une_fin_de_phrase(self):
        bornes = [(0.0, 3.0), (3.5, 8.0), (9.0, 14.0), (15.0, 22.0), (23.0, 30.0)]
        fins = {b for _, b in bornes}
        for _start, end in anchored_windows(phrases(bornes), 40.0, 20.0, 0.3):
            assert end in fins

    def test_la_fenetre_prend_la_derniere_phrase_qui_TIENT(self):
        """Pas la premiere qui depasse : le clip demande 20 s, la phrase qui
        finit a 22 s ne rentre pas."""
        bornes = [(0.0, 3.0), (3.5, 8.0), (9.0, 14.0), (15.0, 22.0)]
        premiere = anchored_windows(phrases(bornes), 40.0, 20.0, 0.3)[0]
        assert premiere == (0.0, 14.0)

    def test_une_phrase_plus_longue_que_le_clip_est_coupee(self):
        """Elle ne peut pas etre refermee proprement : on coupe a la duree
        demandee, exactement comme la grille l'aurait fait."""
        fenetres = anchored_windows(phrases([(0.0, 60.0)]), 100.0, 20.0, 0.3)
        assert fenetres == [(0.0, 20.0)]

    def test_une_fenetre_trop_courte_est_ecartee(self):
        """Une phrase isolee suivie d'un long silence ne doit pas produire un
        clip de trois secondes quand on en demande vingt."""
        bornes = [(0.0, 3.0), (50.0, 53.0)]
        fenetres = anchored_windows(phrases(bornes), 60.0, 20.0, 0.6)
        assert fenetres == []

    def test_rien_ne_depasse_la_duree_de_la_video(self):
        bornes = [(0.0, 3.0), (4.0, 9.0)]
        for _start, end in anchored_windows(phrases(bornes), 7.0, 20.0, 0.1):
            assert end <= 7.0

    def test_sans_phrase_aucune_fenetre(self):
        assert anchored_windows([], 100.0, 20.0, 0.6) == []

    def test_une_phrase_de_duree_nulle_est_ignoree(self):
        assert anchored_windows(phrases([(5.0, 5.0)]), 100.0, 20.0, 0.1) == []


class FauxTexte:
    def __init__(self, mots_par_fenetre=50):
        self.mots_par_fenetre = mots_par_fenetre

    def analyze_window(self, start, end):
        from core.models import TextFeatures
        return "du texte", TextFeatures(word_count=self.mots_par_fenetre)

    def words_in_window(self, start, end):
        return []


class FauxAudio:
    def __init__(self):
        self.plan = None

    def plan_pitch(self, count, duration):
        self.plan = (count, duration)

    def analyze_window(self, start, end):
        from core.models import AudioFeatures
        return AudioFeatures()


class TestLesDeuxSourcesDeFenetres:
    BORNES = [(float(i) * 5.0, float(i) * 5.0 + 4.0) for i in range(40)]

    def test_desactive_donne_exactement_la_grille(self):
        """LE FILET : le comportement d'avant doit etre recuperable a
        l'identique."""
        attendu = grid_windows(200.0, 45.0, 0.33)
        produits = generate_candidates(FauxAudio(), FauxTexte(), 200.0, 45.0, 0.33, 8,
                                       sentences=phrases(self.BORNES),
                                       anchored={"enabled": False})
        assert [(c.start, c.end) for c in produits] == attendu

    def test_sans_phrases_le_reglage_ne_change_rien(self):
        attendu = grid_windows(200.0, 45.0, 0.33)
        produits = generate_candidates(FauxAudio(), FauxTexte(), 200.0, 45.0, 0.33, 8,
                                       sentences=None,
                                       anchored={"enabled": True})
        assert [(c.start, c.end) for c in produits] == attendu

    def test_l_ancrage_ajoute_des_fenetres(self):
        grille = generate_candidates(FauxAudio(), FauxTexte(), 200.0, 45.0, 0.33, 8,
                                     sentences=phrases(self.BORNES),
                                     anchored={"enabled": False})
        avec = generate_candidates(FauxAudio(), FauxTexte(), 200.0, 45.0, 0.33, 8,
                                   sentences=phrases(self.BORNES),
                                   anchored={"enabled": True, "min_duration_ratio": 0.6})
        assert len(avec) > len(grille)

    def test_la_grille_reste_en_tete_et_dans_son_ordre(self):
        """Son ordre historique est preserve : un changement d'ordre changerait
        les egalites de score."""
        grille = grid_windows(200.0, 45.0, 0.33)
        avec = generate_candidates(FauxAudio(), FauxTexte(), 200.0, 45.0, 0.33, 8,
                                   sentences=phrases(self.BORNES),
                                   anchored={"enabled": True})
        assert [(c.start, c.end) for c in avec][:len(grille)] == grille

    def test_aucune_fenetre_en_double(self):
        avec = generate_candidates(FauxAudio(), FauxTexte(), 200.0, 45.0, 0.33, 8,
                                   sentences=phrases(self.BORNES),
                                   anchored={"enabled": True})
        bornes = [(round(c.start, 2), round(c.end, 2)) for c in avec]
        assert len(set(bornes)) == len(bornes)

    def test_on_peut_couper_la_grille(self):
        avec = generate_candidates(FauxAudio(), FauxTexte(), 200.0, 45.0, 0.33, 8,
                                   sentences=phrases(self.BORNES),
                                   anchored={"enabled": True, "keep_grid": False})
        grille = set(grid_windows(200.0, 45.0, 0.33))
        assert not (set((c.start, c.end) for c in avec) & grille - {(0.0, 45.0)})

    def test_une_fenetre_sans_parole_est_ecartee(self):
        produits = generate_candidates(FauxAudio(), FauxTexte(mots_par_fenetre=2),
                                       200.0, 45.0, 0.33, 8,
                                       sentences=phrases(self.BORNES),
                                       anchored={"enabled": True})
        assert produits == []

    def test_le_nombre_de_fenetres_est_annonce_a_l_analyseur_audio(self):
        """L'analyseur ne peut pas le deviner, et ca change sa strategie du
        tout au tout."""
        audio = FauxAudio()
        generate_candidates(audio, FauxTexte(), 200.0, 45.0, 0.33, 8,
                            sentences=phrases(self.BORNES),
                            anchored={"enabled": True})
        assert audio.plan is not None
        nombre, duree = audio.plan
        assert nombre > 0 and duree > 0
