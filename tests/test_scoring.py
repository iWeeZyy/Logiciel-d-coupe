from analysis.scoring import Scorer
from core.models import AudioFeatures, Candidate, TextFeatures

WEIGHTS = {
    "audio": 0.20, "keywords": 0.20, "questions": 0.15,
    "speech_density": 0.15, "silence_build_up": 0.15, "intensity": 0.15,
}
SCORING_PARAMS = {
    "normal_words_per_second": 2.5,
    "ideal_silence_before_min_s": 0.25,
    "ideal_silence_before_max_s": 1.5,
    "keyword_hit_points": 20,
    "peak_points": 25,
    "pitch_scale_hz": 50,
    "intensity_rise_scale_db": 6,
    "rms_std_scale_db": 8,
}
AUDIO_STATS = {"mean_db": -40.0, "p90_db": -20.0}


def _candidate(audio: AudioFeatures, text: TextFeatures) -> Candidate:
    return Candidate(start=0.0, end=45.0, text="", words=[], audio=audio, text_features=text)


def test_total_score_is_bounded_and_matches_weighted_sum():
    scorer = Scorer(WEIGHTS, SCORING_PARAMS, AUDIO_STATS)
    audio = AudioFeatures(rms_mean=-20.0, rms_std=5.0, peak_count=2, intensity_rise=3.0, silence_before_s=0.6)
    text = TextFeatures(question_count=1, keyword_score_raw=2.0, words_per_second=3.0, short_sentence_ratio=0.5)
    c = _candidate(audio, text)

    scored = scorer.score(c)

    assert 0.0 <= scored.scores.total <= 100.0
    expected_total = (
        scored.scores.audio * WEIGHTS["audio"]
        + scored.scores.keywords * WEIGHTS["keywords"]
        + scored.scores.questions * WEIGHTS["questions"]
        + scored.scores.speech_density * WEIGHTS["speech_density"]
        + scored.scores.silence_build_up * WEIGHTS["silence_build_up"]
        + scored.scores.intensity * WEIGHTS["intensity"]
    )
    assert abs(scored.scores.total - expected_total) < 1e-9


def test_silent_low_signal_candidate_scores_low():
    scorer = Scorer(WEIGHTS, SCORING_PARAMS, AUDIO_STATS)
    audio = AudioFeatures(rms_mean=-55.0, rms_std=0.5, peak_count=0, intensity_rise=0.0, silence_before_s=0.0)
    text = TextFeatures(question_count=0, keyword_score_raw=0.0, words_per_second=0.3, short_sentence_ratio=0.0)
    c = _candidate(audio, text)

    scored = scorer.score(c)

    assert scored.scores.total < 30.0


def test_strong_candidate_has_explanatory_reasons():
    scorer = Scorer(WEIGHTS, SCORING_PARAMS, AUDIO_STATS)
    audio = AudioFeatures(rms_mean=-19.0, rms_std=9.0, peak_count=3, intensity_rise=5.0, silence_before_s=0.8)
    text = TextFeatures(
        question_count=1,
        keyword_hits={"secret": 2, "incroyable": 1},
        keyword_score_raw=3.0,
        words_per_second=4.0,
        short_sentence_ratio=0.7,
    )
    c = _candidate(audio, text)

    scored = scorer.score(c)

    assert scored.scores.total > 60.0
    assert any("question" in r for r in scored.reasons)
    assert len(scored.reasons) >= 2
