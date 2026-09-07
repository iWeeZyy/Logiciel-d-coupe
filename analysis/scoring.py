"""Score composite transparent : chaque sous-score est calcule 0-100 et conserve
en detail (jamais seulement le total), pour rester explicable et ajustable
(section 5 et 16 du cahier des charges).

    Hook Score = audio*w_audio + keywords*w_keywords + questions*w_questions
               + speech_density*w_speech_density + silence_build_up*w_silence_build_up
               + intensity*w_intensity

Les poids (w_*) viennent de config/settings.json -> weights et somment a 1.0
(valide par core/config_loader.py), donc le total est deja sur 0-100.
"""
from __future__ import annotations

from core.models import Candidate, ScoreBreakdown, ScoredCandidate


def _clip(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def _silence_component(silence_s: float, ideal_min: float, ideal_max: float) -> float:
    """Note en bell-curve autour d'une plage ideale de silence avant la phrase :
    ni 0s (pas d'effet de surprise) ni trop long (ca ne se sent plus comme un
    silence de mise en tension mais comme un trou)."""
    if silence_s <= 0:
        return 10.0
    if ideal_min <= silence_s <= ideal_max:
        return 100.0
    if silence_s < ideal_min:
        return 10.0 + 90.0 * (silence_s / ideal_min)
    overshoot = silence_s - ideal_max
    return max(0.0, 100.0 - overshoot * 30.0)


class Scorer:
    def __init__(self, weights: dict, scoring_params: dict, audio_stats: dict):
        self.weights = weights
        self.p = scoring_params
        self.audio_mean_db = audio_stats.get("mean_db", -40.0)
        self.audio_p90_db = audio_stats.get("p90_db", -20.0)

    def _audio_score(self, c: Candidate) -> float:
        headroom = max(1.0, self.audio_p90_db - self.audio_mean_db)
        loudness = _clip((c.audio.rms_mean - self.audio_mean_db) / headroom * 100.0)
        peaks = _clip(c.audio.peak_count * self.p.get("peak_points", 25))
        pitch = _clip(c.audio.pitch_variation / self.p.get("pitch_scale_hz", 50) * 100.0)
        return _clip(0.5 * loudness + 0.3 * peaks + 0.2 * pitch)

    def _keyword_score(self, c: Candidate) -> float:
        return _clip(c.text_features.keyword_score_raw * self.p.get("keyword_hit_points", 20))

    def _question_score(self, c: Candidate) -> float:
        return _clip(c.text_features.question_count * 100.0)

    def _speech_density_score(self, c: Candidate) -> float:
        normal_wps = self.p.get("normal_words_per_second", 2.5)
        wps_component = _clip(c.text_features.words_per_second / max(normal_wps, 1e-6) * 100.0, 0, 130)
        short_bonus = c.text_features.short_sentence_ratio * 100.0
        accel_bonus = _clip(c.text_features.speech_rate_acceleration * 20.0, -20, 20)
        return _clip(0.6 * wps_component + 0.25 * short_bonus + 0.15 * (accel_bonus + 20) / 40 * 100)

    def _silence_build_up_score(self, c: Candidate) -> float:
        silence_component = _silence_component(
            c.audio.silence_before_s,
            self.p.get("ideal_silence_before_min_s", 0.25),
            self.p.get("ideal_silence_before_max_s", 1.5),
        )
        rise_bonus = _clip(50 + c.audio.intensity_rise * (50.0 / self.p.get("intensity_rise_scale_db", 6)))
        return _clip(0.75 * silence_component + 0.25 * rise_bonus)

    def _intensity_score(self, c: Candidate) -> float:
        rise_component = _clip(50 + c.audio.intensity_rise * (50.0 / self.p.get("intensity_rise_scale_db", 6)))
        variation_component = _clip(c.audio.rms_std / self.p.get("rms_std_scale_db", 8) * 100.0)
        return _clip(0.6 * rise_component + 0.4 * variation_component)

    def score(self, candidate: Candidate) -> ScoredCandidate:
        audio = self._audio_score(candidate)
        keywords = self._keyword_score(candidate)
        questions = self._question_score(candidate)
        density = self._speech_density_score(candidate)
        silence_bu = self._silence_build_up_score(candidate)
        intensity = self._intensity_score(candidate)

        total = (
            audio * self.weights.get("audio", 0)
            + keywords * self.weights.get("keywords", 0)
            + questions * self.weights.get("questions", 0)
            + density * self.weights.get("speech_density", 0)
            + silence_bu * self.weights.get("silence_build_up", 0)
            + intensity * self.weights.get("intensity", 0)
        )

        breakdown = ScoreBreakdown(
            audio=audio,
            keywords=keywords,
            questions=questions,
            speech_density=density,
            silence_build_up=silence_bu,
            intensity=intensity,
            total=total,
        )
        reasons = self._explain(candidate, breakdown)
        return ScoredCandidate(candidate=candidate, scores=breakdown, reasons=reasons)

    @staticmethod
    def _explain(c: Candidate, s: ScoreBreakdown, threshold: float = 65.0) -> list[str]:
        reasons: list[str] = []
        if s.questions >= 100:
            reasons.append("question detectee")
        if s.keywords >= threshold and c.text_features.keyword_hits:
            top = ", ".join(sorted(c.text_features.keyword_hits, key=c.text_features.keyword_hits.get, reverse=True)[:3])
            reasons.append(f"mot(s)-cle(s) fort(s) : {top}")
        if s.audio >= threshold:
            reasons.append("volume/energie audio eleve")
        if s.speech_density >= threshold:
            reasons.append("forte densite de parole")
        if 0.15 <= c.audio.silence_before_s <= 2.0 and s.silence_build_up >= threshold:
            reasons.append(f"silence de {c.audio.silence_before_s:.2f}s avant la phrase")
        if s.intensity >= threshold:
            reasons.append("hausse/variation notable de l'intensite vocale")
        if not reasons:
            reasons.append("score reparti sans facteur dominant unique")
        return reasons
