"""Socle commun du Radar : createurs, base locale, tendances, Radar Score.

Aucun appel reseau : les adaptateurs de plateforme sont remplaces par des faux.
C'est precisement pour cela qu'ils sont injectes plutot qu'importes.
"""
from datetime import datetime, timedelta, timezone

import pytest

from radar import scoring, trends
from radar.creators import CreatorAlreadyWatched, CreatorManager
from radar.models import (
    KIND_SHORT,
    PRIORITY_HIGH,
    PRIORITY_LOW,
    Creator,
    Opportunity,
    ScanResult,
    Snapshot,
)
from radar.store import RadarStore

NOW = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path):
    return RadarStore(tmp_path / "radar.sqlite3")


def _creator(pid="UC1", platform="youtube", **kwargs):
    return Creator(platform=platform, platform_id=pid, **kwargs)


def _opp(cid, views=1000, hours_ago=3.0, likes=None, comments=None, duration=45,
         creator_key="youtube:UC1", platform="youtube"):
    return Opportunity(
        platform=platform, content_id=cid, kind=KIND_SHORT, creator_key=creator_key,
        published_at=(NOW - timedelta(hours=hours_ago)).isoformat(),
        view_count=views, like_count=likes, comment_count=comments, duration_s=duration,
    )


def _snap(views, minutes_ago, content_id="youtube:v1"):
    return Snapshot(
        content_id=content_id,
        captured_at=(NOW - timedelta(minutes=minutes_ago)).isoformat(timespec="milliseconds"),
        view_count=views,
    )


class FakeAdapter:
    def __init__(self, creator=None):
        self.creator = creator

    def resolve_creator(self, query):
        return self.creator


# ------------------------------------------------------------ createurs

def test_a_channel_is_resolved_but_never_added_without_confirmation(store):
    manager = CreatorManager(store)
    candidate = manager.resolve(FakeAdapter(_creator(display_name="Streamer A")), "@a")

    assert candidate is not None
    assert not candidate.already_watched
    assert manager.all() == [], "resoudre ne doit rien ajouter"


def test_adding_the_same_creator_twice_is_refused(store):
    manager = CreatorManager(store)
    manager.add(_creator(display_name="A"))

    with pytest.raises(CreatorAlreadyWatched) as excinfo:
        manager.add(_creator(display_name="A renomme"))

    assert "déjà surveillé" in str(excinfo.value)


def test_a_renamed_creator_is_not_a_duplicate(store):
    # La cle est l'identifiant de plateforme, pas le pseudo : un createur qui
    # change de nom ne doit ni se dupliquer ni perdre son historique.
    manager = CreatorManager(store)
    manager.add(_creator(display_name="Ancien nom"))
    manager.rename("youtube:UC1", "Nouveau nom")

    assert len(manager.all()) == 1
    assert manager.get("youtube:UC1").display_name == "Nouveau nom"


def test_the_same_person_on_two_platforms_is_two_creators(store):
    manager = CreatorManager(store)
    manager.add(_creator("UC1", "youtube", display_name="A"))
    manager.add(_creator("T1", "twitch", display_name="A"))

    assert len(manager.all()) == 2
    assert len(manager.all(platform="twitch")) == 1


def test_deactivating_keeps_the_creator_and_its_data(store):
    manager = CreatorManager(store)
    manager.add(_creator())
    store.upsert_opportunity(_opp("v1"))

    manager.set_active("youtube:UC1", False)

    assert manager.get("youtube:UC1").active is False
    assert manager.all(active_only=True) == []
    assert len(store.list_opportunities()) == 1


def test_removing_a_creator_never_deletes_the_collected_history(store):
    # Section 4 : les anciennes analyses doivent rester disponibles.
    manager = CreatorManager(store)
    manager.add(_creator())
    store.upsert_opportunity(_opp("v1"))
    store.add_snapshot(_snap(1000, 60))

    manager.remove("youtube:UC1")

    assert manager.get("youtube:UC1") is None
    assert len(store.list_opportunities()) == 1
    assert len(store.snapshots("youtube:v1")) == 1


