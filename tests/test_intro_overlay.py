"""Incrustation de l'intro sur le clip : construction des morceaux de filtre.

Pur -- video/intro_overlay.py ne fait que construire des chaines, verifiable
sans encoder quoi que ce soit (comme video/watermark.py).
"""
from editing.timeline import EditList
from video.filter_graph import build_ffmpeg_args
from video.intro_overlay import (
    IntroOverlay,
    crop_rect,
    overlay_position,
    overlay_spec,
    prepare_filter,
    scaled_size,
)
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
    assert scaled_size(intro, 1080, 1920)[0] == 378
    assert scaled_size(intro, 1920, 1080)[0] == 378


def test_the_height_is_computed_explicitly_from_the_crop_aspect_ratio():
    intro = _intro()

    # Cadre 1000x1140 (ratio 1.14) mis a l'echelle sur une largeur de 378 :
    # meme ratio applique explicitement (arrondi pair), jamais un "-1" ffmpeg.
    width, height = scaled_size(intro, 1080, 1920)
    expected = round(width * 1140 / 1000)
    expected -= expected % 2
    assert height == expected


def test_video_and_mask_are_scaled_to_the_exact_same_size():
    # alphamerge exige des tailles identiques image par image : les deux
    # branches du sous-graphe doivent porter le meme scale=W:H explicite.
    intro = _intro()

    filt = prepare_filter(intro, 1080, 1920, video_index=2, mask_index=3)
    width, height = scaled_size(intro, 1080, 1920)
    assert filt.count(f"scale={width}:{height}") == 2


def test_the_crop_dimensions_reach_the_filter():
    intro = _intro()

    filt = prepare_filter(intro, 1080, 1920, video_index=2, mask_index=3)
    assert "crop=1000:1140:40:330" in filt


def test_the_filter_references_the_given_input_indices():
    intro = _intro()

    filt = prepare_filter(intro, 1080, 1920, video_index=5, mask_index=6)
    assert "[5:v]" in filt
    assert "[6:v]" in filt


def test_transparency_comes_from_alphamerge_with_the_precomputed_mask():
    # Plus de colorkey : un colorkey supprimerait aussi le noir VOULU a
    # l'interieur de l'anneau (le disque du logo). alphamerge avec le
    # masque precalcule est ce qui le preserve -- voir tools/
    # build_intro_asset.py, qui construit ce masque.
    intro = _intro()

    filt = prepare_filter(intro, 1080, 1920, video_index=2, mask_index=3)
    assert "colorkey" not in filt
    assert "alphamerge" in filt
    assert "format=rgba" in filt
    assert "format=gray" in filt


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

    _, _, enable = overlay_spec(intro, 1080, 1920, video_index=2, mask_index=3)

    assert enable == "between(t,0,6.500)"


def test_overlay_spec_clamps_a_negative_duration_to_zero():
    intro = _intro(duration=-1.0)

    _, _, enable = overlay_spec(intro, 1080, 1920, video_index=2, mask_index=3)

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


def test_the_intro_adds_two_extra_inputs_after_the_main_video():
    # La video ET son masque de transparence, dans cet ordre.
    args = _args(intro=_intro())

    assert args[:4] == ["-ss", "10.000", "-i", "in.mp4"]
    assert args.count("-i") == 3
    assert "intro.mp4" in args
    assert any(a.endswith("intro_follow_mask.mp4") for a in args)


def test_the_intro_overlay_carries_a_time_bounded_enable_expression():
    args = _args(intro=_intro(duration=7.0))
    graph = args[args.index("-filter_complex") + 1]

    assert "overlay=W-w-43:43:enable='between(t,0,7.000)'" in graph


def test_the_intro_is_composited_after_the_watermark_so_it_stays_on_top():
    watermark = Watermark(image="logo.png")
    args = _args(watermark=watermark, intro=_intro())
    graph = args[args.index("-filter_complex") + 1]

    # Le filigrane (entree 1) se compose avant l'intro ("ov0" avant "ov1").
    assert graph.index("[ov0]") < graph.index("[ov1]")
    assert "logo.png" in args and "intro.mp4" in args
    # Le filigrane (calque permanent) ne porte pas de condition enable=.
    assert "enable=" not in graph.split("[ov1]")[0]
    # L'incrustation reference bien ses DEUX entrees propres (video+masque),
    # decalees par l'entree du filigrane qui les precede (0: video
    # principale, 1: filigrane, 2: intro, 3: masque).
    assert "[2:v]" in graph and "[3:v]" in graph


def test_a_permanent_layer_like_the_watermark_keeps_no_enable_expression():
    watermark = Watermark(image="logo.png")
    args = _args(watermark=watermark, intro=None)
    graph = args[args.index("-filter_complex") + 1]

    assert "enable=" not in graph
