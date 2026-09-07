import pytest

from analysis.selector import select_clips
from core.models import AudioFeatures, Candidate, ScoreBreakdown, ScoredCandidate, TextFeatures
from utils.errors import InsufficientContentError


class _StubTextAnalyzer:
    """window_text/words_in_window minimalistes -- suffisant pour selector.py,
    qui ne fait que reformuler le texte/les mots sur la fenetre etendue."""

    def window_text(self, start, end):
        return f"[{start:.1f}-{end:.1f}]"

    def words_in_window(self, start, end):
        return []


def _sc(start: float, end: float, total: float) -> ScoredCandidate:
    candidate = Candidate(start=start, end=end, text="", words=[], audio=AudioFeatures(), text_features=TextFeatures())
    return ScoredCandidate(candidate=candidate, scores=ScoreBreakdown(total=total), reasons=[])


def test_overlapping_candidates_keep_only_best_score():
    candidates = [
        _sc(10.0, 55.0, 90.0),
        _sc(20.0, 65.0, 70.0),  # chevauche le premier -> doit etre rejete
        _sc(200.0, 245.0, 80.0),
    ]
    selected = select_clips(
        candidates, nb_clips=5, min_gap=20, pre_roll=0, post_roll=0,
        max_overshoot_ratio=0.2, video_duration=500.0, text_analyzer=_StubTextAnalyzer(),
    )

    starts = sorted(sc.candidate.start for sc in selected)
    assert len(selected) == 2
    assert 10.0 in starts and 200.0 in starts
    assert 20.0 not in starts


def test_min_gap_rejects_close_non_overlapping_candidates():
    candidates = [
        _sc(0.0, 45.0, 95.0),
        _sc(50.0, 95.0, 90.0),  # ne chevauche pas mais gap de 5s < min_gap=20
        _sc(200.0, 245.0, 60.0),
    ]
    selected = select_clips(
        candidates, nb_clips=5, min_gap=20, pre_roll=0, post_roll=0,
        max_overshoot_ratio=0.2, video_duration=500.0, text_analyzer=_StubTextAnalyzer(),
    )

    starts = sorted(sc.candidate.start for sc in selected)
    assert starts == [0.0, 200.0]


def test_ranking_is_by_score_descending():
    candidates = [_sc(0.0, 45.0, 50.0), _sc(100.0, 145.0, 95.0), _sc(300.0, 345.0, 70.0)]
    selected = select_clips(
        candidates, nb_clips=3, min_gap=20, pre_roll=0, post_roll=0,
        max_overshoot_ratio=0.2, video_duration=500.0, text_analyzer=_StubTextAnalyzer(),
    )
    scores = [sc.scores.total for sc in selected]
    assert scores == sorted(scores, reverse=True)


def test_pre_post_roll_extends_but_clamps_to_video_bounds():
    candidates = [_sc(2.0, 47.0, 90.0)]  # commence a 2s -- pre-roll de 10s deborderait avant 0
    selected = select_clips(
        candidates, nb_clips=1, min_gap=20, pre_roll=10, post_roll=3,
        max_overshoot_ratio=0.5, video_duration=500.0, text_analyzer=_StubTextAnalyzer(),
    )
    c = selected[0].candidate
    assert c.start == 0.0  # clampe a 0, pas de valeur negative
    assert c.end <= 50.0 + 1e-6


def test_overshoot_cap_limits_extended_duration():
    candidates = [_sc(100.0, 145.0, 90.0)]  # duree de base 45s
    selected = select_clips(
        candidates, nb_clips=1, min_gap=20, pre_roll=20, post_roll=20,
        max_overshoot_ratio=0.2, video_duration=1000.0, text_analyzer=_StubTextAnalyzer(),
    )
    c = selected[0].candidate
    max_allowed = 45.0 * 1.2
    assert (c.end - c.start) <= max_allowed + 1e-6


def test_empty_candidates_raises():
    with pytest.raises(InsufficientContentError):
        select_clips(
            [], nb_clips=5, min_gap=20, pre_roll=5, post_roll=3,
            max_overshoot_ratio=0.2, video_duration=500.0, text_analyzer=_StubTextAnalyzer(),
        )
