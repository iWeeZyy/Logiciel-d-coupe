"""EditList : normalisation des segments et conversion temps source <-> sortie.

Tests purs (aucun ffmpeg, aucune video) -- c'est tout l'interet d'avoir isole
cette logique dans editing/timeline.py.
"""
from core.models import Word
from editing.timeline import Cut, EditList


def _w(text, start, end):
    return Word(text=text, start=start, end=end)


def test_identity_keeps_one_cut_and_full_duration():
    edl = EditList.identity(10.0, 25.0)

    assert edl.is_identity
    assert edl.output_duration == 15.0
    assert edl.removed_duration == 0.0
    assert edl.to_output_time(10.0) == 0.0
    assert edl.to_output_time(25.0) == 15.0


def test_from_ranges_sorts_and_merges_overlapping_segments():
    edl = EditList.from_ranges([(5.0, 8.0), (0.0, 3.0), (2.0, 6.0)])

    assert edl.cuts == (Cut(0.0, 8.0),)


def test_from_ranges_drops_empty_segments():
    edl = EditList.from_ranges([(1.0, 1.0), (2.0, 5.0), (7.0, 6.0)])

    assert edl.cuts == (Cut(2.0, 5.0),)


def test_keeping_removes_the_given_intervals():
    edl = EditList.keeping(0.0, 30.0, removed=[(10.0, 12.0), (20.0, 21.0)])

    assert edl.cuts == (Cut(0.0, 10.0), Cut(12.0, 20.0), Cut(21.0, 30.0))
    assert edl.output_duration == 27.0
    assert edl.removed_duration == 3.0
    assert not edl.is_identity


def test_output_time_skips_removed_intervals():
    edl = EditList.keeping(0.0, 30.0, removed=[(10.0, 12.0)])

    assert edl.to_output_time(5.0) == 5.0
    assert edl.to_output_time(13.0) == 11.0  # 10s gardees + 1s apres la coupe


def test_output_time_is_none_inside_a_removed_interval():
    edl = EditList.keeping(0.0, 30.0, removed=[(10.0, 12.0)])

    assert edl.to_output_time(11.0) is None
    # Rabattu sur la borne conservee la plus proche quand on ne veut pas de None.
    assert edl.to_output_time_clamped(11.0) == 10.0


def test_source_time_is_the_inverse_of_output_time():
    edl = EditList.keeping(0.0, 30.0, removed=[(10.0, 12.0), (20.0, 21.0)])

    for source_t in (0.0, 4.0, 9.9, 12.5, 19.0, 25.0):
        assert abs(edl.to_source_time(edl.to_output_time(source_t)) - source_t) < 1e-6


def test_remap_words_drops_removed_words_and_shifts_the_others():
    edl = EditList.keeping(0.0, 30.0, removed=[(10.0, 12.0)])
    words = [_w("avant", 5.0, 5.5), _w("coupe", 10.5, 11.5), _w("apres", 13.0, 13.5)]

    remapped = edl.remap_words(words)

    assert [w.text for w in remapped] == ["avant", "apres"]
    assert remapped[0].start == 5.0
    assert remapped[1].start == 11.0  # 13.0 - 2.0 de silence supprime


def test_remap_words_keeps_the_longest_kept_part_of_a_straddling_word():
    # Un mot a cheval sur une coupe n'est jamais scinde en deux : il garde sa
    # portion conservee la plus longue, sinon un sous-titre apparaitrait a moitie.
    edl = EditList.keeping(0.0, 30.0, removed=[(10.0, 12.0)])
    words = [_w("cheval", 9.0, 12.5)]

    remapped = edl.remap_words(words)

    assert len(remapped) == 1
    assert remapped[0].start == 9.0
    assert remapped[0].end == 10.0


def test_round_trip_serialisation():
    edl = EditList.keeping(0.0, 30.0, removed=[(10.0, 12.0)])

    assert EditList.from_dict(edl.to_dict()).cuts == edl.cuts
