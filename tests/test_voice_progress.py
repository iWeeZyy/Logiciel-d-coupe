"""Ou en est la generation de la voix (voice_studio/voice_progress.py).

Aucune interface, aucune horloge reelle : le temps est un parametre, donc une
generation de dix minutes se rejoue en quelques microsecondes.

CE QUE CES TESTS DEFENDENT, avant tout : qu'aucune duree restante ne soit
affichee sans mesure derriere elle. Une barre qui ment sur le temps restant
est pire qu'une barre qui ne dit rien.
"""
from __future__ import annotations

import pytest

from voice_studio.voice_progress import (MAX_FRACTION, GenerationProgress,
                                         Report, format_duration)


class TestFormatDuration:
    @pytest.mark.parametrize("seconds,attendu", [
        (0, "0 s"),
        (1, "1 s"),
        (59, "59 s"),
        (60, "1 min"),
        (61, "1 min 01"),
        (80, "1 min 20"),
        (120, "2 min"),
        (3599, "59 min 59"),
    ])
    def test_lecture_humaine(self, seconds, attendu):
        assert format_duration(seconds) == attendu

    def test_une_duree_negative_ne_s_affiche_pas_en_negatif(self):
        """Une horloge qui recule ne doit pas produire « -3 s »."""
        assert format_duration(-5) == "0 s"


class TestSansAucuneMesure:
    """Premiere generation apres l'installation : rien n'est encore connu."""

    def test_la_barre_reste_indeterminee(self):
        progress = GenerationProgress()
        report = progress.start(0.0)
        assert report.fraction is None

    def test_aucune_duree_restante_n_est_annoncee(self):
        progress = GenerationProgress()
        progress.event({"event": "plan", "chunks": 4, "chars": 800}, 0.0)
        progress.event({"event": "loading"}, 1.0)
        report = progress.event({"event": "chunk", "index": 1, "total": 4}, 30.0)
        assert report.fraction is None
        assert "encore" not in report.label

    def test_le_temps_ecoule_lui_est_affiche(self):
        """Il est mesure, donc il peut etre montre."""
        progress = GenerationProgress()
        progress.event({"event": "loading"}, 0.0)
        report = progress.tick(75.0)
        assert "1 min 15" in report.label

    def test_l_estimation_apparait_des_le_premier_morceau_termine(self):
        progress = GenerationProgress()
        progress.event({"event": "plan", "chunks": 4, "chars": 400}, 0.0)
        progress.event({"event": "loaded", "seconds": 10.0}, 10.0)
        progress.event({"event": "chunk", "index": 1, "total": 4, "chars": 100}, 10.0)
        report = progress.event(
            {"event": "chunk_done", "index": 1, "chars": 100, "seconds": 20.0}, 30.0)
        # 400 caracteres a 0,2 s/caractere = 80 s, plus 10 s de chargement.
        assert report.fraction is not None
        assert "encore" in report.label
        assert progress.measured_rate == pytest.approx(0.2)


class TestAvecLesMesuresPrecedentes:
    """La machine a deja genere une fois : on peut estimer des la premiere
    seconde, parce qu'on parle d'une mesure faite ICI, pas d'une moyenne."""

    def test_la_barre_est_determinee_immediatement(self):
        progress = GenerationProgress(previous_load_s=20.0, previous_rate=0.1)
        progress.event({"event": "plan", "chunks": 2, "chars": 300}, 0.0)
        report = progress.event({"event": "loading"}, 0.0)
        assert report.fraction is not None
        # 20 s + 300 * 0,1 = 50 s attendues.
        assert "encore 50 s" in report.label

    def test_la_fraction_suit_le_temps_ecoule(self):
        progress = GenerationProgress(previous_load_s=20.0, previous_rate=0.1)
        progress.event({"event": "plan", "chunks": 2, "chars": 300}, 0.0)
        progress.event({"event": "loading"}, 0.0)
        assert progress.tick(25.0).fraction == pytest.approx(0.5)

    def test_la_mesure_du_jour_remplace_celle_d_hier(self):
        """Un ordinateur peut etre occupe : la generation en cours fait foi."""
        progress = GenerationProgress(previous_load_s=10.0, previous_rate=0.1)
        progress.event({"event": "plan", "chunks": 2, "chars": 200}, 0.0)
        progress.event({"event": "loaded", "seconds": 10.0}, 10.0)
        progress.event({"event": "chunk", "index": 1, "total": 2, "chars": 100}, 10.0)
        progress.event(
            {"event": "chunk_done", "index": 1, "chars": 100, "seconds": 40.0}, 50.0)
        # 0,4 s/caractere mesure, pas 0,1 : total 10 + 200*0,4 = 90 s.
        assert progress.measured_rate == pytest.approx(0.4)
        assert "encore 40 s" in progress.tick(50.0).label

    def test_un_depassement_ne_produit_pas_une_duree_negative(self):
        progress = GenerationProgress(previous_load_s=5.0, previous_rate=0.01)
        progress.event({"event": "plan", "chunks": 1, "chars": 100}, 0.0)
        progress.event({"event": "loading"}, 0.0)
        report = progress.tick(600.0)
        assert "-" not in report.label
        assert "bientôt terminé" in report.label


