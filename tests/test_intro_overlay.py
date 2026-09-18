"""Incrustation de l'intro sur le clip : construction des morceaux de filtre.

Pur -- video/intro_overlay.py ne fait que construire des chaines, verifiable
sans encoder quoi que ce soit (comme video/watermark.py).
"""
from editing.timeline import EditList
from video.filter_graph import build_ffmpeg_args
from video.intro_overlay import IntroOverlay, crop_rect, overlay_position, overlay_spec, prepare_filter
from video.watermark import Watermark


def _intro(**overrides):
    kwargs = dict(path="intro.mp4", src_w=1080, src_h=1920, duration=7.0)
    kwargs.update(overrides)
    return IntroOverlay(**kwargs)


def test_the_crop_matches_the_reference_asset_exactly():
    # L'asset livre (assets/branding/intro_follow.mp4) EST la reference
    # (1080x1920) : le cadre mesure doit ressortir tel quel, sans arrondi.
    assert crop_rect(1080, 1920) == (40, 330, 1000, 1140)


def test_the_crop_scales_proportionally_for_a_differently_sized_asset():
    x, y, w, h = crop_rect(2160, 3840)  # deux fois plus grand

    assert (x, y, w, h) == (80, 660, 2000, 2280)


def test_the_size_is_a_percentage_of_the_smaller_output_side():
    intro = _intro()

    # 35% de 1080 (le petit cote, meme en paysage) = 378, arrondi pair.
    assert "scale=378:-1" in prepare_filter(intro, 1080, 1920)
    assert "scale=378:-1" in prepare_filter(intro, 1920, 1080)


def test_the_crop_dimensions_reach_the_filter():
    intro = _intro()

    filt = prepare_filter(intro, 1080, 1920)
    assert "crop=1000:1140:40:330" in filt


def test_the_black_background_is_keyed_out():
    intro = _intro()

    filt = prepare_filter(intro, 1080, 1920)
    assert "colorkey=0x000000" in filt
    assert "format=yuva420p" in filt


def test_the_default_position_is_top_right_so_it_never_meets_the_watermark():
    intro = _intro()

    assert overlay_position(intro, 1080, 1920) == "W-w-43:43"


def test_each_position_has_its_own_expression():
    def pos(where):
        return overlay_position(_intro(position=where, margin_percent=5.0), 1000, 1000)

    assert pos("haut-gauche") == "50:50"
    assert pos("haut-centre") == "(W-w)/2:50"
    assert pos("haut-droite") == "W-w-50:50"
    assert pos("bas-gauche") == "50:H-h-50"
    assert pos("bas-centre") == "(W-w)/2:H-h-50"
    assert pos("bas-droite") == "W-w-50:H-h-50"


def test_an_unknown_position_falls_back_to_the_default():
    intro = _intro(position="au-milieu")

    assert overlay_position(intro, 1080, 1920) == overlay_position(_intro(), 1080, 1920)


def test_overlay_spec_enables_only_for_the_intro_duration():
    intro = _intro(duration=6.5)

    _, _, enable = overlay_spec(intro, 1080, 1920)

    assert enable == "between(t,0,6.500)"


def test_overlay_spec_clamps_a_negative_duration_to_zero():
    intro = _intro(duration=-1.0)

    _, _, enable = overlay_spec(intro, 1080, 1920)

    assert enable == "between(t,0,0.000)"


# -------------------------------------------------- integration : build_ffmpeg_args

def _args(**overrides):
    kwargs = dict(
        video_path="in.mp4",
        edit_list=EditList.identity(10.0, 40.0),
        framing_plan=None, zoom_track=None, src_w=1920, src_h=1080, fps=25.0,
        face_hint=None, ass_path=None, audio_cfg=None,
        export_settings={}, out_mp4_path="out.mp4",
    )
    kwargs.update(overrides)
    return build_ffmpeg_args(**kwargs)


def test_no_intro_means_a_single_input_and_no_overlay_at_all():
    args = _args(intro=None)

    assert args.count("-i") == 1
    assert "-filter_complex" not in args  # rien a composer : chemin -vf simple


def test_the_intro_is_an_extra_input_after_the_main_video():
    args = _args(intro=_intro())

    assert args[:4] == ["-ss", "10.000", "-i", "in.mp4"]
    assert "-i" in args and "intro.mp4" in args


def test_the_intro_overlay_carries_a_time_bounded_enable_expression():
    args = _args(intro=_intro(duration=7.0))
    graph = args[args.index("-filter_complex") + 1]

    assert "overlay=W-w-43:43:enable='between(t,0,7.000)'" in graph


def test_the_intro_is_composited_after_the_watermark_so_it_stays_on_top():
    watermark = Watermark(image="logo.png")
    args = _args(watermark=watermark, intro=_intro())
    graph = args[args.index("-filter_complex") + 1]

    # Le filigrane (entree 1) se compose avant l'intro (entree 2) : "ov0" (le
    # filigrane) apparait avant "ov1" (l'intro) dans le graphe.
    assert graph.index("[ov0]") < graph.index("[ov1]")
    assert "-i" in args and "logo.png" in args and "intro.mp4" in args
    # Chacun garde sa propre position -- aucun des deux calques ne porte la
    # marge de l'autre.
    assert "enable=" not in graph.split("[ov1]")[0]  # le filigrane, permanent


def test_a_permanent_layer_like_the_watermark_keeps_no_enable_expression():
    watermark = Watermark(image="logo.png")
    args = _args(watermark=watermark, intro=None)
    graph = args[args.index("-filter_complex") + 1]

    assert "enable=" not in graph
