"""Adaptateur YouTube et moteur de scan.

Aucun appel reseau : les reponses de l'API sont simulees. Ce que ces tests
protegent en priorite est le COUT EN QUOTA -- c'est lui qui rend le Radar
utilisable ou non, et une regression y serait invisible a l'oeil nu.
"""
from datetime import datetime, timedelta, timezone

import pytest

from core.cancellation import CancelToken
from radar.engine import PERIODS, RadarEngine, since_iso
from radar.models import KIND_SHORT, KIND_VOD, Creator
from radar.platforms.base import PlatformStatus
from radar.platforms.youtube import (
    COST_CHANNELS_LIST,
    COST_PLAYLIST_ITEMS,
    COST_SEARCH,
    COST_VIDEOS_LIST,
    SHORT_MAX_DURATION_S,
    YouTubeAdapter,
    _parse_duration_iso8601,
)
from radar.store import RadarStore
from utils.errors import CancelledError, YouTubeApiError, YouTubeQuotaError
from youtube.quota import QuotaTracker

NOW = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)


class FakeYouTube(YouTubeAdapter):
    """Adaptateur reel, mais dont les reponses HTTP sont fournies a la main :
    tout le reste du code (quota, parsing, detection de Short) est bien execute."""

    def __init__(self, responses, quota):
        super().__init__(api_key="fake", quota=quota)
        self.responses = responses
        self.calls = []

    def _get(self, endpoint, params, cost):
        if self.quota.would_exceed(cost):
            raise YouTubeQuotaError("Quota YouTube insuffisant pour cette requête.")
        self.quota.record(cost)
        self.calls.append((endpoint, cost))
        value = self.responses.get(endpoint)
        if isinstance(value, Exception):
            raise value
        return value or {}


@pytest.fixture
def quota(tmp_path):
    return QuotaTracker(daily_limit=10000, path=tmp_path / "quota.json")


def _published(hours_ago):
    return (NOW - timedelta(hours=hours_ago)).isoformat()


def _playlist_response(entries):
    return {"items": [{"contentDetails": {"videoId": vid, "videoPublishedAt": pub}}
                      for vid, pub in entries]}


def _videos_response(entries):
    return {"items": [{
        "id": vid,
        "snippet": {"title": f"Titre {vid}", "thumbnails": {"high": {"url": "t.jpg"}},
                    "publishedAt": pub, "liveBroadcastContent": "none"},
        "contentDetails": {"duration": duration},
        "statistics": {"viewCount": str(views), "likeCount": "100", "commentCount": "10"},
        "status": {"license": "youtube"},
    } for vid, pub, duration, views in entries]}


# ------------------------------------------------------------- resolution

@pytest.mark.parametrize("query,expected", [
    ("https://www.youtube.com/@streamera", ("handle", "streamera")),
    ("https://youtube.com/channel/UCabcdefghijklmnopqrstuv", ("channel_id", "UCabcdefghijklmnopqrstuv")),
    ("@streamera", ("handle", "streamera")),
    ("UCabcdefghijklmnopqrstuv", ("channel_id", "UCabcdefghijklmnopqrstuv")),
    ("Nom Approximatif", ("name", "Nom Approximatif")),
    ("", ("empty", "")),
])
def test_the_three_accepted_forms_are_recognised(query, expected):
    assert YouTubeAdapter.parse_query(query) == expected


def test_a_url_or_handle_never_costs_a_hundred_units(quota):
    # search.list coute 100 unites. L'utiliser pour une URL qui contient deja
    # l'identifiant gaspillerait cinquante fois le necessaire.
    adapter = FakeYouTube({"channels": {"items": [{
        "id": "UC1", "snippet": {"title": "A", "customUrl": "@a"},
        "statistics": {"subscriberCount": "1200000"},
        "contentDetails": {"relatedPlaylists": {"uploads": "UU1"}},
    }]}}, quota)

    creator = adapter.resolve_creator("https://www.youtube.com/@a")

    assert creator.display_name == "A" and creator.follower_count == 1200000
    assert quota.used() == COST_CHANNELS_LIST


