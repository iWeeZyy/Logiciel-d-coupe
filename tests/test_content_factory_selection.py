"""Selection Content Factory : priorite, diversite, entonnoir.

Le defaut que ces tests couvrent est concret : sur une video longue, les dix
meilleurs scores viennent souvent du meme quart d'heure, et le top 10 brut donne
dix variantes du meme moment.

Tests purs : ni video, ni ffmpeg, ni transcription.
"""
import pytest

from analysis.selector import select_clips
from content_factory import diversity
from content_factory.priority import DEFAULT_WEIGHTS, compute_priority, publishability
from content_factory.selection import DiverseRanker
from core.models import AudioFeatures, Candidate, ScoreBreakdown, ScoredCandidate, TextFeatures, Word
from editing.sentences import build_sentences

AUDIO_STATS = {"mean_db": -40.0, "p90_db": -20.0}


def _words(text, start, end):
    tokens = text.split()
    step = (end - start) / max(1, len(tokens))
    return [Word(text=t, start=round(start + i * step, 3), end=round(start + (i + 1) * step, 3))
            for i, t in enumerate(tokens)]


def _candidate(start, end, text="un passage de test qui parle de quelque chose", rms=-30.0):
    return Candidate(
        start=start, end=end, text=text, words=_words(text, start, end),
        audio=AudioFeatures(rms_mean=rms, rms_std=5.0),
        text_features=TextFeatures(word_count=len(text.split())),
    )


def _scored(start, end, viral, text="un passage de test qui parle de quelque chose", rms=-30.0):
    return ScoredCandidate(
        candidate=_candidate(start, end, text, rms),
        scores=ScoreBreakdown(total=viral, viral=viral),
    )


def _ranker(nb_clips, video_duration=7200.0, sentences=None, **kwargs):
    return DiverseRanker(
        sentences=sentences if sentences is not None else [],
        audio_stats=AUDIO_STATS, video_duration=video_duration, nb_clips=nb_clips, **kwargs
    )


def _select(scored, nb_clips, ranker, min_gap=1.0):
    return select_clips(
        scored, nb_clips=nb_clips, min_gap=min_gap, pre_roll=0, post_roll=0,
        max_overshoot_ratio=0.2, video_duration=7200.0, text_analyzer=None,
        apply_context=False, ranker=ranker,
    )


# ----------------------------------------------------------- diversite

def test_ten_clips_do_not_all_come_from_the_same_five_minutes():
    # Le defaut a corriger : une grappe de tres bons scores au meme endroit,
    # et quelques bons passages ailleurs dans la video.
    cluster = [_scored(600 + i * 40, 630 + i * 40, 95 - i * 0.1,
                       f"le meme sujet repete encore et encore variante {i}")
               for i in range(20)]
    elsewhere = [_scored(t, t + 30, 80.0, f"un tout autre sujet numero {t} avec son vocabulaire propre")
                 for t in (100, 2000, 3500, 5000, 6500)]

    selected = _select(cluster + elsewhere, 6, _ranker(6))

    starts = sorted(sc.candidate.start for sc in selected)
    assert len(selected) == 6
    assert starts[-1] - starts[0] > 1800, \
        f"selection concentree sur {starts[-1] - starts[0]:.0f}s : {starts}"
    from_cluster = sum(1 for sc in selected if 600 <= sc.candidate.start <= 1400)
    assert from_cluster <= 3, f"{from_cluster}/6 clips issus de la meme grappe"


def test_without_the_ranker_the_historical_behaviour_is_unchanged():
    # Non-regression : sans classeur, on reprend les meilleurs scores.
    cluster = [_scored(600 + i * 40, 630 + i * 40, 95 - i * 0.1, f"variante {i}") for i in range(20)]
    elsewhere = [_scored(t, t + 30, 80.0, f"autre sujet {t}") for t in (100, 2000, 3500)]

    selected = _select(cluster + elsewhere, 6, ranker=None)

    assert all(600 <= sc.candidate.start <= 1400 for sc in selected), \
        "sans classeur, la selection doit rester le top des scores"


def test_a_much_better_passage_still_wins_over_diversity():
    # La diversite ne doit pas primer sur la qualite : un passage nettement
    # meilleur reste pris, meme proche d'un deja retenu.
    scored = [
        _scored(100, 130, 95.0, "sujet alpha tres fort"),
        _scored(135, 165, 94.0, "sujet beta different mais juste a cote"),
        _scored(5000, 5030, 40.0, "sujet gamma tres loin mais tres faible"),
    ]

    selected = _select(scored, 2, _ranker(2))
    starts = {sc.candidate.start for sc in selected}

    assert 100 in starts
    assert 5000 not in starts, "un candidat mediocre ne doit pas passer devant grace a sa distance"


def test_diversity_strength_zero_gives_pure_priority_ranking():
    cluster = [_scored(600 + i * 40, 630 + i * 40, 95 - i, f"variante {i}") for i in range(6)]
    far = [_scored(5000, 5030, 60.0, "tout autre chose")]

    selected = _select(cluster + far, 3, _ranker(3, diversity_strength=0.0))

    assert all(sc.candidate.start < 1000 for sc in selected)


