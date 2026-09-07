"""Montage automatique : silences, hesitations, zooms, et chaine ffmpeg."""
from core.models import Word
from editing.framing import FramingKeyframe, FramingPlan
from editing.sentences import build_sentences
from editing.silence_cut import build_montage_plan, detect_fillers, detect_silences
from editing.timeline import EditList
from editing.zoom import build_zoom_track
from video.filter_graph import build_ffmpeg_args, build_audio_chain, piecewise_expression


def _w(text, start, end):
    return Word(text=text, start=start, end=end)


ALWAYS_SILENT = lambda a, b: True    # noqa: E731 -- lambda lisible dans un test
NEVER_SILENT = lambda a, b: False    # noqa: E731


# ----------------------------------------------------------- silences

def test_a_long_gap_between_two_words_is_removed():
    words = [_w("avant", 0.0, 0.5), _w("apres", 3.0, 3.5)]

    gaps = detect_silences(words, 0.0, 4.0, min_silence_s=0.5, keep_padding_s=0.1,
                           is_silent=ALWAYS_SILENT)

    assert len(gaps) == 1
    assert gaps[0] == (0.6, 2.9)      # marge de securite conservee de chaque cote


def test_a_short_gap_is_left_alone():
    words = [_w("un", 0.0, 0.5), _w("deux", 0.8, 1.2)]

    assert detect_silences(words, 0.0, 2.0, min_silence_s=0.5, is_silent=ALWAYS_SILENT) == []


def test_a_gap_that_is_not_actually_quiet_is_never_cut():
    # Rire, musique, reaction : Whisper n'a rien transcrit, mais il se passe
    # quelque chose -- couper serait une faute.
    words = [_w("avant", 0.0, 0.5), _w("apres", 3.0, 3.5)]

    assert detect_silences(words, 0.0, 4.0, min_silence_s=0.5, is_silent=NEVER_SILENT) == []


def test_a_pause_right_after_a_question_is_protected():
    words = [_w("Vraiment", 0.0, 0.4), _w("?", 0.4, 0.5), _w("Oui", 3.0, 3.4)]

    gaps = detect_silences(words, 0.0, 4.0, min_silence_s=0.5,
                           protected=[(0.5, 1.7)], is_silent=ALWAYS_SILENT)

    assert gaps == []


# --------------------------------------------------------- hesitations

def test_configured_filler_words_are_removed():
    words = [_w("alors", 0.0, 0.4), _w("euh", 0.4, 0.7), _w("voila", 0.7, 1.1)]

    removed = detect_fillers(words, ["euh", "hmm"])

    assert len(removed) == 1
    assert removed[0][0] < 0.4 and removed[0][1] > 0.7


def test_an_immediate_repetition_drops_the_first_occurrence():
    words = [_w("je", 0.0, 0.2), _w("je", 0.25, 0.45), _w("pense", 0.5, 0.9)]

    removed = detect_fillers(words, [], remove_repetitions=True)

    assert len(removed) == 1
    assert removed[0][0] < 0.0 + 0.05


def test_a_deliberate_repetition_far_apart_is_kept():
    words = [_w("tres", 0.0, 0.3), _w("bien", 0.4, 0.8), _w("tres", 2.0, 2.3)]

    assert detect_fillers(words, [], remove_repetitions=True) == []


# ---------------------------------------------------------------- plan

def test_montage_plan_produces_an_edit_list_and_shortens_the_clip():
    # Clip de 20 s avec un silence de 3,4 s : le montage doit s'appliquer sans
    # atteindre le plafond de 35 %.
    words = ([_w(f"mot{i}", i * 0.4, i * 0.4 + 0.35) for i in range(20)]
             + [_w("Ensuite", 11.4, 11.8), _w("euh", 11.9, 12.2),
                _w("on", 12.3, 12.5), _w("continue.", 12.5, 13.1)]
             + [_w(f"suite{i}", 13.5 + i * 0.4, 13.5 + i * 0.4 + 0.35) for i in range(15)])

    plan = build_montage_plan(words, 0.0, 20.0, filler_terms=["euh"], is_silent=ALWAYS_SILENT)

    assert plan.applied
    assert plan.removed_silences >= 1
    assert plan.removed_fillers >= 1
    assert plan.edit_list.output_duration < 20.0
    assert plan.edit_list.removed_duration > 3.0


def test_montage_is_abandoned_when_it_would_cut_too_much():
    # Presque que du silence : couper serait reecrire le rythme du locuteur.
    words = [_w("un", 0.0, 0.3), _w("deux", 9.0, 9.3), _w("trois", 9.4, 9.7)]

    plan = build_montage_plan(words, 0.0, 10.0, max_removed_ratio=0.35, is_silent=ALWAYS_SILENT)

    assert not plan.applied
    assert plan.edit_list.is_identity
    assert any("plafond" in r for r in plan.reasons)


