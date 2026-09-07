from youtube.models import SearchFilters, VideoResult
from youtube.search import (
    _apply_client_side_duration_filter,
    _merge_video_details,
    _parse_duration_iso8601,
    _parse_search_item,
)


def test_parse_duration_full_hms():
    assert _parse_duration_iso8601("PT1H2M3S") == 3723


def test_parse_duration_minutes_seconds_only():
    assert _parse_duration_iso8601("PT15M33S") == 933


def test_parse_duration_seconds_only():
    assert _parse_duration_iso8601("PT45S") == 45


def test_parse_duration_invalid_returns_zero():
    assert _parse_duration_iso8601("not-a-duration") == 0


def test_parse_search_item_extracts_expected_fields():
    item = {
        "id": {"kind": "youtube#video", "videoId": "abc12345678"},
        "snippet": {
            "publishedAt": "2024-01-01T12:00:00Z",
            "channelId": "UCxyz",
            "title": "Un titre",
            "description": "Une description",
            "channelTitle": "Une chaine",
            "thumbnails": {
                "default": {"url": "https://example.com/default.jpg"},
                "medium": {"url": "https://example.com/medium.jpg"},
            },
        },
    }
    video = _parse_search_item(item)
    assert video is not None
    assert video.video_id == "abc12345678"
    assert video.title == "Un titre"
    assert video.channel_title == "Une chaine"
    assert video.thumbnail_url == "https://example.com/medium.jpg"  # prefere "medium" a "default"
    assert video.url == "https://www.youtube.com/watch?v=abc12345678"


def test_parse_search_item_without_video_id_returns_none():
    item = {"id": {"kind": "youtube#channel", "channelId": "UCxyz"}, "snippet": {}}
    assert _parse_search_item(item) is None


def test_merge_video_details_fills_duration_views_license():
    video = VideoResult(
        video_id="abc12345678", title="t", channel_id="c", channel_title="ct",
        published_at="2024-01-01T00:00:00Z", description="d", thumbnail_url="",
    )
    details = {
        "contentDetails": {"duration": "PT10M"},
        "statistics": {"viewCount": "12345", "likeCount": "678"},
        "status": {"license": "creativeCommon"},
    }
    _merge_video_details(video, details)

    assert video.duration_seconds == 600
    assert video.view_count == 12345
    assert video.like_count == 678
    assert video.license == "creativeCommon"


def test_merge_video_details_never_guesses_missing_fields():
    video = VideoResult(
        video_id="abc12345678", title="t", channel_id="c", channel_title="ct",
        published_at="2024-01-01T00:00:00Z", description="d", thumbnail_url="",
    )
    _merge_video_details(video, {"contentDetails": {}, "statistics": {}, "status": {}})

    assert video.duration_seconds is None
    assert video.view_count is None
    assert video.license is None


def test_duration_filter_excludes_videos_with_unknown_duration():
    known = VideoResult(
        video_id="aaaaaaaaaaa", title="t", channel_id="c", channel_title="ct",
        published_at="2024-01-01T00:00:00Z", description="d", thumbnail_url="",
        duration_seconds=300,
    )
    unknown = VideoResult(
        video_id="bbbbbbbbbbb", title="t", channel_id="c", channel_title="ct",
        published_at="2024-01-01T00:00:00Z", description="d", thumbnail_url="",
        duration_seconds=None,
    )
    filters = SearchFilters(min_duration_s=100, max_duration_s=600)
    result = _apply_client_side_duration_filter([known, unknown], filters)

    assert result == [known]  # jamais suppose conforme sans donnee
