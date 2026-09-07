"""Analyse audio locale (RMS/energie/pics/silences/hauteur) par fenetre temporelle.

Tout est calcule une seule fois sur l'enveloppe RMS complete du wav extrait
(video/audio_extractor.py), puis analyze_window() decoupe simplement dans les
tableaux precalcules -- pas de re-traitement du signal par fenetre candidate.
"""
from __future__ import annotations

import numpy as np
import soundfile as sf

from core.logging_setup import get_logger
from core.models import AudioFeatures

logger = get_logger()

_EPS = 1e-10


class AudioAnalyzer:
    def __init__(self, wav_path: str, hop_length_ms: int = 20, compute_pitch: bool = True,
                 silence_threshold_db: float = -35.0, peak_prominence_db: float = 6.0):
        self.y, self.sr = sf.read(wav_path, dtype="float32", always_2d=False)
        if self.y.ndim > 1:
            self.y = self.y.mean(axis=1)

        self.hop_length = max(1, int(self.sr * hop_length_ms / 1000))
        self.compute_pitch = compute_pitch
        self.silence_threshold_db = silence_threshold_db
        self.peak_prominence_db = peak_prominence_db

        self.rms = self._compute_rms(self.y, self.hop_length)
        self.rms_db = 20.0 * np.log10(self.rms + _EPS)
        self.frame_times = np.arange(len(self.rms)) * self.hop_length / self.sr

        # Reference globale pour normaliser le score audio plus tard (scoring.py).
        self.global_rms_db_mean = float(np.mean(self.rms_db)) if len(self.rms_db) else -60.0
        self.global_rms_db_p90 = float(np.percentile(self.rms_db, 90)) if len(self.rms_db) else -30.0

        self.duration = len(self.y) / self.sr if self.sr else 0.0

    @staticmethod
    def _compute_rms(y: np.ndarray, hop_length: int) -> np.ndarray:
        # Frames non chevauchantes (frame_length == hop_length) : un chevauchement
        # aurait fait "voir" a une frame quelques ms dans le futur, ce qui contamine
        # silencieusement la detection de silence juste avant une transition
        # silence -> son (le premier silence_before_s trouve devient 0 au lieu de
        # ~1s). Trouve via le test tests/test_audio_analyzer.py::test_silence_before_tone_is_detected.
        frame_length = hop_length
        if len(y) < frame_length:
            return np.array([float(np.sqrt(np.mean(y ** 2))) if len(y) else 0.0])
        n_frames = 1 + (len(y) - frame_length) // hop_length
        out = np.empty(n_frames, dtype=np.float64)
        for i in range(n_frames):
            seg = y[i * hop_length: i * hop_length + frame_length]
            out[i] = np.sqrt(np.mean(seg.astype(np.float64) ** 2))
        return out

    def _slice(self, start: float, end: float) -> tuple[int, int]:
        i0 = int(np.searchsorted(self.frame_times, start))
        i1 = int(np.searchsorted(self.frame_times, end))
        i0 = max(0, min(i0, len(self.rms_db)))
        i1 = max(i0, min(i1, len(self.rms_db)))
        return i0, i1

    def mean_db(self, start: float, end: float) -> float:
        """Niveau moyen (dB) sur un intervalle. Utilise pour reperer les mots
        prononces plus fort que le reste du clip (mise en evidence des
        sous-titres) -- reutilise l'enveloppe RMS deja calculee, aucune
        analyse audio supplementaire."""
        i0, i1 = self._slice(start, end)
        if i1 <= i0:
            return float(self.global_rms_db_mean)
        return float(np.mean(self.rms_db[i0:i1]))

    def _count_peaks(self, db_slice: np.ndarray) -> int:
        if len(db_slice) < 3:
            return 0
        try:
            from scipy.signal import find_peaks

            peaks, _ = find_peaks(db_slice, prominence=self.peak_prominence_db)
            return int(len(peaks))
        except Exception:
            return 0

    def _silence_before(self, start: float, lookback_s: float = 3.0) -> float:
        i_start = int(np.searchsorted(self.frame_times, start))
        i_lookback = int(np.searchsorted(self.frame_times, max(0.0, start - lookback_s)))
        silence_frames = 0
        for i in range(i_start - 1, i_lookback - 1, -1):
            if i < 0 or i >= len(self.rms_db):
                break
            if self.rms_db[i] < self.silence_threshold_db:
                silence_frames += 1
            else:
                break
        return silence_frames * self.hop_length / self.sr

    def _silence_after(self, end: float, lookahead_s: float = 3.0) -> float:
        i_end = int(np.searchsorted(self.frame_times, end))
        i_lookahead = int(np.searchsorted(self.frame_times, min(self.duration, end + lookahead_s)))
        silence_frames = 0
        for i in range(i_end, min(i_lookahead, len(self.rms_db))):
            if self.rms_db[i] < self.silence_threshold_db:
                silence_frames += 1
            else:
                break
        return silence_frames * self.hop_length / self.sr

    def _pitch_variation(self, start: float, end: float) -> float:
        if not self.compute_pitch:
            return 0.0
        i0 = int(start * self.sr)
        i1 = int(end * self.sr)
        segment = self.y[max(0, i0):min(len(self.y), i1)]
        if len(segment) < self.sr * 0.5:
            return 0.0
        try:
            import librosa

            f0, voiced_flag, _ = librosa.pyin(
                segment.astype(np.float32),
                fmin=librosa.note_to_hz("C2"),
                fmax=librosa.note_to_hz("C6"),
                sr=self.sr,
            )
            voiced = f0[voiced_flag] if voiced_flag is not None else f0[~np.isnan(f0)]
            voiced = voiced[~np.isnan(voiced)]
            if len(voiced) < 3:
                return 0.0
            return float(np.std(voiced))
        except Exception as e:
            logger.debug(f"Analyse de hauteur (pitch) ignoree pour [{start:.1f},{end:.1f}] : {e}")
            return 0.0

    def analyze_window(self, start: float, end: float) -> AudioFeatures:
        i0, i1 = self._slice(start, end)
        db_slice = self.rms_db[i0:i1]

        if len(db_slice) == 0:
            return AudioFeatures()

        rms_mean = float(np.mean(db_slice))
        rms_std = float(np.std(db_slice))
        peak_count = self._count_peaks(db_slice)

        mid = len(db_slice) // 2
        first_half = db_slice[:mid] if mid > 0 else db_slice
        second_half = db_slice[mid:] if mid > 0 else db_slice
        intensity_rise = float(np.mean(second_half) - np.mean(first_half))

        silence_before = self._silence_before(start)
        silence_after = self._silence_after(end)
        pitch_variation = self._pitch_variation(start, end)

        return AudioFeatures(
            rms_mean=rms_mean,
            rms_std=rms_std,
            peak_count=peak_count,
            intensity_rise=intensity_rise,
            silence_before_s=silence_before,
            silence_after_s=silence_after,
            pitch_variation=pitch_variation,
        )
