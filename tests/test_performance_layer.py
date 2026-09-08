"""Couche performances : modeles, stockage local, Performance Score, ecarts.

Aucun apprentissage ici -- c'est la phase suivante. Ces tests couvrent la
collecte et l'analyse, et surtout les regles d'honnetete : ne jamais remplacer
une donnee absente par une valeur plausible, ne jamais annoncer une tendance
sur trop peu de clips, ne jamais parler de cause.

Tests purs : rien n'est ecrit hors de tmp_path, rien ne sort de la machine.
"""
from datetime import datetime, timedelta, timezone

import pytest

from performance import analyzer, metrics
from performance.models import ClipFeatures, ClipPerformance, PerformanceRecord
from performance.store import PerformanceStore
from performance.tracker import clip_identifier, features_from_clip, silence_ratio
from core.models import ClipResult

NOW = datetime(2026, 9, 8, tzinfo=timezone.utc)


def _perf(cid, days_ago=30, **kwargs):
    published = (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%d")
    return ClipPerformance(clip_id=cid, published_at=published, **kwargs)


def _record(cid, viral=80.0, **feature_kwargs):
    return PerformanceRecord(
        features=ClipFeatures(clip_id=cid, viral_potential_score=viral, **feature_kwargs),
        performance=None,
    )


# --------------------------------------------------------------- modeles

def test_an_untouched_performance_form_is_empty_and_not_recorded():
    assert ClipPerformance(clip_id="c1").is_empty()
    assert not ClipPerformance(clip_id="c1", views=1).is_empty()


def test_zero_views_is_not_the_same_as_no_views_entered():
    # Distinguer "le clip a fait 0 vue" de "je n'ai pas saisi les vues" est
    # indispensable : le premier est une information, le second une absence.
    entered_zero = ClipPerformance(clip_id="c1", views=0)
    not_entered = ClipPerformance(clip_id="c2")

    assert not entered_zero.is_empty()
    assert not_entered.is_empty()
    assert "views" in entered_zero.to_dict()
    assert "views" not in not_entered.to_dict()


def test_unknown_fields_in_a_saved_file_are_ignored_not_fatal():
    # Un fichier ecrit par une version ulterieure ne doit pas empecher de
    # relire ce qu'on comprend.
    loaded = ClipPerformance.from_dict({"clip_id": "c1", "views": 10, "future_field": 42})

    assert loaded.views == 10


# ------------------------------------------------------------ stockage

def test_everything_is_written_locally_and_reread(tmp_path):
    store = PerformanceStore(tmp_path)
    store.save_features([ClipFeatures(clip_id="p/c1", hook_score=90.0)])
    store.save_performance(_perf("p/c1", views=1200))

    records = store.records()

    assert [r.clip_id for r in records] == ["p/c1"]
    assert records[0].has_performance
    assert (tmp_path / "clips.json").is_file()
    assert (tmp_path / "performances.json").is_file()


def test_clearing_every_field_deletes_the_entry_instead_of_saving_nothing(tmp_path):
    store = PerformanceStore(tmp_path)
    store.save_features([ClipFeatures(clip_id="p/c1")])
    store.save_performance(_perf("p/c1", views=500))
    store.save_performance(ClipPerformance(clip_id="p/c1"))

    assert store.load_performances() == {}
    assert store.records()[0].features.clip_id == "p/c1", "la fiche technique survit"


def test_resetting_the_learning_keeps_measurements_and_entries(tmp_path):
    # Section 24 : repartir de ponderations neutres ne doit pas effacer le
    # travail de saisie de l'utilisateur, qui est un fait, pas un reglage.
    store = PerformanceStore(tmp_path)
    store.save_features([ClipFeatures(clip_id="p/c1")])
    store.save_performance(_perf("p/c1", views=500))
    store.save_profile({"weights": {"hook": 0.5}})

    store.reset_learning()

    assert store.load_profile() == {}
    assert store.load_features() and store.load_performances()


def test_reset_all_really_clears_everything(tmp_path):
    store = PerformanceStore(tmp_path)
    store.save_features([ClipFeatures(clip_id="p/c1")])
    store.save_performance(_perf("p/c1", views=1))
    store.reset_all()

    assert store.records() == []


def test_a_corrupted_file_does_not_prevent_producing_clips(tmp_path):
    (tmp_path / "clips.json").write_text("{ ceci n'est pas du json", encoding="utf-8")

    assert PerformanceStore(tmp_path).load_features() == {}


def test_export_gathers_everything_for_a_backup(tmp_path):
    store = PerformanceStore(tmp_path)
    store.save_features([ClipFeatures(clip_id="p/c1")])
    store.save_performance(_perf("p/c1", views=10))

    exported = store.export_all()

    assert set(exported) == {"schema_version", "clips", "performances", "learning_profile"}


# ------------------------------------------------- Performance Score

def _population(n=6):
    return [_perf(f"c{i}", views=1000 * (i + 1), likes=10 * (i + 1),
                  completion_rate=30 + i * 8) for i in range(n)]


def test_views_alone_are_enough_to_get_a_score():
    # Le logiciel doit fonctionner meme si l'utilisateur ne renseigne que les vues.
    result = metrics.compute_performance_score(_perf("x", views=4000), _population(), reference=NOW)

    assert 0 <= result.score <= 100
    assert "engagement" in result.missing and "retention" in result.missing


def test_a_missing_component_is_removed_not_counted_as_zero():
    # Sans retention, le score doit se calculer sur le reste, pas etre plombe.
    population = _population()
    complete = _perf("a", views=6000, likes=600, completion_rate=90)
    partial = _perf("b", views=6000, likes=600)

    with_retention = metrics.compute_performance_score(complete, population, reference=NOW)
    without = metrics.compute_performance_score(partial, population, reference=NOW)

    assert without.score > 50, "une donnee absente ne doit pas valoir zero"
    assert "retention" in without.missing and "retention" not in with_retention.missing


def test_an_older_clip_is_not_favoured_just_by_having_more_time():
    # Le coeur de la section 14 : 10 000 vues en six mois ne valent pas
    # 10 000 vues en trois jours.
    old = _perf("old", days_ago=180, views=10000)
    fresh = _perf("fresh", days_ago=3, views=10000)
    population = [old, fresh]

    old_score = metrics.compute_performance_score(old, population, reference=NOW)
    fresh_score = metrics.compute_performance_score(fresh, population, reference=NOW)

    assert fresh_score.score > old_score.score


def test_engagement_needs_views_to_mean_anything():
    assert metrics.engagement_rate(ClipPerformance(clip_id="x", likes=200)) is None


def test_a_score_computed_on_too_few_clips_says_so():
    result = metrics.compute_performance_score(_perf("x", views=100), [_perf("y", views=50)],
                                               reference=NOW)

    assert not result.reliable
    assert "indicatif" in result.explain()


def test_the_ranking_survives_one_outlier():
    # Avec une normalisation min-max, un clip a un million de vues ecraserait
    # tous les autres vers zero. Le rang, non.
    population = _population() + [_perf("viral", views=1_000_000)]
    middling = _perf("m", views=4000, likes=40, completion_rate=54)

    result = metrics.compute_performance_score(middling, population, reference=NOW)

    assert result.score > 20


# ------------------------------------------------------------- ecarts

def test_a_clip_that_outperformed_its_estimate_is_described_as_such():
    dev = analyzer.Deviation(clip_id="c", estimated=72.0, observed=91.0, reliable=True)

    assert dev.gap == pytest.approx(19.0)
    assert "dépassé" in dev.sentence()


def test_a_clip_matching_its_estimate_is_not_dramatised():
    assert "conforme" in analyzer.Deviation("c", 80.0, 84.0, True).sentence()


# ------------------------------------------------ seuils et confiance

def test_no_trend_is_presented_below_the_first_threshold():
    records = []
    for i in range(5):
        record = _record(f"c{i}", viral=70 + i)
        record.performance = _perf(f"c{i}", views=1000 * (i + 1), likes=10 * i)
        records.append(record)

    summary = analyzer.analyse(records)

    assert summary.level == analyzer.LEVEL_NONE
    assert summary.correlations == [] and summary.categories == []
    assert not summary.usable


def test_confidence_never_reaches_a_hundred_percent():
    # Aucune quantite de donnees saisies a la main ne justifie d'annoncer une
    # certitude (section 22).
    assert analyzer.confidence_percent(10_000) < 100


def test_correlations_appear_once_there_is_enough_data():
    records = []
    for i in range(12):
        record = _record(f"c{i}", viral=50 + i * 3)
        record.features.rewatch_score = 40 + i * 4
        record.performance = _perf(f"c{i}", views=500 * (i + 1), likes=5 * (i + 1),
                                   completion_rate=30 + i * 4)
        records.append(record)

    summary = analyzer.analyse(records)

    assert summary.usable
    assert summary.correlations
    assert all(hasattr(c, "label") for c in summary.correlations)


def test_a_factor_missing_on_some_clips_is_measured_only_where_it_exists():
    # Remplacer une valeur absente par une moyenne inventerait de la correlation.
    records = []
    for i in range(12):
        record = _record(f"c{i}", viral=60 + i)
        record.features.context_quality = None if i % 2 else 50 + i
        record.performance = _perf(f"c{i}", views=800 * (i + 1))
        records.append(record)

    correlations = analyzer.factor_correlations(records)
    context = [c for c in correlations if c.factor == "Qualité du contexte"]

    assert context and context[0].sample_size == 6


def test_a_category_with_too_few_clips_is_not_summarised():
    records = []
    for i in range(9):
        record = _record(f"c{i}")
        record.features.subtitle_style = "dynamic" if i < 7 else "bold"
        record.performance = _perf(f"c{i}", views=400 * (i + 1))
        records.append(record)

    values = {c.value for c in analyzer.categorical_performance(records)}

    assert "dynamic" in values
    assert "bold" not in values, "2 clips ne font pas une moyenne"


def test_the_vocabulary_stays_correlation_never_cause():
    labels = {analyzer.correlation_label(v) for v in (0.9, 0.4, 0.2, 0.01)}

    assert labels == {"forte", "moyenne", "faible", "negligeable"}
    assert all("cause" not in label for label in labels)


# ------------------------------------------------------------ tracker

def test_the_feature_sheet_reuses_what_the_pipeline_already_computed():
    clip = ClipResult(
        index=1, file_name="clips/clip_01.mp4", start=0, end=34, duration=34,
        score=93, scores={"total": 91, "rewatch": 96, "content": 88, "viral": 93, "audio": 72},
        transcript="mot " * 160, language="fr", reasons=["question detectee"],
        context={"confidence": 0.92, "category": "revelation"},
        framing={"mode": "track"}, montage={"applied": True},
        metadata={"titles": [{"kind": "curiosite"}]},
        thumbnails=["thumbnails/clip_01_a.jpg"],
    )

    features = features_from_clip(clip, "Podcast_01", priority=88.4, spoken_s=31.3,
                                  subtitle_style="dynamic")

    assert features.clip_id == "Podcast_01/clips/clip_01.mp4"
    assert (features.hook_score, features.rewatch_score, features.viral_potential_score) == (91.0, 96.0, 93.0)
    assert features.question_detected and features.category == "revelation"
    assert features.title_style == "curiosite" and features.thumbnail_variant == "a"
    assert features.subtitle_style == "dynamic" and features.framing_mode == "track"
    assert features.silence_ratio == pytest.approx(0.079, abs=1e-3)


def test_an_unmeasured_silence_ratio_is_absent_not_zero():
    # 0.0 signifierait "aucun silence" et fausserait toute correlation.
    assert silence_ratio(34.0, None) is None
    assert silence_ratio(34.0, 34.0) == 0.0


def test_two_projects_can_hold_a_clip_with_the_same_file_name():
    assert clip_identifier("A", "clips/clip_01.mp4") != clip_identifier("B", "clips/clip_01.mp4")