def test_a_clip_with_nothing_to_remove_keeps_its_identity_edit_list():
    words = [_w("un", 0.0, 0.4), _w("deux", 0.4, 0.8), _w("trois", 0.8, 1.2)]

    plan = build_montage_plan(words, 0.0, 1.4, is_silent=ALWAYS_SILENT)

    assert not plan.applied and plan.edit_list.is_identity


def test_subtitles_follow_the_montage_automatically():
    # Le point cle de tout le montage : apres une coupe, les mots doivent etre
    # recales, sinon les sous-titres se desynchronisent.
    words = [_w("avant", 0.0, 0.5), _w("apres", 3.0, 3.5)]
    # max_removed_ratio releve : ce test porte sur le recalage, pas sur le
    # plafond de securite (teste separement).
    plan = build_montage_plan(words, 0.0, 4.0, min_silence_s=0.5, keep_padding_s=0.1,
                              max_removed_ratio=1.0, is_silent=ALWAYS_SILENT)

    remapped = plan.edit_list.remap_words(words)

    assert remapped[0].start == 0.0
    assert remapped[1].start < 3.0          # le mot a avance du silence retire
    assert abs(remapped[1].start - 0.7) < 0.01


# ---------------------------------------------------------------- zoom

def test_zoom_events_are_spaced_and_capped():
    highlights = [1.0, 1.5, 2.0, 8.0, 15.0, 22.0, 30.0, 40.0]

    track = build_zoom_track(highlights, 0.0, 60.0, min_gap_s=4.0, max_events=3)

    assert track.events == 3
    assert max(kf.zoom for kf in track.keyframes) > 1.0


def test_no_highlight_means_no_zoom_at_all():
    track = build_zoom_track([], 0.0, 60.0)

    assert track.is_empty and track.events == 0


def test_zoom_envelope_starts_and_ends_at_one():
    track = build_zoom_track([10.0], 0.0, 30.0)

    assert track.keyframes[0].zoom == 1.0
    assert track.keyframes[-1].zoom == 1.0


# -------------------------------------------------------- chaine ffmpeg

def test_piecewise_expression_gates_are_mutually_exclusive():
    expr = piecewise_expression([(0.0, 100.0), (1.0, 200.0), (2.0, 300.0)])

    # Une seule porte peut s'activer a la fois, sinon deux segments s'additionnent
    # aux frontieres et la valeur double.
    assert "gte(t\\,0.0000)*lt(t\\,1.0000)" in expr
    assert "lt(t\\,0.0000)*100.0000" in expr      # avant le premier point
    assert "gte(t\\,2.0000)*300.0000" in expr     # apres le dernier


def test_a_single_point_gives_a_constant():
    assert piecewise_expression([(3.0, 42.0)]) == "42.0000"


def test_identity_edit_list_uses_a_simple_vf_not_a_filter_complex():
    args = build_ffmpeg_args(
        video_path="in.mp4", edit_list=EditList.identity(10.0, 40.0),
        framing_plan=None, zoom_track=None, src_w=1920, src_h=1080, fps=25.0,
        face_hint=None, ass_path=None, audio_cfg=None,
        export_settings={}, out_mp4_path="out.mp4",
    )

    assert "-vf" in args and "-filter_complex" not in args
    assert args[args.index("-ss") + 1] == "10.000"
    assert "crop=" in args[args.index("-vf") + 1]


def test_a_cut_edit_list_builds_a_trim_concat_graph():
    edl = EditList.keeping(10.0, 40.0, removed=[(20.0, 22.0)])

    args = build_ffmpeg_args(
        video_path="in.mp4", edit_list=edl, framing_plan=None, zoom_track=None,
        src_w=1920, src_h=1080, fps=25.0, face_hint=None, ass_path=None,
        audio_cfg=None, export_settings={}, out_mp4_path="out.mp4",
    )

    graph = args[args.index("-filter_complex") + 1]
    assert "concat=n=2:v=1:a=1" in graph
    # Les temps sont relatifs au point d'entree -ss, pas absolus.
    assert "trim=start=0.000:end=10.000" in graph
    assert "atrim=start=12.000:end=30.000" in graph
    assert "-map" in args


def test_a_moving_framing_plan_produces_a_time_varying_crop():
    plan = FramingPlan(
        keyframes=(FramingKeyframe(10.0, 0.3, 0.4), FramingKeyframe(20.0, 0.7, 0.4)),
        mode="track", confidence=0.9,
    )

    args = build_ffmpeg_args(
        video_path="in.mp4", edit_list=EditList.identity(10.0, 30.0),
        framing_plan=plan, zoom_track=None, src_w=1920, src_h=1080, fps=25.0,
        face_hint=None, ass_path=None, audio_cfg=None,
        export_settings={}, out_mp4_path="out.mp4",
    )

    vf = args[args.index("-vf") + 1]
    assert "crop=" in vf and "x='" in vf and "gte(t" in vf


def test_audio_chain_is_empty_unless_explicitly_enabled():
    assert build_audio_chain(None) == ""
    assert build_audio_chain({"enabled": False, "loudnorm": True}) == ""
    assert "loudnorm" in build_audio_chain({"enabled": True})
