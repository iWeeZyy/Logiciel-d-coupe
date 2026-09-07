from youtube.models import VideoResult
from youtube.ranking import rank_videos, score_video

WEIGHTS = {"relevance": 0.30, "popularity": 0.25, "duration_fit": 0.25, "quality_proxy": 0.20}
PARAMS = {
    "reference_view_count_log10": 6.0,
    "duration_fit_points_per_slot": 20.0,
    "ideal_duration_min_s": 600,
    "ideal_duration_max_s": 5400,
}


def _video(**overrides) -> VideoResult:
    base = dict(
        video_id="abc12345678",
        title="Podcast entrepreneuriat francais",
        channel_id="UC123",
        channel_title="Chaine Test",
        published_at="2024-01-01T00:00:00Z",
        description="Un podcast sur l'entrepreneuriat en France",
        thumbnail_url="",
    )
    base.update(overrides)
    return VideoResult(**base)


def test_unknown_view_count_scores_zero_popularity_not_guessed():
    video = _video(view_count=None, duration_seconds=3600)
    result = score_video(video, "podcast entrepreneuriat francais", 0, 1, 45, 20, WEIGHTS, PARAMS)
    assert result.score.popularity == 0.0


def test_more_views_scores_higher_popularity():
    low = _video(view_count=100, duration_seconds=3600)
    high = _video(view_count=1_000_000, duration_seconds=3600)
    r_low = score_video(low, "q", 0, 2, 45, 20, WEIGHTS, PARAMS)
    r_high = score_video(high, "q", 0, 2, 45, 20, WEIGHTS, PARAMS)
    assert r_high.score.popularity > r_low.score.popularity


def test_video_too_short_for_a_single_clip_has_zero_duration_fit():
    video = _video(duration_seconds=30)  # plus court que clip_duration + min_gap
    result = score_video(video, "q", 0, 1, 45, 20, WEIGHTS, PARAMS)
    assert result.score.duration_fit == 0.0


def test_video_fitting_many_slots_scores_high_duration_fit():
    video = _video(duration_seconds=3600)  # 1h -> 3600 / (45+20) = 55 slots
    result = score_video(video, "q", 0, 1, 45, 20, WEIGHTS, PARAMS)
    assert result.score.duration_fit == 100.0  # plafonne


def test_relevance_rewards_keyword_overlap_and_top_rank():
    on_topic = _video(title="Podcast entrepreneuriat francais : le guide")
    off_topic = _video(title="Recette de tarte aux pommes")
    r_on = score_video(on_topic, "podcast entrepreneuriat francais", 0, 10, 45, 20, WEIGHTS, PARAMS)
    r_off = score_video(off_topic, "podcast entrepreneuriat francais", 9, 10, 45, 20, WEIGHTS, PARAMS)
    assert r_on.score.relevance > r_off.score.relevance


def test_quality_proxy_prefers_ideal_duration_range():
    ideal = _video(duration_seconds=1800)  # 30 min, dans [600, 5400]
    too_short = _video(duration_seconds=60)
    too_long = _video(duration_seconds=20000)
    r_ideal = score_video(ideal, "q", 0, 1, 45, 20, WEIGHTS, PARAMS)
    r_short = score_video(too_short, "q", 0, 1, 45, 20, WEIGHTS, PARAMS)
    r_long = score_video(too_long, "q", 0, 1, 45, 20, WEIGHTS, PARAMS)
    assert r_ideal.score.quality_proxy == 100.0
    assert r_ideal.score.quality_proxy > r_short.score.quality_proxy
    assert r_ideal.score.quality_proxy > r_long.score.quality_proxy


def test_rank_videos_sort_by_potential_vs_views_can_differ():
    # Peu de vues mais une duree ideale et un bon nombre de creneaux exploitables.
    low_views_good_fit = _video(video_id="aaaaaaaaaaa", view_count=10, duration_seconds=1800)
    # Enormement de vues mais trop courte pour fournir un seul clip de 45s+20s de gap.
    high_views_bad_fit = _video(video_id="bbbbbbbbbbb", view_count=5_000_000, duration_seconds=10)

    by_views = rank_videos(
        [low_views_good_fit, high_views_bad_fit], "q", 45, 20, WEIGHTS, PARAMS, sort_by="views"
    )
    assert by_views[0].video.video_id == "bbbbbbbbbbb"  # tri brut par vues, sans nuance

    by_potential = rank_videos(
        [low_views_good_fit, high_views_bad_fit], "q", 45, 20, WEIGHTS, PARAMS, sort_by="potential"
    )
    assert by_potential[0].video.video_id == "aaaaaaaaaaa"  # le score compose favorise l'exploitable