def test_high_priority_creators_are_listed_first(store):
    manager = CreatorManager(store)
    manager.add(_creator("UC1", display_name="Normal"))
    manager.add(_creator("UC2", display_name="Faible"), priority=PRIORITY_LOW)
    manager.add(_creator("UC3", display_name="Haute"), priority=PRIORITY_HIGH)

    assert [c.display_name for c in manager.all()] == ["Haute", "Normal", "Faible"]


def test_a_manual_order_is_kept_within_a_priority(store):
    manager = CreatorManager(store)
    for i in range(3):
        manager.add(_creator(f"UC{i}", display_name=f"C{i}"))

    manager.reorder(["youtube:UC2", "youtube:UC0", "youtube:UC1"])

    assert [c.display_name for c in manager.all()] == ["C2", "C0", "C1"]


def test_an_unknown_priority_is_rejected_rather_than_stored(store):
    manager = CreatorManager(store)
    manager.add(_creator())

    assert manager.set_priority("youtube:UC1", "enorme") is None
    assert manager.get("youtube:UC1").priority == "normal"


def test_an_unresolvable_channel_yields_no_candidate(store):
    assert CreatorManager(store).resolve(FakeAdapter(None), "chaine inexistante") is None


# ------------------------------------------------------------- stockage

def test_favorites_keep_the_statistics_of_the_moment(store):
    # Les chiffres qui ont motive la mise en favori ne doivent pas bouger quand
    # le contenu evolue.
    opportunity = _opp("v1", views=8000)
    store.add_favorite(opportunity, note="a decouper")

    opportunity.view_count = 90000
    store.upsert_opportunity(opportunity)

    favorite = store.list_favorites()[0]
    assert favorite["snapshot"]["view_count"] == 8000
    assert favorite["note"] == "a decouper"
    assert store.is_favorite(opportunity.key)


def test_two_snapshots_in_the_same_second_are_both_kept(store):
    # A la seconde pres, le second ecrasait le premier sans erreur -- une perte
    # silencieuse de la donnee dont depend toute la detection de tendance.
    opportunity = _opp("v1", views=1000)
    store.add_snapshot(opportunity.snapshot())
    opportunity.view_count = 4200
    store.add_snapshot(opportunity.snapshot())

    assert [s.view_count for s in store.snapshots(opportunity.key)] == [1000, 4200]


def test_scans_are_recorded_for_the_history(store):
    store.record_scan(ScanResult(started_at="2026-09-08T09:00:00+00:00",
                                 platforms=("youtube",), creators_scanned=27,
                                 opportunities_found=63))

    scans = store.list_scans()
    assert scans[0]["creators_scanned"] == 27
    assert scans[0]["platforms"] == ["youtube"]


# ------------------------------------------------------------ tendances

def test_a_first_sighting_reports_no_trend_rather_than_zero_growth():
    # Aucune API ne donne de vitesse : au premier releve il n'y a rien a
    # comparer, et afficher "+0 %" serait faux.
    trend = trends.compute_trend([_snap(18500, 0)])

    assert trend.level == trends.LEVEL_UNKNOWN
    assert trend.growth_percent is None
    assert not trend.detected
    assert "prochain scan" in trend.sentence()


def test_a_real_surge_is_detected_and_quantified():
    trend = trends.compute_trend([_snap(18500, 60), _snap(42000, 0)])

    assert trend.level == trends.LEVEL_STRONG
    assert trend.growth_percent == pytest.approx(127.0, abs=1.0)
    assert "18 500" in trend.sentence() and "42 000" in trend.sentence()


def test_two_snapshots_too_close_together_prove_nothing():
    assert trends.compute_trend([_snap(100, 3), _snap(250, 0)]).level == trends.LEVEL_UNKNOWN


def test_a_stable_content_is_not_announced_as_trending():
    trend = trends.compute_trend([_snap(40000, 60), _snap(40500, 0)])

    assert trend.level == trends.LEVEL_FLAT and not trend.detected