class TestLaBarreNeMentJamais:
    def test_elle_n_atteint_jamais_cent_pour_cent_avant_la_fin(self):
        progress = GenerationProgress(previous_load_s=1.0, previous_rate=0.001)
        progress.event({"event": "plan", "chunks": 1, "chars": 10}, 0.0)
        progress.event({"event": "loading"}, 0.0)
        assert progress.tick(9999.0).fraction == pytest.approx(MAX_FRACTION)

    def test_elle_ne_recule_pas_quand_l_estimation_s_allonge(self):
        """Une barre qui revient en arriere donne le sentiment d'un bug."""
        progress = GenerationProgress(previous_load_s=1.0, previous_rate=0.01)
        progress.event({"event": "plan", "chunks": 2, "chars": 200}, 0.0)
        progress.event({"event": "loaded", "seconds": 1.0}, 1.0)
        progress.event({"event": "chunk", "index": 1, "total": 2, "chars": 100}, 1.0)
        avant = progress.tick(2.0).fraction
        # Le morceau a pris dix fois plus longtemps que prevu.
        progress.event(
            {"event": "chunk_done", "index": 1, "chars": 100, "seconds": 20.0}, 21.0)
        assert progress.tick(21.0).fraction >= avant

    def test_la_fin_seule_met_la_barre_a_cent(self):
        progress = GenerationProgress()
        report = progress.event(
            {"event": "done", "seconds": 42.0, "duration": 12.5}, 42.0)
        assert report.fraction == 1.0
        assert report.phase == "fini"
        assert "42 s" in report.label


class TestLibelles:
    def test_le_chargement_se_nomme(self):
        progress = GenerationProgress()
        assert "Chargement" in progress.event({"event": "loading"}, 0.0).label

    def test_le_morceau_en_cours_est_compte(self):
        progress = GenerationProgress()
        report = progress.event({"event": "chunk", "index": 3, "total": 12}, 0.0)
        assert "3/12" in report.label

    def test_avant_tout_evenement_l_ecran_dit_quand_meme_quelque_chose(self):
        assert GenerationProgress().start(0.0).label


class TestMesuresARetenir:
    def test_rien_a_retenir_si_rien_n_a_ete_mesure(self):
        progress = GenerationProgress(previous_load_s=9.0, previous_rate=0.5)
        # Les valeurs precedentes ne doivent pas etre reecrites telles quelles :
        # ce ne sont pas des mesures de CETTE generation.
        assert progress.measured_load_s == 0.0
        assert progress.measured_rate == 0.0

    def test_la_vitesse_moyenne_cumule_tous_les_morceaux(self):
        progress = GenerationProgress()
        progress.event({"event": "chunk_done", "chars": 100, "seconds": 10.0}, 10.0)
        progress.event({"event": "chunk_done", "chars": 300, "seconds": 10.0}, 20.0)
        assert progress.measured_rate == pytest.approx(20.0 / 400.0)


class TestFormeDuRapport:
    def test_le_rapport_est_immuable(self):
        """Personne ne doit pouvoir corriger une fraction apres coup."""
        report = Report(0.5, "x", "narration")
        with pytest.raises(Exception):
            report.fraction = 0.9
