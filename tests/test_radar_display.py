"""Fiches du Radar : duree, date, categorie lisible, et ordre de la liste."""
from datetime import datetime, timedelta, timezone

import pytest

from radar.display import (
    ORDER_RECENT,
    ORDER_SCORE,
    ORDER_VIEWS,
    format_age,
    format_duration,
    is_readable_category,
    sort_key,
)
from radar.models import KIND_CLIP, Opportunity
from radar.store import RadarStore

NOW = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)


def _ago(**kwargs):
    return (NOW - timedelta(**kwargs)).isoformat().replace("+00:00", "Z")


# ----------------------------------------------------------------- duree

@pytest.mark.parametrize("seconds,expected", [
    (34.6, "0:35"),          # Twitch donne des decimales : on arrondit
    (29.8, "0:30"),
    (90, "1:30"),
    (3725, "1:02:05"),
])
def test_a_duration_is_written_the_way_a_player_writes_it(seconds, expected):
    assert format_duration(seconds) == expected


@pytest.mark.parametrize("value", [None, 0, "", "abc"])
def test_an_unknown_duration_shows_nothing_rather_than_zero(value):
    # "0:00" se lirait comme un clip vide, ce qui serait une information fausse.
    assert format_duration(value) == ""


# ------------------------------------------------------------------ date

def test_a_recent_clip_is_dated_by_its_age():
    assert format_age(_ago(minutes=20), now=NOW) == "il y a 20 min"
    assert format_age(_ago(hours=5), now=NOW) == "il y a 5 h"
    assert format_age(_ago(days=3), now=NOW) == "il y a 3 j"


def test_an_old_clip_gets_a_real_date():
    # « il y a 23 j » ne dit rien ; une date, si.
    assert format_age(_ago(days=23), now=NOW) == "17/08/2026"


def test_an_unreadable_date_shows_nothing():
    assert format_age("", now=NOW) == ""
    assert format_age("pas une date", now=NOW) == ""


def test_a_date_without_timezone_is_read_as_utc():
    assert format_age("2026-09-09T09:00:00", now=NOW) == "il y a 3 h"


# ------------------------------------------------------------- categorie

def test_a_numeric_category_is_an_identifier_not_a_name():
    # /clips renvoie un game_id : "132735846" ne veut rien dire pour personne.
    assert is_readable_category("132735846") is False
    assert is_readable_category("Fortnite") is True
    assert is_readable_category("") is False


def test_a_numeric_category_never_becomes_a_hashtag():
    from radar.analysis.describer import _as_hashtag

    assert _as_hashtag("132735846") == ""
    assert _as_hashtag("Just Chatting") == "#JustChatting"


# ----------------------------------------------------------------- ordre

def _clip(store, content_id, *, score, views, published_at):
    clip = Opportunity(platform="twitch", content_id=content_id, kind=KIND_CLIP,
                       creator_key="twitch:1", title=content_id,
                       published_at=published_at, view_count=views)
    clip.radar_score = score
    store.upsert_opportunity(clip)
    return clip


@pytest.fixture
def store(tmp_path):
    store = RadarStore(path=tmp_path / "radar.sqlite3")
    _clip(store, "vieux-tres-vu", score=90, views=5000, published_at=_ago(days=5))
    _clip(store, "recent-peu-vu", score=40, views=12, published_at=_ago(hours=1))
    _clip(store, "milieu", score=60, views=800, published_at=_ago(days=1))
    return store


def test_the_default_order_is_still_the_radar_score(store):
    assert [o.content_id for o in store.list_opportunities(platform="twitch")][0] == "vieux-tres-vu"


def test_the_most_recent_first(store):
    ids = [o.content_id for o in store.list_opportunities(platform="twitch", order=ORDER_RECENT)]
    assert ids == ["recent-peu-vu", "milieu", "vieux-tres-vu"]


def test_the_most_watched_first(store):
    ids = [o.content_id for o in store.list_opportunities(platform="twitch", order=ORDER_VIEWS)]
    assert ids == ["vieux-tres-vu", "milieu", "recent-peu-vu"]


def test_the_order_is_applied_before_the_limit(store):
    # Le point qui compte : trier apres coup ne trierait que ce que la requete
    # a deja laisse passer, donc le plus recent pourrait ne jamais apparaitre.
    top = store.list_opportunities(platform="twitch", limit=1, order=ORDER_RECENT)

    assert [o.content_id for o in top] == ["recent-peu-vu"]


def test_a_clip_with_no_known_views_falls_to_the_bottom(store):
    _clip(store, "sans-vues", score=95, views=None, published_at=_ago(hours=2))

    ids = [o.content_id for o in store.list_opportunities(platform="twitch", order=ORDER_VIEWS)]
    assert ids[-1] == "sans-vues"


def test_the_interface_sorts_the_same_way_as_the_database(store):
    # L'onglet « Tous » fusionne deux plateformes interrogees separement : la
    # liste fusionnee doit se retrier exactement comme la requete.
    for order in (ORDER_SCORE, ORDER_RECENT, ORDER_VIEWS):
        from_db = store.list_opportunities(platform="twitch", order=order)
        resorted = sorted(from_db, key=sort_key(order), reverse=True)

        assert [o.content_id for o in resorted] == [o.content_id for o in from_db]
