"""Titres/description extraits et selection d'image de miniature."""
from core.models import Word
from editing.metadata import (
    KIND_CURIOSITY,
    KIND_DIRECT,
    KIND_PUNCHY,
    build_metadata,
    strip_leading_fillers,
)
from editing.sentences import build_sentences
from editing.thumbnail import (
    VARIANT_CONTEXT,
    VARIANT_FACE,
    FrameMetrics,
    choose_text,
    choose_text_layout,
    choose_variants,
    score_frames,
)
from video.face_detector import FaceBox


def _sentences(spec, question_starters=("pourquoi", "comment")):
    words = []
    for text, start, end in spec:
        tokens = text.split()
        step = (end - start) / len(tokens)
        words += [Word(text=t, start=start + i * step, end=start + (i + 1) * step)
                  for i, t in enumerate(tokens)]
    return build_sentences(words, question_starters=list(question_starters))


# ------------------------------------------------------------- titres

def test_titles_only_reuse_words_actually_spoken():
    spec = [("Le secret des boulangers est la temperature de l eau.", 0.0, 5.0),
            ("Personne ne vous le dit jamais.", 5.5, 8.0)]
    sentences = _sentences(spec)
    spoken = {w.text.lower().strip(".,") for s in sentences for w in s.words}

    meta = build_metadata(sentences, keyword_terms=["secret", "jamais"])

    assert meta.titles
    for title in meta.titles:
        for word in title.text.replace("…", "").split():
            assert word.lower().strip(".,!?") in spoken, f"mot invente : {word}"


def test_a_real_question_becomes_the_curiosity_title():
    spec = [("Pourquoi personne n en parle jamais ?", 0.0, 4.0),
            ("Parce que ca derange beaucoup de monde.", 4.5, 8.0)]

    meta = build_metadata(_sentences(spec), keyword_terms=["jamais"])

    curiosity = next((t for t in meta.titles if t.kind == KIND_CURIOSITY), None)
    assert curiosity is not None
    assert "ourquoi" in curiosity.text


def test_the_three_kinds_are_produced_on_rich_content():
    spec = [("Le secret des boulangers est la temperature de l eau froide.", 0.0, 6.0),
            ("Pourquoi personne n en parle jamais vraiment ?", 6.5, 10.0),
            ("Ca change tout.", 10.5, 12.0)]

    meta = build_metadata(_sentences(spec), keyword_terms=["secret", "jamais"])

    kinds = {t.kind for t in meta.titles}
    assert {KIND_DIRECT, KIND_CURIOSITY, KIND_PUNCHY} <= kinds


def test_leading_discourse_fillers_are_stripped():
    assert strip_leading_fillers("Et donc du coup le secret") == "le secret"
    assert strip_leading_fillers("Alors voila") == ""
    # "donc" au milieu porte du sens et doit rester.
    assert strip_leading_fillers("Le secret donc est simple") == "Le secret donc est simple"


def test_nothing_is_produced_from_an_empty_clip():
    meta = build_metadata([])

    assert meta.titles == ()
    assert meta.description == ""
    assert any("aucune phrase" in r for r in meta.reasons)


def test_hashtags_come_only_from_keywords_really_present():
    spec = [("Le secret est la temperature.", 0.0, 4.0)]

    meta = build_metadata(_sentences(spec), keyword_terms=["secret", "incroyable", "arnaque"])

    assert meta.hashtags == ("#secret",)   # les deux autres ne sont pas dans le clip


def test_description_uses_real_sentences_and_ends_cleanly():
    spec = [("Bonjour et bienvenue dans cette video.", 0.0, 4.0),
            ("Le secret est la temperature de l eau.", 4.5, 9.0)]

    meta = build_metadata(_sentences(spec), keyword_terms=["secret"])

    assert meta.description
    assert meta.description.endswith(".")
    assert "bienvenue" in meta.description.lower()


# --------------------------------------------------------- miniatures

def _metrics(t, sharpness=200.0, brightness=128.0, face=None, eyes=None, change=0.0):
    return FrameMetrics(t=t, sharpness=sharpness, brightness=brightness, face=face,
                        eyes_open=eyes, change_from_previous=change)


FACE = FaceBox(x=0.35, y=0.2, w=0.3, h=0.35, confidence=0.95)


def test_blurry_and_transition_frames_are_rejected():
    scores = score_frames([
        _metrics(0.0, sharpness=5.0),
        _metrics(1.0, change=0.9),
        _metrics(2.0, face=FACE),
    ])

    assert scores[0].score == 0.0 and "floue" in scores[0].reasons[0]
    assert scores[1].score == 0.0 and "transition" in scores[1].reasons[0]
    assert scores[2].score > 0


def test_a_visible_face_with_open_eyes_scores_highest():
    scores = score_frames([
        _metrics(0.0),
        _metrics(1.0, face=FACE),
        _metrics(2.0, face=FACE, eyes=True),
    ])

    assert scores[2].score > scores[1].score > scores[0].score


def test_three_distinct_variants_are_chosen():
    scores = score_frames([
        _metrics(0.0, face=FACE, eyes=True),
        _metrics(3.0),
        _metrics(6.0, face=FACE, eyes=True, sharpness=400.0),
    ])

    variants = choose_variants(scores, min_gap_s=1.0)

    assert len(variants) == 3
    times = [v.t for v in variants.values()]
    assert len(set(times)) == 3


def test_the_least_bad_frame_is_still_proposed_when_none_qualifies():
    # "si aucune frame n'est suffisamment bonne -> proposer la meilleure sans
    # traitement agressif", pas rien du tout.
    scores = score_frames([_metrics(0.0, sharpness=3.0), _metrics(1.0, sharpness=8.0)])

    variants = choose_variants(scores)

    assert len(variants) == 1
    assert variants[VARIANT_FACE].t == 1.0


def test_text_goes_to_the_band_furthest_from_the_face():
    low_face = choose_text_layout(0.85, {"top": 40.0, "bottom": 40.0})
    high_face = choose_text_layout(0.20, {"top": 40.0, "bottom": 40.0})

    assert low_face.band == "top"
    assert high_face.band == "bottom"


def test_text_colour_follows_the_real_background_luminance():
    on_dark = choose_text_layout(None, {"bottom": 30.0})
    on_light = choose_text_layout(None, {"bottom": 220.0})

    assert on_dark.light_text is True
    assert on_light.light_text is False


def test_thumbnail_text_is_the_shortest_title_and_never_invented():
    titles = [{"text": "Le secret des boulangers est la temperature"}, {"text": "Ca change tout"}]

    assert choose_text(titles) == "CA CHANGE TOUT"
    assert choose_text([]) == ""