def test_confidence_grows_with_evidence_not_with_percentage():
    # +150 % de 10 a 25 vues ne vaut pas +150 % de 10 000 a 25 000.
    small = trends.compute_trend([_snap(10, 120), _snap(25, 0)])
    large = trends.compute_trend([_snap(10000, 120), _snap(25000, 0)])

    assert large.confidence > small.confidence


def test_the_average_speed_works_from_the_very_first_sighting():
    # Contrairement a la progression : elle rapporte un compteur a un age.
    speed = trends.views_per_hour_since_publication(
        42000, (NOW - timedelta(hours=3)).isoformat(), reference=NOW)

    assert speed == pytest.approx(14000.0, rel=0.01)


# ---------------------------------------------------------- Radar Score

def test_the_score_is_not_just_a_view_count():
    # Un contenu enorme mais vieux et sans engagement ne doit pas ecraser un
    # contenu recent, engageant, et au-dessus de la moyenne de son createur.
    history = [_opp(f"h{i}", views=8500, hours_ago=3) for i in range(5)]
    fast = _opp("fast", views=42000, hours_ago=3, likes=5900, comments=340)
    old_giant = _opp("old", views=500000, hours_ago=400, likes=100, comments=2)

    fast_score = scoring.compute_radar_score(fast, history, reference=NOW)
    old_score = scoring.compute_radar_score(old_giant, history, reference=NOW)

    assert fast_score.total > old_score.total


def test_the_relative_comparison_matches_the_creators_own_history():
    history = [_opp(f"h{i}", views=8500, hours_ago=3) for i in range(5)]
    result = scoring.compute_radar_score(_opp("star", views=42000, hours_ago=3),
                                         history, reference=NOW)

    assert result.relative_ratio == pytest.approx(4.9, abs=0.2)
    assert "au-dessus de la moyenne" in scoring.relative_sentence(result.relative_ratio)


def test_without_enough_history_the_relative_component_is_absent_not_guessed():
    result = scoring.compute_radar_score(_opp("x", views=5000), [_opp("h0", views=1000)],
                                         reference=NOW)

    assert "relative" in result.missing
    assert result.relative_ratio is None
    assert "historique du créateur" in result.explain_missing()


def test_a_missing_component_never_counts_as_zero():
    complete = _opp("a", views=20000, likes=1200, comments=90)
    without_engagement = _opp("b", views=20000)

    with_all = scoring.compute_radar_score(complete, [], reference=NOW)
    partial = scoring.compute_radar_score(without_engagement, [], reference=NOW)

    assert partial.total > 25, "une donnee absente ne doit pas plomber le score"
    assert "engagement" in partial.missing and "engagement" not in with_all.missing


def test_priority_influences_the_ranking_without_crushing_it():
    # Section 11 : un createur en priorite faible qui produit un contenu
    # exceptionnel doit pouvoir passer devant.
    exceptional = _opp("exceptional", views=80000, hours_ago=2, likes=9000, comments=800)
    ordinary = _opp("ordinary", views=900, hours_ago=20)

    low_but_great = scoring.compute_radar_score(exceptional, [], PRIORITY_LOW, reference=NOW)
    high_but_weak = scoring.compute_radar_score(ordinary, [], PRIORITY_HIGH, reference=NOW)

    assert low_but_great.total > high_but_weak.total


def test_the_score_details_are_all_displayable():
    result = scoring.compute_radar_score(
        _opp("x", views=20000, likes=1500, comments=120),
        [_opp(f"h{i}", views=4000) for i in range(4)], reference=NOW)

    assert 0 <= result.total <= 100
    assert len(result.lines()) == 5
    assert all(":" in line for line in result.lines())


def test_a_long_live_is_penalised_but_never_eliminated():
    live = _opp("live", views=5000, duration=4 * 3600)

    assert scoring.format_component(live, scoring.DEFAULT_PARAMS) >= 0.0
    assert scoring.compute_radar_score(live, [], reference=NOW).total > 0
