import numpy as np
import soundfile as sf

from analysis.audio_analyzer import AudioAnalyzer


def _write_silence_tone_silence_wav(path, sr=16000):
    silence = np.zeros(int(sr * 1.0), dtype=np.float32)
    t = np.linspace(0, 1.0, int(sr * 1.0), endpoint=False)
    tone = (0.6 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    y = np.concatenate([silence, tone, silence])
    sf.write(path, y, sr)
    return y, sr


def test_tone_window_louder_than_silence_window(tmp_path):
    wav_path = str(tmp_path / "test.wav")
    _write_silence_tone_silence_wav(wav_path)

    analyzer = AudioAnalyzer(wav_path, hop_length_ms=20, compute_pitch=False)

    silence_features = analyzer.analyze_window(0.0, 1.0)
    tone_features = analyzer.analyze_window(1.0, 2.0)

    assert tone_features.rms_mean > silence_features.rms_mean


def test_silence_before_tone_is_detected():
    import tempfile
    import os

    with tempfile.TemporaryDirectory() as d:
        wav_path = os.path.join(d, "test.wav")
        _write_silence_tone_silence_wav(wav_path)

        analyzer = AudioAnalyzer(wav_path, hop_length_ms=20, compute_pitch=False, silence_threshold_db=-35)
        features = analyzer.analyze_window(1.0, 2.0)

        # Le silence dure ~1s avant le debut de la fenetre -- on doit en detecter
        # une bonne partie (au moins 0.5s, marge pour l'attaque du signal).
        assert features.silence_before_s > 0.5


def test_intensity_rise_positive_across_silence_to_tone_transition():
    import tempfile
    import os

    with tempfile.TemporaryDirectory() as d:
        wav_path = os.path.join(d, "test.wav")
        _write_silence_tone_silence_wav(wav_path)

        analyzer = AudioAnalyzer(wav_path, hop_length_ms=20, compute_pitch=False)
        # Fenetre a cheval sur la transition silence -> tonalite.
        features = analyzer.analyze_window(0.5, 1.5)

        assert features.intensity_rise > 0


class TestLaStrategieDeHauteur:
    """`librosa.pyin` etait relance sur l'audio BRUT de chaque fenetre, alors
    que les fenetres se chevauchent largement.

    Mesure sur ce sandbox, fenetre de 45 s : 6,25 s PAR FENETRE, soit environ
    750 s pour les 118 fenetres d'une video de 30 minutes -- pour une
    composante qui pese 20 % du seul score audio. Apres correction : 134 s pour
    la meme grille, et 167 s avec sept fois plus de fenetres, puisque le cout
    ne depend plus de leur nombre.

    Le precalcul global a ete verifie contre le calcul par fenetre sur un signal
    a hauteur variable : ecart median 1,5 %, maximum 2,2 %, correlation 0,9988.
    """

    def _analyseur(self, duree=600.0):
        from analysis.audio_analyzer import AudioAnalyzer

        # On n'a pas besoin d'un vrai fichier pour tester la DECISION : on
        # construit l'objet sans passer par __init__, puisque seuls la duree et
        # les reglages de hauteur entrent en jeu.
        analyseur = AudioAnalyzer.__new__(AudioAnalyzer)
        analyseur.compute_pitch = True
        analyseur.duration = duree
        analyseur._pitch_precompute_wanted = False
        analyseur._f0_track = None
        analyseur._pitch_windows = 0
        return analyseur

    def test_une_seule_fenetre_ne_declenche_pas_le_precalcul(self):
        """Le chemin du Radar : un clip, une fenetre. Calculer la piste entiere
        pour elle coutrait bien plus cher."""
        a = self._analyseur(1800.0)
        a.plan_pitch(1, 45.0)
        assert a._pitch_precompute_wanted is False

    def test_beaucoup_de_fenetres_declenchent_le_precalcul(self):
        a = self._analyseur(1800.0)
        a.plan_pitch(118, 45.0)
        assert a._pitch_precompute_wanted is True

    def test_le_seuil_est_un_rapport_et_non_un_compte(self):
        """Vingt fenetres sur une source de 30 minutes ne valent pas vingt
        fenetres sur une source de 3 minutes."""
        longue = self._analyseur(1800.0)
        longue.plan_pitch(10, 45.0)
        courte = self._analyseur(120.0)
        courte.plan_pitch(10, 45.0)
        assert longue._pitch_precompute_wanted is False
        assert courte._pitch_precompute_wanted is True

    def test_hauteur_coupee_aucun_precalcul(self):
        a = self._analyseur(1800.0)
        a.compute_pitch = False
        a.plan_pitch(500, 45.0)
        assert a._pitch_precompute_wanted is False

    def test_un_plan_absurde_ne_leve_pas(self):
        a = self._analyseur(1800.0)
        a.plan_pitch(0, 45.0)
        a.plan_pitch(-3, 45.0)
        a.duration = 0.0
        a.plan_pitch(100, 45.0)
        assert a._pitch_precompute_wanted is False

    def test_l_ecart_ne_compte_que_les_trames_voisees(self):
        """Sans ce filtre, les pauses recoivent une hauteur arbitraire et
        l'ecart-type explose. C'est pour cela que `librosa.yin`, soixante fois
        plus rapide, ne peut pas remplacer `pyin` : sur un signal a hauteur
        variable il renvoyait des valeurs cinq fois trop grandes."""
        import numpy as np

        from analysis.audio_analyzer import AudioAnalyzer

        f0 = np.array([100.0, 102.0, 98.0, 900.0, 950.0])
        voisees = np.array([True, True, True, False, False])
        avec_filtre = AudioAnalyzer._pitch_spread(f0, voisees)
        sans_filtre = AudioAnalyzer._pitch_spread(f0, None)
        assert avec_filtre < 5.0
        assert sans_filtre > 100.0

    def test_moins_de_trois_valeurs_donne_zero(self):
        import numpy as np

        from analysis.audio_analyzer import AudioAnalyzer

        assert AudioAnalyzer._pitch_spread(np.array([100.0, 102.0]), None) == 0.0
        assert AudioAnalyzer._pitch_spread(np.array([]), None) == 0.0
        assert AudioAnalyzer._pitch_spread(None, None) == 0.0

    def test_les_nan_sont_ecartes(self):
        import numpy as np

        from analysis.audio_analyzer import AudioAnalyzer

        f0 = np.array([100.0, np.nan, 102.0, 98.0, np.nan])
        assert AudioAnalyzer._pitch_spread(f0, None) > 0.0
