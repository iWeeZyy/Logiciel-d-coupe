"""Detection intelligente du contexte : recalage des bornes d'un clip.

Tests purs -- aucune video, aucun ffmpeg : editing/context.py ne travaille que
sur des phrases horodatees.
"""
from core.models import Word
from editing.context import adjust_clip_bounds, detect_category, resolve_overlaps
from editing.sentences import build_sentences


def _sentence_words(text: str, start: float, end: float):
    """Repartit uniformement les mots de `text` entre start et end."""
    tokens = text.split()
    step = (end - start) / len(tokens)
    return [Word(text=t, start=start + i * step, end=start + (i + 1) * step)
            for i, t in enumerate(tokens)]


def _build(spec):
    """spec : liste de (texte, start, end), une entree par phrase."""
    words = []
    for text, start, end in spec:
        words.extend(_sentence_words(text, start, end))
    return build_sentences(words, max_gap_s=0.6, question_starters=["pourquoi", "comment"])


BASE = dict(video_duration=120.0, max_duration=60.0, min_duration=5.0,
            padding_before_s=0.0, padding_after_s=0.0, min_confidence=0.0)


def test_start_inside_a_sentence_goes_back_to_its_beginning():
    sentences = _build([("Je vais vous raconter une histoire.", 10.0, 14.0),
                        ("Ca a change ma vie.", 14.5, 17.0)])

    result = adjust_clip_bounds(start=12.0, end=17.0, sentences=sentences, **BASE)

    assert result.applied
    assert result.start == 10.0  # remonte au debut de la phrase, pas de phrase coupee
    assert any("debut remonte" in r for r in result.reasons)


def test_end_inside_a_sentence_is_extended_to_its_end():
    sentences = _build([("Le probleme est simple.", 10.0, 13.0),
                        ("La solution tient en un mot.", 13.5, 17.0)])

    result = adjust_clip_bounds(start=10.0, end=15.0, sentences=sentences, **BASE)

    assert result.end == 17.0
    assert any("fin prolongee" in r for r in result.reasons)


def test_answer_after_a_question_is_included_as_payoff():
    sentences = _build([("Pourquoi personne n en parle ?", 10.0, 13.0),
                        ("Parce que ca coute trop cher.", 13.4, 16.0)])

    result = adjust_clip_bounds(start=10.0, end=13.0, sentences=sentences, **BASE)

    assert result.end == 16.0
    assert any("payoff" in r for r in result.reasons)


def test_answer_is_not_included_when_it_arrives_too_late():
    sentences = _build([("Pourquoi personne n en parle ?", 10.0, 13.0),
                        ("Parce que ca coute trop cher.", 20.0, 23.0)])

    # min_duration abaissee : sans ca, c'est la duree minimale (et non le
    # payoff) qui deciderait de la fin, et le test ne testerait plus rien.
    result = adjust_clip_bounds(start=10.0, end=13.0, sentences=sentences,
                               **{**BASE, "payoff_max_gap_s": 1.5, "min_duration": 2.0})

    assert result.end == 13.0


def test_maximum_duration_is_never_exceeded():
    sentences = _build([("Une phrase tres longue qui dure vraiment longtemps a l oral.", 0.0, 40.0),
                        ("Et une autre juste apres qui dure aussi longtemps.", 40.5, 80.0)])

    result = adjust_clip_bounds(start=5.0, end=45.0, sentences=sentences,
                               **{**BASE, "max_duration": 30.0})

    assert result.duration <= 30.0 + 1e-6


def test_padding_never_eats_into_the_neighbouring_sentence():
    sentences = _build([("Phrase precedente.", 0.0, 5.0),
                        ("Phrase du clip.", 5.2, 9.0),
                        ("Phrase suivante.", 9.3, 13.0)])

    result = adjust_clip_bounds(start=5.2, end=9.0, sentences=sentences,
                               **{**BASE, "padding_before_s": 3.0, "padding_after_s": 3.0})

    # La marge s'arrete au silence : jamais sur le dernier mot d'avant ni sur le
    # premier mot d'apres.
    assert result.start >= 5.0
    assert result.end <= 9.3