def test_only_an_approximate_name_pays_for_a_search(quota):
    adapter = FakeYouTube({
        "search": {"items": [{"snippet": {"channelId": "UC1"}}]},
        "channels": {"items": [{"id": "UC1", "snippet": {"title": "A"},
                                "statistics": {}, "contentDetails": {}}]},
    }, quota)

    adapter.resolve_creator("un nom approximatif")

    assert quota.used() == COST_SEARCH + COST_CHANNELS_LIST


def test_a_hidden_subscriber_count_stays_unknown_instead_of_zero(quota):
    adapter = FakeYouTube({"channels": {"items": [
        {"id": "UC1", "snippet": {"title": "A"}, "statistics": {}, "contentDetails": {}}]}}, quota)

    assert adapter.resolve_creator("UCabcdefghijklmnopqrstuv").follower_count is None


def test_a_missing_channel_yields_no_creator(quota):
    adapter = FakeYouTube({"channels": {"items": []}}, quota)

    assert adapter.resolve_creator("UCabcdefghijklmnopqrstuv") is None


# ------------------------------------------------------------------ scan

def _creator_with_playlist():
    return Creator(platform="youtube", platform_id="UC1", display_name="A",
                   extra={"uploads_playlist": "UU1"})


def test_a_scan_costs_two_units_per_creator(quota):
    # Le coeur de l'architecture : 2 unites au lieu de 100. Sur 27 createurs,
    # c'est 185 scans par jour possibles au lieu de 3.
    adapter = FakeYouTube({
        "playlistItems": _playlist_response([("v1", _published(2))]),
        "videos": _videos_response([("v1", _published(2), "PT45S", 4200)]),
    }, quota)

    adapter.scan(_creator_with_playlist(), since_iso("24h", NOW))

    assert quota.used() == COST_PLAYLIST_ITEMS + COST_VIDEOS_LIST == 2


def test_the_uploads_playlist_is_cached_and_not_refetched(quota):
    adapter = FakeYouTube({
        "playlistItems": _playlist_response([]),
        "channels": {"items": [{"id": "UC1", "snippet": {"title": "A"}, "statistics": {},
                                "contentDetails": {"relatedPlaylists": {"uploads": "UU1"}}}]},
    }, quota)
    creator = _creator_with_playlist()

    adapter.scan(creator, since_iso("24h", NOW))

    assert not any(endpoint == "channels" for endpoint, _ in adapter.calls), \
        "la playlist d'uploads ne change jamais : la relire serait une unite gaspillee"


def test_a_creator_without_a_known_playlist_resolves_it_once(quota):
    adapter = FakeYouTube({
        "channels": {"items": [{"id": "UC1", "snippet": {"title": "A"}, "statistics": {},
                                "contentDetails": {"relatedPlaylists": {"uploads": "UU1"}}}]},
        "playlistItems": _playlist_response([]),
    }, quota)
    creator = Creator(platform="youtube", platform_id="UC1", display_name="A")

    adapter.scan(creator, since_iso("24h", NOW))

    assert creator.extra.get("uploads_playlist") == "UU1", "elle doit être mémorisée"


def test_content_older_than_the_period_is_skipped_without_a_second_call(quota):
    adapter = FakeYouTube({
        "playlistItems": _playlist_response([("old", _published(200))]),
        "videos": _videos_response([]),
    }, quota)

    assert adapter.scan(_creator_with_playlist(), since_iso("24h", NOW)) == []
    assert not any(endpoint == "videos" for endpoint, _ in adapter.calls)




