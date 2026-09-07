"""Decoupage des mots horodates de Whisper en phrases."""
from core.models import Word
from editing.sentences import (
    build_sentences,
    index_containing,
    index_last_ending_at_or_before,
    index_next_starting_at_or_after,
)


def _words(spec):
    """spec : liste de (texte, start, end)."""
    return [Word(text=t, start=s, end=e) for t, s, e in spec]


def test_strong_punctuation_ends_a_sentence():
    words = _words([("Bonjour", 0.0, 0.4), ("tout", 0.4, 0.7), ("le", 0.7, 0.8), ("monde.", 0.8, 1.2),
                    ("On", 1.3, 1.5), ("commence.", 1.5, 2.0)])

    sentences = build_sentences(words)

    assert len(sentences) == 2
    assert sentences[0].text == "Bonjour tout le monde."
    assert sentences[0].ends_with_punctuation
    assert sentences[0].start == 0.0 and sentences[0].end == 1.2


def test_long_pause_ends_a_sentence_even_without_punctuation():
    words = _words([("alors", 0.0, 0.5), ("voila", 0.5, 1.0), ("ensuite", 3.0, 3.5)])

    sentences = build_sentences(words, max_gap_s=0.6)

    assert len(sentences) == 2
    assert sentences[0].text == "alors voila"
    assert not sentences[0].ends_with_punctuation  # coupee sur un silence, pas un point


def test_question_detected_by_question_mark():
    words = _words([("Tu", 0.0, 0.2), ("viens", 0.2, 0.6), ("?", 0.6, 0.7)])

    sentences = build_sentences(words)

    assert sentences[0].is_question


def test_question_detected_by_starter_when_whisper_omits_the_mark():
    words = _words([("Pourquoi", 0.0, 0.5), ("ca", 0.5, 0.7), ("marche", 0.7, 1.1)])

    sentences = build_sentences(words, question_starters=["pourquoi", "comment"])

    assert sentences[0].is_question


def test_closing_quote_after_the_period_still_ends_the_sentence():
    words = _words([("Il", 0.0, 0.2), ("a", 0.2, 0.3), ('dit.»', 0.3, 0.8), ("Ensuite", 0.9, 1.4)])

    sentences = build_sentences(words)

    assert len(sentences) == 2
    assert sentences[0].ends_with_punctuation


def test_no_words_gives_no_sentences():
    assert build_sentences([]) == []


def test_index_helpers_locate_the_right_sentence():
    words = _words([("un.", 0.0, 1.0), ("deux.", 2.0, 3.0), ("trois.", 4.0, 5.0)])
    sentences = build_sentences(words)

    assert index_containing(sentences, 2.5) == 1
    assert index_containing(sentences, 1.5) is None  # silence entre deux phrases
    assert index_next_starting_at_or_after(sentences, 1.5) == 1
    assert index_last_ending_at_or_before(sentences, 3.5) == 1