def test_no_sentence_means_no_adjustment_at_all():
    result = adjust_clip_bounds(start=10.0, end=20.0, sentences=[], **BASE)

    assert not result.applied
    assert (result.start, result.end) == (10.0, 20.0)
    assert result.confidence == 0.0


def test_low_confidence_keeps_the_original_bounds():
    # Une seule phrase enorme : impossible de recaler debut et fin dessus sans
    # depasser la duree max -> confiance basse -> on ne touche a rien.
    sentences = _build([("Un monologue continu sans aucune ponctuation forte du tout ici", 0.0, 100.0)])

    result = adjust_clip_bounds(start=30.0, end=45.0, sentences=sentences,
                               **{**BASE, "max_lookback_s": 1.0, "max_lookahead_s": 1.0,
                                  "min_confidence": 0.9})

    assert not result.applied
    assert (result.start, result.end) == (30.0, 45.0)


def test_bounds_never_fall_inside_a_word():
    sentences = _build([("Premiere phrase ici.", 0.0, 4.0), ("Deuxieme phrase la.", 4.5, 9.0)])
    all_words = [w for s in sentences for w in s.words]

    result = adjust_clip_bounds(start=1.3, end=6.7, sentences=sentences,
                               **{**BASE, "max_lookback_s": 0.0, "max_lookahead_s": 0.0})

    for w in all_words:
        assert not (w.start < result.start < w.end), "une borne tombe au milieu d'un mot"
        assert not (w.start < result.end < w.end), "une borne tombe au milieu d'un mot"


def test_category_comes_from_the_configured_markers_only():
    markers = {"revelation": ["en fait", "la verite"], "conclusion": ["au final"]}

    assert detect_category("Et en fait, personne ne le sait", markers) == "revelation"
    assert detect_category("Au final c'est ca", markers) == "conclusion"
    # Rien ne ressort -> aucune categorie inventee.
    assert detect_category("Il fait beau aujourd'hui", markers) == ""
    assert detect_category("n'importe quoi", {}) == ""


def test_comment_keys_of_the_config_are_never_returned_as_a_category():
    # config/editing.json porte ses commentaires dans une cle "_comment" dont la
    # valeur est une CHAINE : parcourue comme une liste de marqueurs, chacun de
    # ses caracteres se retrouve dans n'importe quel texte et elle ressortait
    # comme categorie de tous les clips (bug reel vu en test end-to-end).
    markers = {
        "_comment": "Marqueurs servant a etiqueter le type narratif d'un clip",
        "revelation": ["en fait"],
    }

    assert detect_category("Et en fait personne ne le sait", markers) == "revelation"
    assert detect_category("Une phrase quelconque sans marqueur", markers) == ""


def test_resolve_overlaps_trims_the_lower_scored_clip():
    items = [(0.0, 30.0, 90.0), (25.0, 55.0, 70.0)]

    resolved = resolve_overlaps(items, min_gap=0.0, min_duration=5.0)

    assert resolved[0] == (0.0, 30.0)          # le mieux note garde ses bornes
    assert resolved[1] == (30.0, 55.0)          # l'autre est rogne


def test_resolve_overlaps_drops_a_clip_fully_covered_by_a_better_one():
    items = [(0.0, 60.0, 90.0), (10.0, 20.0, 50.0)]

    resolved = resolve_overlaps(items, min_gap=0.0, min_duration=5.0)

    assert resolved[0] == (0.0, 60.0)
    assert resolved[1] is None


def test_resolve_overlaps_leaves_disjoint_clips_untouched():
    items = [(0.0, 20.0, 90.0), (40.0, 60.0, 80.0)]

    resolved = resolve_overlaps(items, min_gap=0.0, min_duration=5.0)

    assert resolved == [(0.0, 20.0), (40.0, 60.0)]