def test_short_detection_is_by_duration_and_labelled_as_such(quota):
    # L'API n'expose aucun champ "c'est un Short" : la detection est une
    # heuristique et doit se presenter comme telle.
    adapter = FakeYouTube({
        "playlistItems": _playlist_response([("s", _published(1)), ("l", _published(1))]),
        "videos": _videos_response([("s", _published(1), "PT45S", 4200),
                                    ("l", _published(1), "PT12M", 900)]),
    }, quota)

    found = {o.content_id: o for o in adapter.scan(_creator_with_playlist(), since_iso("24h", NOW))}

    assert found["s"].kind == KIND_SHORT and found["s"].extra["short_detection"] == "durée"
    assert found["l"].kind == KIND_VOD and found["l"].extra["short_detection"] == ""
    assert SHORT_MAX_DURATION_S == 180


def test_a_hidden_like_count_stays_unknown(quota):
    response = _videos_response([("v", _published(1), "PT30S", 5000)])
    del response["items"][0]["statistics"]["likeCount"]
    adapter = FakeYouTube({
        "playlistItems": _playlist_response([("v", _published(1))]), "videos": response}, quota)

    assert adapter.scan(_creator_with_playlist(), since_iso("24h", NOW))[0].like_count is None


def test_the_license_is_recorded_as_information_never_as_permission(quota):
    adapter = FakeYouTube({
        "playlistItems": _playlist_response([("v", _published(1))]),
        "videos": _videos_response([("v", _published(1), "PT30S", 5000)]),
    }, quota)

    opportunity = adapter.scan(_creator_with_playlist(), since_iso("24h", NOW))[0]

    assert opportunity.extra["license"] == "youtube"
    assert "libre" not in str(opportunity.to_dict()).lower()


def test_an_exhausted_quota_is_reported_before_the_call(tmp_path):
    quota = QuotaTracker(daily_limit=1, path=tmp_path / "q.json")
    quota.record(1)
    adapter = FakeYouTube({"playlistItems": _playlist_response([])}, quota)

    with pytest.raises(YouTubeQuotaError):
        adapter.scan(_creator_with_playlist(), since_iso("24h", NOW))


def test_a_deleted_channel_gives_a_readable_message(quota):
    adapter = FakeYouTube({"playlistItems": YouTubeApiError("Ressource introuvable")}, quota)

    with pytest.raises(YouTubeApiError):
        adapter.scan(_creator_with_playlist(), since_iso("24h", NOW))


def test_without_an_api_key_the_platform_says_it_is_unavailable(monkeypatch):
    from utils.errors import YouTubeConfigError
    adapter = YouTubeAdapter(api_key=None)
    monkeypatch.setattr("radar.platforms.youtube.load_api_key",
                        lambda: (_ for _ in ()).throw(YouTubeConfigError("Aucune clé")))

    status = adapter.status()

    assert isinstance(status, PlatformStatus)
    assert not status.available and "clé" in status.setup_hint.lower()


# ---------------------------------------------------------------- moteur

class StubAdapter:
    platform = "youtube"

    def __init__(self, per_creator=None, error=None):
        self.per_creator = per_creator or {}
        self.error = error

    def status(self):
        return PlatformStatus(platform="youtube", available=True)

    def resolve_creator(self, query):
        return None

    def scan(self, creator, since_iso_value, max_results=50):
        if self.error is not None:
            raise self.error
        return list(self.per_creator.get(creator.key, []))


def _engine(tmp_path, adapter):
    store = RadarStore(tmp_path / "radar.sqlite3")
    return store, RadarEngine(store, {"youtube": adapter})


def _opportunity(cid, views, hours_ago=2):
    from radar.models import Opportunity
    return Opportunity(platform="youtube", content_id=cid, kind=KIND_SHORT,
                       creator_key="youtube:UC1", published_at=_published(hours_ago),
                       view_count=views, duration_s=45)


def test_a_scan_scores_stores_and_records_a_snapshot(tmp_path):
    store, engine = _engine(tmp_path, StubAdapter({"youtube:UC1": [_opportunity("v1", 4200)]}))
    store.upsert_creator(Creator(platform="youtube", platform_id="UC1", display_name="A"))

    result = engine.scan(reference=NOW)

    assert result.opportunities_found == 1 and result.creators_scanned == 1
    stored = store.list_opportunities()[0]
    assert stored.radar_score is not None and stored.score_breakdown
    assert len(store.snapshots(stored.key)) == 1, "un relevé par scan, même sur un contenu connu"


