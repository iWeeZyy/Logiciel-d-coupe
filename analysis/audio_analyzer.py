"""Analyse audio locale (RMS/energie/pics/silences/hauteur) par fenetre temporelle.

L'enveloppe RMS est calculee une seule fois sur le wav extrait
(video/audio_extractor.py), puis analyze_window() decoupe simplement dans les
tableaux precalcules.

LA HAUTEUR FAISAIT EXCEPTION, ET C'ETAIT LE POINT LE PLUS COUTEUX DE TOUTE
L'ANALYSE. `librosa.pyin` etait relance sur l'audio BRUT de chaque fenetre
candidate, alors que les fenetres se chevauchent largement : chaque seconde
d'audio etait donc analysee trois fois avec la grille seule. Mesure sur ce
sandbox, fenetre de 45 s : 6,25 SECONDES par fenetre, soit 750 s pour les
120 fenetres d'une video de 30 minutes -- pour une composante qui pese 20 % du
seul score audio. L'en-tete de ce module affirmait pourtant qu'aucun
re-traitement n'avait lieu par fenetre : c'etait vrai du RMS, faux de la
hauteur.

DEUX CORRECTIONS, chacune mesuree :

1. `pyin` tourne avec une fenetre d'analyse plus large et un pas plus grossier
   (4096 / 1024 au lieu des defauts). Sur un signal a hauteur variable, les
   valeurs bougent de 1,5 % et le cout est divise par trois. On ne cherche
   qu'un ECART-TYPE de hauteur sur des dizaines de secondes : une resolution
   temporelle fine n'y apporte rien.
2. Au-dela de quelques fenetres, la trajectoire de hauteur est calculee UNE
   fois sur la piste entiere, puis simplement decoupee. Verifie contre le
   calcul par fenetre : ecart median de 1,5 %, maximum 2,2 %, correlation
   0,9988 -- le lissage global de pyin ne change donc pas la mesure de facon
   sensible. Cout du precalcul : 0,068 s par seconde d'audio, soit environ
   122 s pour 30 minutes, UNE fois, quel que soit le nombre de fenetres.

Le seuil existe pour ne pas penaliser l'analyse d'un CLIP UNIQUE (le chemin du
Radar, une seule fenetre) : precalculer la piste entiere pour une fenetre
coutrait plus cher que de la calculer seule. En dessous du seuil, le
comportement est celui d'avant.
"""
from __future__ import annotations

import numpy as np
import soundfile as sf

from core.logging_setup import get_logger
from core.models import AudioFeatures

logger = get_logger()

_EPS = 1e-10

# Parametres de l'estimation de hauteur. Plus larges que les defauts de librosa
# parce qu'on ne cherche qu'un ecart-type sur des dizaines de secondes : une
# resolution temporelle fine n'apporte rien et coute trois fois plus cher.
PITCH_FRAME_LENGTH = 4096
PITCH_HOP_LENGTH = 1024

# Calculer la piste entiere n'est rentable que s'il y a assez de fenetres a
# analyser. Mesure sur ce sandbox : le calcul par fenetre coute environ 0,12 s
# par seconde d'audio de fenetre, le precalcul global 0,068 s par seconde de
# piste -- soit un RAPPORT d'a peu pres 1,8. C'est ce rapport qui compte, pas
# les valeurs absolues : il vient des couts fixes de pyin et ne depend pas de
# la machine.
#
# On precalcule donc quand l'audio total des fenetres a venir depasse la duree
# de la piste divisee par ce rapport. Une seule fenetre sur une longue source
# (le chemin du Radar) reste calculee seule ; une video de 30 minutes qui donne
# 118 fenetres passe par le precalcul.
PITCH_PER_WINDOW_OVERHEAD = 1.8

# Repli quand l'appelant n'annonce rien : au-dela de ce nombre de fenetres, on
# precalcule. Sert aux usages directs de analyze_window().
PITCH_PRECOMPUTE_AFTER = 8


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

        # Trajectoire de hauteur de la piste entiere, calculee paresseusement :
        # voir l'en-tete du module pour la raison du seuil.
        self._pitch_windows = 0
        self._f0_track = None
        self._f0_voiced = None
        self._f0_times = None
        self._pitch_precompute_wanted = False

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

    def plan_pitch(self, window_count: int, window_duration: float) -> None:
        """L'appelant annonce combien de fenetres il va demander.

        Cette information ne se devine pas depuis l'analyseur, et elle change la
        bonne strategie du tout au tout : precalculer la piste entiere pour une
        seule fenetre coute bien plus cher que de la calculer seule. Sans cet
        appel, un repli par comptage prend le relais.
        """
        if not self.compute_pitch or window_count <= 0 or self.duration <= 0:
            return
        audio_des_fenetres = window_count * max(0.0, min(window_duration, self.duration))
        seuil = self.duration / PITCH_PER_WINDOW_OVERHEAD
        self._pitch_precompute_wanted = audio_des_fenetres > seuil

    @staticmethod
    def _pitch_spread(f0, voiced) -> float:
        """Ecart-type de la hauteur, sur les seules trames VOISEES.

        Le filtre sur les trames voisees n'est pas un detail : sans lui, les
        pauses et le bruit de fond reçoivent une hauteur arbitraire et l'ecart-
        type explose. C'est d'ailleurs pourquoi `librosa.yin`, soixante fois
        plus rapide, ne peut pas remplacer `pyin` ici -- il n'a pas cette
        detection, et sur un signal a hauteur variable il renvoyait des valeurs
        cinq fois trop grandes.
        """
        if f0 is None or len(f0) == 0:
            return 0.0
        values = f0[voiced] if voiced is not None else f0[~np.isnan(f0)]
        values = values[~np.isnan(values)]
        if len(values) < 3:
            return 0.0
        return float(np.std(values))

    def _compute_pitch_track(self) -> bool:
        """Calcule la trajectoire de hauteur de la piste entiere. Vrai si elle
        est disponible ensuite."""
        if self._f0_track is not None:
            return True
        try:
            import librosa

            f0, voiced, _ = librosa.pyin(
                self.y.astype(np.float32),
                fmin=librosa.note_to_hz("C2"),
                fmax=librosa.note_to_hz("C6"),
                sr=self.sr,
                frame_length=PITCH_FRAME_LENGTH,
                hop_length=PITCH_HOP_LENGTH,
            )
            self._f0_track = f0
            self._f0_voiced = voiced
            self._f0_times = np.arange(len(f0)) * PITCH_HOP_LENGTH / self.sr
            logger.info("Hauteur : trajectoire calculee une fois sur la piste entiere.")
            return True
        except Exception as e:                           # pragma: no cover
            logger.debug(f"Trajectoire de hauteur indisponible : {e}")
            return False

    def _pitch_variation(self, start: float, end: float) -> float:
        if not self.compute_pitch:
            return 0.0

        self._pitch_windows += 1
        if self._f0_track is None and (
                self._pitch_precompute_wanted
                or self._pitch_windows > PITCH_PRECOMPUTE_AFTER):
            self._compute_pitch_track()

        if self._f0_track is not None:
            i0 = int(np.searchsorted(self._f0_times, start))
            i1 = int(np.searchsorted(self._f0_times, end))
            return self._pitch_spread(self._f0_track[i0:i1],
                                      self._f0_voiced[i0:i1]
                                      if self._f0_voiced is not None else None)

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
                frame_length=PITCH_FRAME_LENGTH,
                hop_length=PITCH_HOP_LENGTH,
            )
            return self._pitch_spread(f0, voiced_flag)
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
