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


# --------------------------------------------------------------------------
# Content Score / Rewatch Score / Viral Potential (ajoutes avec l'edition
# automatique). Ils recombinent les memes mesures que le Hook Score, sans
# aucune extraction supplementaire.
# --------------------------------------------------------------------------

CLIP_SCORES = {
    "weights": {"hook": 0.5, "rewatch": 0.3, "content": 0.2},
    "params": {
        "speech_coverage_target": 0.72,
        "ideal_avg_sentence_words": 12,
        "sentence_length_tolerance": 8,
        "rewatch_intensity_scale_db": 8,
        "acceleration_scale_wps": 1.5,
    },
}


def _words(spec):
    from core.models import Word

    return [Word(text=t, start=s, end=e) for t, s, e in spec]


def _candidate_with_words(audio, text, words, start=0.0, end=45.0):
    return Candidate(start=start, end=end, text="", words=words, audio=audio, text_features=text)


def test_viral_equals_hook_score_when_no_clip_scores_config():
    # Appel historique a trois arguments : le comportement de classement d'avant
    # l'ajout des trois scores doit etre strictement preserve.
    scorer = Scorer(WEIGHTS, SCORING_PARAMS, AUDIO_STATS)
    scored = scorer.score(_candidate(AudioFeatures(rms_mean=-22.0), TextFeatures(words_per_second=2.5)))

    assert scored.scores.viral == scored.scores.total
    assert scored.scores.hook == scored.scores.total


def test_viral_is_the_weighted_mix_of_the_three_scores():
    scorer = Scorer(WEIGHTS, SCORING_PARAMS, AUDIO_STATS, clip_scores=CLIP_SCORES)
    audio = AudioFeatures(rms_mean=-20.0, rms_std=6.0, peak_count=2, intensity_rise=3.0, silence_before_s=0.6)
    text = TextFeatures(question_count=1, keyword_score_raw=2.0, words_per_second=3.0,
                        short_sentence_ratio=0.5, avg_sentence_length=11.0, strong_punctuation_count=2)
    scored = scorer.score(_candidate_with_words(audio, text, _words([("fin.", 44.0, 44.6)])))

    s = scored.scores
    expected = s.total * 0.5 + s.rewatch * 0.3 + s.content * 0.2
    assert abs(s.viral - expected) < 1e-6
    assert 0.0 <= s.viral <= 100.0


def test_rewatch_rewards_a_clip_that_ends_on_a_finished_sentence():
    scorer = Scorer(WEIGHTS, SCORING_PARAMS, AUDIO_STATS, clip_scores=CLIP_SCORES)
    audio = AudioFeatures(rms_mean=-20.0, rms_std=6.0)
    text = TextFeatures(words_per_second=3.0, short_sentence_ratio=0.5, avg_sentence_length=12.0)

    finished = scorer.score(_candidate_with_words(audio, text, _words([("voila.", 44.0, 44.6)])))
    cut_off = scorer.score(_candidate_with_words(audio, text, _words([("voila", 44.0, 44.6)])))

    assert finished.scores.rewatch > cut_off.scores.rewatch


def test_all_scores_stay_within_bounds_on_an_empty_candidate():
    scorer = Scorer(WEIGHTS, SCORING_PARAMS, AUDIO_STATS, clip_scores=CLIP_SCORES)
    scored = scorer.score(_candidate(AudioFeatures(), TextFeatures()))

    for value in (scored.scores.content, scored.scores.rewatch, scored.scores.viral):
        assert 0.0 <= value <= 100.0


def test_breakdown_built_with_total_only_stays_rankable():
    # Un ScoreBreakdown construit sans les nouveaux scores (code anterieur, stub
    # de test) doit rester classable : viral retombe sur le Hook Score.
    from core.models import ScoreBreakdown

    assert ScoreBreakdown(total=72.0).viral == 72.0
    assert ScoreBreakdown(total=72.0).hook == 72.0
