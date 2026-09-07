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
from core.text_utils import ends_sentence

# Utilise quand aucun bloc clip_scores n'est fourni (appel historique a trois
# arguments) : le potentiel viral vaut alors exactement le Hook Score, donc le
# comportement d'avant l'ajout des trois scores est preserve a l'identique.
_DEFAULT_CLIP_WEIGHTS = {"hook": 1.0, "rewatch": 0.0, "content": 0.0}


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
    def __init__(self, weights: dict, scoring_params: dict, audio_stats: dict,
                 clip_scores: dict | None = None):
        self.weights = weights
        self.p = scoring_params
        self.audio_mean_db = audio_stats.get("mean_db", -40.0)
        self.audio_p90_db = audio_stats.get("p90_db", -20.0)

        clip_scores = clip_scores or {}
        self.clip_weights = clip_scores.get("weights") or dict(_DEFAULT_CLIP_WEIGHTS)
        self.clip_params = clip_scores.get("params", {})

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

    # ---------- Content / Rewatch / Viral ----------
    #
    # Ces trois scores ne declenchent AUCUNE extraction supplementaire : ils
    # recombinent les mesures deja calculees pour le Hook Score (mots-cles,
    # densite de parole, structure des phrases, variation d'intensite, mots
    # horodates). Ce sont des heuristiques explicables, pas une prediction de
    # viralite reelle -- aucune donnee de performance n'existe pour calibrer ca.

    def _sentence_structure_score(self, c: Candidate) -> float:
        avg = c.text_features.avg_sentence_length
        if avg <= 0:
            return 0.0
        ideal = self.clip_params.get("ideal_avg_sentence_words", 12)
        tolerance = max(1.0, self.clip_params.get("sentence_length_tolerance", 8))
        closeness = max(0.0, 1.0 - abs(avg - ideal) / tolerance)
        punctuation = _clip(c.text_features.strong_punctuation_count * 25.0)
        return _clip(70.0 * closeness + 0.30 * punctuation)

    def _content_score(self, c: Candidate) -> float:
        return _clip(
            0.40 * self._keyword_score(c)
            + 0.35 * self._speech_density_score(c)
            + 0.25 * self._sentence_structure_score(c)
        )

    def _speech_coverage_score(self, c: Candidate) -> float:
        """Part du clip reellement occupee par de la parole -- un passage
        parseme de temps morts se revoit mal."""
        if not c.words or c.duration <= 0:
            return 0.0
        spoken = sum(max(0.0, w.end - w.start) for w in c.words)
        target = max(0.05, self.clip_params.get("speech_coverage_target", 0.72))
        return _clip((spoken / c.duration) / target * 100.0)

    @staticmethod
    def _ending_completeness_score(c: Candidate) -> float:
        """Un clip qui s'arrete sur une phrase terminee se revoit ; un clip
        coupe au milieu d'un mot donne surtout envie de fermer."""
        if not c.words:
            return 40.0
        return 100.0 if ends_sentence(c.words[-1].text) else 40.0

    def _rewatch_score(self, c: Candidate) -> float:
        intensity_scale = max(1e-6, self.clip_params.get("rewatch_intensity_scale_db", 8))
        accel_scale = max(1e-6, self.clip_params.get("acceleration_scale_wps", 1.5))

        coverage = self._speech_coverage_score(c)
        ending = self._ending_completeness_score(c)
        variation = _clip(c.audio.rms_std / intensity_scale * 100.0)
        short_sentences = _clip(c.text_features.short_sentence_ratio * 100.0)
        acceleration = _clip(50.0 + c.text_features.speech_rate_acceleration / accel_scale * 50.0)

        return _clip(
            0.30 * coverage
            + 0.25 * ending
            + 0.20 * variation
            + 0.15 * short_sentences
            + 0.10 * acceleration
        )

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

        content = self._content_score(candidate)
        rewatch = self._rewatch_score(candidate)
        viral = _clip(
            total * self.clip_weights.get("hook", 0)
            + rewatch * self.clip_weights.get("rewatch", 0)
            + content * self.clip_weights.get("content", 0)
        )

        breakdown = ScoreBreakdown(
            audio=audio,
            keywords=keywords,
            questions=questions,
            speech_density=density,
            silence_build_up=silence_bu,
            intensity=intensity,
            total=total,
            content=content,
            rewatch=rewatch,
            viral=viral,
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
        if s.rewatch >= threshold:
            reasons.append("bon potentiel de revisionnage (rythme soutenu, fin nette)")
        if s.content >= threshold:
            reasons.append("propos dense et structure")
        if not reasons:
            reasons.append("score reparti sans facteur dominant unique")
        return reasons
