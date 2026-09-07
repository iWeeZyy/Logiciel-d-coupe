from datetime import datetime
from zoneinfo import ZoneInfo

from youtube.quota import QuotaTracker, _current_pacific_date

PACIFIC = ZoneInfo("America/Los_Angeles")


def test_current_pacific_date_is_date_only_string():
    now = datetime(2024, 6, 15, 3, 0, tzinfo=PACIFIC)
    assert _current_pacific_date(now) == "2024-06-15"


def test_record_accumulates_within_same_day(tmp_path):
    tracker = QuotaTracker(daily_limit=1000, path=tmp_path / "quota.json")
    now = datetime(2024, 6, 15, 10, 0, tzinfo=PACIFIC)

    assert tracker.used(now) == 0
    tracker.record(100, now)
    tracker.record(100, now)
    assert tracker.used(now) == 200
    assert tracker.remaining(now) == 800


def test_would_exceed_respects_daily_limit(tmp_path):
    tracker = QuotaTracker(daily_limit=150, path=tmp_path / "quota.json")
    now = datetime(2024, 6, 15, 10, 0, tzinfo=PACIFIC)

    tracker.record(100, now)
    assert tracker.would_exceed(100, now) is True  # 100+100 > 150
    assert tracker.would_exceed(50, now) is False  # 100+50 == 150, pas de depassement


def test_quota_resets_on_new_pacific_day(tmp_path):
    tracker = QuotaTracker(daily_limit=1000, path=tmp_path / "quota.json")
    day1 = datetime(2024, 6, 15, 23, 0, tzinfo=PACIFIC)
    day2 = datetime(2024, 6, 16, 1, 0, tzinfo=PACIFIC)

    tracker.record(900, day1)
    assert tracker.used(day1) == 900
    assert tracker.used(day2) == 0  # nouveau jour Pacifique -> compteur remis a zero
    assert tracker.remaining(day2) == 1000