def test_a_second_scan_makes_the_trend_measurable(tmp_path):
    # Le premier scan ne peut rien dire ; le second le peut. C'est toute la
    # raison d'etre de la table des releves.
    store, engine = _engine(tmp_path, StubAdapter({"youtube:UC1": [_opportunity("v1", 18500)]}))
    store.upsert_creator(Creator(platform="youtube", platform_id="UC1", display_name="A"))

    engine.scan(reference=NOW)
    first = store.list_opportunities()[0]
    assert first.trend["level"] == "inconnue"

    engine.adapters["youtube"].per_creator["youtube:UC1"] = [_opportunity("v1", 42000)]
    store.add_snapshot(type(first.snapshot())(
        content_id=first.key, captured_at=(NOW - timedelta(hours=1)).isoformat(timespec="milliseconds"),
        view_count=18500))
    engine.scan(reference=NOW)

    assert store.list_opportunities()[0].trend["level"] == "forte"


def test_one_failing_creator_does_not_abort_the_whole_scan(tmp_path):
    class Mixed(StubAdapter):
        def scan(self, creator, since_iso_value, max_results=50):
            if creator.platform_id == "UC_BROKEN":
                raise YouTubeApiError("Chaîne supprimée")
            return [_opportunity("v1", 1000)]

    store, engine = _engine(tmp_path, Mixed())
    store.upsert_creator(Creator(platform="youtube", platform_id="UC_BROKEN", display_name="Cassé"))
    store.upsert_creator(Creator(platform="youtube", platform_id="UC1", display_name="Sain"))

    result = engine.scan(reference=NOW)

    assert result.creators_scanned == 1
    assert len(result.errors) == 1 and "Cassé" in result.errors[0]


def test_inactive_creators_are_not_scanned(tmp_path):
    store, engine = _engine(tmp_path, StubAdapter({"youtube:UC1": [_opportunity("v1", 100)]}))
    store.upsert_creator(Creator(platform="youtube", platform_id="UC1",
                                 display_name="A", active=False))

    assert engine.scan(reference=NOW).creators_scanned == 0


def test_a_scan_can_be_cancelled(tmp_path):
    store, engine = _engine(tmp_path, StubAdapter())
    store.upsert_creator(Creator(platform="youtube", platform_id="UC1", display_name="A"))
    token = CancelToken()
    token.cancel()

    with pytest.raises(CancelledError):
        engine.scan(cancel_token=token, reference=NOW)


def test_progress_is_reported_for_the_progress_bar(tmp_path):
    store, engine = _engine(tmp_path, StubAdapter({"youtube:UC1": [_opportunity("v1", 100)]}))
    for i in range(3):
        store.upsert_creator(Creator(platform="youtube", platform_id=f"UC{i}", display_name=f"C{i}"))

    seen = []
    engine.scan(on_progress=lambda p: seen.append((p.fraction, p.label())), reference=NOW)

    assert seen and seen[-1][0] == pytest.approx(1.0)
    assert "créateur" in seen[-1][1]


def test_the_dashboard_counts_what_was_actually_found(tmp_path):
    store, engine = _engine(tmp_path, StubAdapter({"youtube:UC1": [_opportunity("v1", 90000)]}))
    store.upsert_creator(Creator(platform="youtube", platform_id="UC1", display_name="A"))
    engine.scan(reference=NOW)

    dashboard = engine.dashboard(reference=NOW)

    assert dashboard["creators_watched"] == 1
    assert dashboard["opportunities"] == 1


@pytest.mark.parametrize("period", list(PERIODS))
def test_every_advertised_period_is_usable(period):
    assert since_iso(period, NOW) < NOW.isoformat()
