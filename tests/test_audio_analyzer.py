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
