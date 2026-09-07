"""Sous-titres intelligents : regroupement, mise en evidence, placement."""
from core.models import Word
from editing.captions import (
    build_captions,
    choose_margin_v,
    group_words,
    score_emphasis,
)


def _words(spec):
    return [Word(text=t, start=s, end=e) for t, s, e in spec]


def _even_words(texts, start=0.0, step=0.4):
    return [Word(text=t, start=start + i * step, end=start + (i + 1) * step)
            for i, t in enumerate(texts)]


# ------------------------------------------------------------- regroupement

def test_group_words_respects_the_maximum_per_group():
    words = _even_words(["un", "deux", "trois", "quatre", "cinq"])

    groups = group_words(words, max_words=2)

    assert [[w.text for w in g] for g in groups] == [["un", "deux"], ["trois", "quatre"], ["cinq"]]


def test_group_words_breaks_on_a_pause():
    words = _words([("alors", 0.0, 0.4), ("voila", 0.4, 0.8), ("ensuite", 3.0, 3.4)])

    groups = group_words(words, max_words=10, sentence_gap_s=0.6)

    assert len(groups) == 2


def test_group_words_breaks_when_the_line_would_be_too_long():
    words = _even_words(["anticonstitutionnellement", "extraordinairement", "ok"])

    groups = group_words(words, max_words=5, max_chars=30)

    assert len(groups) >= 2
    for g in groups:
        text = " ".join(w.text for w in g)
        assert len(text) <= 30 or len(g) == 1  # un mot seul peut depasser, on ne le coupe pas


def test_sentence_end_break_is_opt_in_so_legacy_styles_are_untouched():
    words = _words([("Salut.", 0.0, 0.4), ("Ensuite", 0.5, 0.9), ("on", 0.9, 1.1)])

    without = group_words(words, max_words=3, break_on_sentence_end=False)
    with_break = group_words(words, max_words=3, break_on_sentence_end=True)

    assert len(without) == 1          # comportement historique : un seul bloc
    assert len(with_break) == 2       # mode smart : la phrase finie coupe le bloc


# -------------------------------------------------------- mise en evidence

def test_emphasis_detects_configured_keywords_numbers_and_loud_words():
    words = _even_words(["voici", "le", "secret", "des", "10", "regles"])
    loudness = [-30.0] * 6
    loudness[0] = -10.0  # "voici" prononce beaucoup plus fort

    scores = score_emphasis(
        words, keyword_terms=["secret"], loudness_db=loudness, loud_threshold_db=-15.0
    )

    assert scores[2] > 0            # mot-cle configure
    assert scores[4] > 0            # chiffre reellement prononce
    assert scores[0] > 0            # mot nettement plus fort
    assert 1 not in scores          # "le" n'a aucun signal


def test_emphasis_ignores_punctuation_around_a_keyword():
    words = _even_words(["c'est", "un", "secret,"])

    scores = score_emphasis(words, keyword_terms=["secret"])

    assert scores.get(2, 0) > 0


def test_at_most_one_word_is_emphasised_per_group():
    words = _even_words(["secret", "incroyable", "jamais", "vu"])

    groups = build_captions(
        words, max_words_per_group=2,
        emphasis_scores=score_emphasis(words, keyword_terms=["secret", "incroyable", "jamais"]),
        max_emphasis_ratio=1.0, min_emphasis_score=0.5,
    )

    for group in groups:
        assert sum(1 for w in group.words if w.emphasized) <= 1


def test_nothing_is_emphasised_when_no_word_stands_out():
    words = _even_words(["il", "fait", "beau", "aujourd", "hui"])

    groups = build_captions(words, max_words_per_group=2,
                            emphasis_scores=score_emphasis(words, keyword_terms=["secret"]))

    assert not any(w.emphasized for g in groups for w in g.words)


def test_emphasis_ratio_caps_how_many_words_are_highlighted():
    words = _even_words(["secret"] * 10)

    groups = build_captions(
        words, max_words_per_group=1,
        emphasis_scores=score_emphasis(words, keyword_terms=["secret"]),
        max_emphasis_ratio=0.2, min_emphasis_score=0.5,
    )

    assert sum(1 for g in groups for w in g.words if w.emphasized) == 2


# -------------------------------------------------------------- timings

def test_short_group_is_held_long_enough_to_be_read():
    words = _words([("bref", 10.0, 10.1)])

    groups = build_captions(words, clip_start=10.0, max_words_per_group=2, min_display_s=0.3)

    assert groups[0].duration >= 0.3


def test_groups_never_overlap_and_are_relative_to_the_clip():
    words = _even_words(["un", "deux", "trois", "quatre"], start=20.0, step=0.5)

    groups = build_captions(words, clip_start=20.0, max_words_per_group=2,
                            min_display_s=0.8, gap_s=0.05)

    assert groups[0].start == 0.0
    for a, b in zip(groups, groups[1:]):
        assert a.end <= b.start + 1e-9


def test_no_words_gives_no_captions():
    assert build_captions([]) == []


# ---------------------------------------------------------- positionnement

MARGIN = dict(default_margin_v=380, text_height_px=200, frame_height=1920,
              avoid_half_frac=0.16, min_margin_v=140)


def test_without_a_detected_face_the_style_margin_is_kept():
    assert choose_margin_v(None, **MARGIN) == 380


def test_a_face_in_the_upper_frame_leaves_the_subtitles_where_they_are():
    assert choose_margin_v(0.35, **MARGIN) == 380


def test_a_face_low_in_the_frame_pushes_the_subtitles_above_it():
    margin = choose_margin_v(0.85, **MARGIN)

    assert margin > 380  # remontes au-dessus du visage
    # Le texte reste dans le cadre.
    assert margin <= 1920 - 200 - 140


def test_a_face_in_the_middle_keeps_the_subtitles_below_it():
    margin = choose_margin_v(0.62, **MARGIN)

    assert 140 <= margin < 380
