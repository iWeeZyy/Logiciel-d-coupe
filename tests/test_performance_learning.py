"""Apprentissage : propositions de ponderations, profil personnel, influence
sur la selection.

Le risque que ces tests couvrent est celui de la section 26 : qu'un petit
nombre de clips fasse basculer tout le systeme, ou qu'un reglage change sans
que l'utilisateur l'ait voulu.

Tests purs.
"""
from datetime import datetime, timedelta, timezone

import pytest

from performance import learning, profile as personal
from performance.models import ClipFeatures, ClipPerformance, PerformanceRecord

NOW = datetime(2026, 9, 8, tzinfo=timezone.utc)


def _records(n, rewatch_drives=True, duration=30.0):
    """n clips ou la performance suit le Rewatch Score (ou le Hook Score)."""
    out = []
    for i in range(n):
        driver = 20 + i * 6
        other = 90 - i * 5
        features = ClipFeatures(
            clip_id=f"p/c{i}",
            hook_score=other if rewatch_drives else driver,
            rewatch_score=driver if rewatch_drives else other,
            content_score=50.0,
            viral_potential_score=60 + i,
            duration=duration + (i % 5),
        )
        published = (NOW - timedelta(days=30)).strftime("%Y-%m-%d")
        performance = ClipPerformance(
            clip_id=features.clip_id, published_at=published,
            views=200 * (i + 1), likes=8 * (i + 1), completion_rate=25 + i * 3,
        )
        out.append(PerformanceRecord(features=features, performance=performance))
    return out


# ------------------------------------------------------- propositions

def test_nothing_is_proposed_without_enough_data():
    assert learning.propose(_records(4)) is None


def test_a_clear_signal_produces_a_proposal_naming_the_two_scores():
    proposal = learning.propose(_records(20))

    assert proposal is not None
    assert "Rewatch Score" in proposal.reason and "Hook Score" in proposal.reason
    assert "20 derniers clips" in proposal.reason


def test_a_proposal_never_moves_a_weight_more_than_the_cap():
    proposal = learning.propose(_records(40))

    assert proposal is not None
    for _name, before, after in proposal.changes():
        assert abs(after - before) <= learning.MAX_SHIFT + 0.01, \
            "quarante clips ne doivent pas retourner le moteur"


def test_no_signal_is_ever_removed_completely():
    profile = {}
    for _ in range(15):
        proposal = learning.propose(_records(40), profile)
        if proposal is None:
            break
        profile = learning.apply_proposal(profile, proposal)

    weights = learning.current_weights(profile)
    assert min(weights.values()) >= learning.MIN_WEIGHT - 0.01, \
        f"un poids est tombe trop bas : {weights}"


def test_the_weights_always_sum_to_one():
    profile = learning.apply_proposal({}, learning.propose(_records(30)))

    assert sum(learning.current_weights(profile).values()) == pytest.approx(1.0, abs=0.01)


def test_nothing_changes_until_the_proposal_is_applied():
    # Section 16 : ne jamais modifier silencieusement les parametres.
    profile = {}
    proposal = learning.propose(_records(30), profile)

    assert proposal is not None
    assert learning.current_weights(profile) == learning.BASE_WEIGHTS


def test_applying_then_reverting_restores_the_previous_weights():
    before = learning.current_weights({})
    profile = learning.apply_proposal({}, learning.propose(_records(30)))
    assert learning.current_weights(profile) != before
    assert learning.can_revert(profile)

    reverted = learning.revert(profile)

    assert learning.current_weights(reverted) == before


def test_reverting_without_history_is_harmless():
    assert learning.current_weights(learning.revert({})) == learning.BASE_WEIGHTS


def test_refusing_a_proposal_changes_no_weight_and_silences_it():
    profile = learning.reject({}, learning.propose(_records(30)))

    assert learning.current_weights(profile) == learning.BASE_WEIGHTS
    assert learning.is_rejected(profile, learning.propose(_records(30)))


def test_the_history_does_not_grow_without_bound():
    profile = {}
    for _ in range(30):
        proposal = learning.propose(_records(30), profile)
        if proposal is None:
            break
        profile = learning.apply_proposal(profile, proposal)

    assert len(profile.get("history", [])) <= 20


# ------------------------------------------------------------- profil

def test_no_profile_is_built_from_too_few_clips():
    built = personal.build_profile(_records(4))

    assert not built.usable and built.lines() == []


def test_a_profile_reports_a_duration_window_once_there_is_data():
    built = personal.build_profile(_records(20))

    assert built.usable
    assert built.duration is not None
    assert any("Durée optimale" in line for line in built.lines())


def test_the_personal_profile_never_dominates_the_general_analysis():
    # Section 25 : garder l'equilibre entre ce qui marche generalement et ce
    # qui marche pour cet utilisateur.
    built = personal.build_profile(_records(200))

    assert built.influence <= personal.MAX_PERSONAL_INFLUENCE
    assert personal.blend(80.0, 0.0, built.influence) >= 80.0 * (1 - personal.MAX_PERSONAL_INFLUENCE)


def test_a_candidate_outside_the_preferred_window_is_penalised_not_excluded():
    built = personal.build_profile(_records(20, duration=30.0))
    assert built.duration is not None

    inside = personal.affinity(built, (built.duration.low + built.duration.high) / 2)
    far = personal.affinity(built, built.duration.high + 300)

    assert inside == 100.0
    assert far == 0.0
    assert personal.blend(80.0, far, built.influence) > 0.0, "penalise, jamais elimine"


def test_an_unusable_profile_is_perfectly_neutral():
    built = personal.build_profile(_records(3))

    assert personal.affinity(built, 30.0) == 50.0
    assert personal.blend(80.0, personal.affinity(built, 30.0), built.influence) == 80.0


def test_the_influence_grows_with_the_amount_of_data():
    small = personal.build_profile(_records(12)).influence
    large = personal.build_profile(_records(60)).influence

    assert large > small