def test_the_selection_is_stable_across_runs():
    # Deux analyses de la meme video doivent donner la meme selection : sans
    # departage explicite, l'ordre dependrait de l'ordre d'iteration.
    scored = [_scored(i * 300, i * 300 + 30, 70.0, f"sujet {i}") for i in range(12)]

    first = [sc.candidate.start for sc in _select(list(scored), 5, _ranker(5))]
    second = [sc.candidate.start for sc in _select(list(reversed(scored)), 5, _ranker(5))]

    assert first == second


# ------------------------------------------------------------ priorite

def test_priority_falls_back_to_viral_when_every_weight_is_zero():
    scored = _scored(10, 40, 77.0)
    zeroed = {name: 0.0 for name in DEFAULT_WEIGHTS}

    assert compute_priority(scored, [], AUDIO_STATS, zeroed).total == pytest.approx(77.0)


def test_a_partial_weight_configuration_does_not_change_the_scale():
    # Poids ne sommant pas a 1 : le score doit rester dans 0-100, pas etre
    # divise ou multiplie silencieusement.
    scored = _scored(10, 40, 80.0)

    result = compute_priority(scored, [], AUDIO_STATS, {"viral": 3.0, "audio_quality": 1.0})

    assert 0.0 <= result.total <= 100.0


def test_visual_quality_is_reported_as_absent_not_invented():
    # Aucune image n'est decodee au moment de la selection : la composante doit
    # valoir zero et peser zero, jamais une valeur plausible inventee.
    result = compute_priority(_scored(10, 40, 80.0), [], AUDIO_STATS)

    assert result.visual_quality == 0.0
    assert DEFAULT_WEIGHTS["visual_quality"] == 0.0


def test_a_clip_that_lands_on_sentence_boundaries_scores_better_on_context():
    words = _words("Voici une premiere phrase complete. Et voici la seconde phrase du passage.", 0.0, 20.0)
    sentences = build_sentences(words, max_gap_s=0.6, question_starters=[])
    assert sentences

    aligned = _scored(sentences[0].start, sentences[0].end, 70.0)
    misaligned = _scored(sentences[0].start + 3.0, sentences[0].end + 3.0, 70.0)

    on_boundary = compute_priority(aligned, sentences, AUDIO_STATS).context_quality
    off_boundary = compute_priority(misaligned, sentences, AUDIO_STATS).context_quality
    assert on_boundary > off_boundary


def test_duration_outside_the_publishable_window_loses_priority_but_is_not_rejected():
    inside = publishability(_candidate(0, 40), {})
    too_long = publishability(_candidate(0, 200), {})

    assert inside == 100.0
    assert too_long == 0.0, "hors fenetre et hors tolerance"
    assert publishability(_candidate(0, 100), {}) > 0.0, "un depassement modere reste jouable"


# ------------------------------------------------------------ entonnoir

def test_the_funnel_reports_what_the_engine_went_through():
    scored = ([_scored(i * 300, i * 300 + 30, 95.0, f"fort {i}") for i in range(4)]
              + [_scored(4000 + i * 300, 4030 + i * 300, 40.0, f"faible {i}") for i in range(6)])
    ranker = _ranker(3)

    _select(scored, 3, ranker)

    funnel = ranker.funnel
    assert funnel.candidates == 10
    assert funnel.final == 3
    assert funnel.candidates >= funnel.interesting >= funnel.strong >= funnel.best
    assert len(funnel.lines()) == 5


def test_empty_intermediate_levels_are_omitted_from_the_display():
    # "25 candidats -> 0 interessants -> 0 a fort potentiel -> 4 clips" est
    # exact mais illisible et laisse croire a une incoherence.
    from content_factory.selection import SelectionFunnel

    lines = SelectionFunnel(candidates=25, interesting=0, strong=0, best=0, final=4).lines()

    assert lines == ["25 moments candidats detectes", "4 clips finaux"]
    assert len(SelectionFunnel(120, 70, 30, 18, 10).lines()) == 5, "tous les paliers quand ils existent"


def test_the_funnel_never_filters_anything_out():
    # Tous les candidats sont mediocres : on doit quand meme produire des clips.
    scored = [_scored(i * 300, i * 300 + 30, 12.0, f"faible {i}") for i in range(5)]
    ranker = _ranker(3)

    selected = _select(scored, 3, ranker)

    assert len(selected) == 3
    assert ranker.funnel.interesting == 0, "aucun ne passe le premier palier..."
    assert ranker.funnel.final == 3, "...et pourtant trois clips sont produits"


# ------------------------------------------- signatures et similarites

def test_stopwords_never_make_two_unrelated_passages_look_alike():
    a = diversity.topic_signature("Alors donc voila je vais vous parler de fermentation")
    b = diversity.topic_signature("Alors donc voila je vais vous parler de motorisation")

    assert diversity.topic_similarity(a, b) < 0.4


def test_an_empty_or_short_passage_has_no_signature_and_no_similarity():
    assert diversity.topic_signature("") == frozenset()
    assert diversity.topic_similarity(frozenset(), diversity.topic_signature("boulangerie")) == 0.0


def test_the_temporal_horizon_scales_with_the_video_length():
    long_video = diversity.default_horizon(7200, 10)
    short_video = diversity.default_horizon(600, 10)

    assert long_video > short_video
    assert diversity.default_horizon(60, 10) >= 30.0, "plancher pour les videos courtes"
    assert diversity.default_horizon(7200, 1) == 0.0, "un seul clip : pas de diversite a mesurer"
