"""Concatenation de l'intro devant un clip : construction du graphe ffmpeg.

Pur -- video/intro_concat.py ne fait que construire une liste d'arguments,
verifiable sans encoder quoi que ce soit.
"""
from video.intro_concat import build_concat_args


def _args(**overrides):
    kwargs = dict(
        intro_path="intro.mp4",
        intro_src_size=(1080, 1920),
        clip_path="clip.mp4",
        clip_has_audio=True,
        out_mp4_path="out.mp4",
        target_size=(1080, 1920),
        fps=30.0,
    )
    kwargs.update(overrides)
    return build_concat_args(**kwargs)


def test_both_inputs_are_declared_in_order():
    args = _args()

    assert args[:4] == ["-i", "intro.mp4", "-i", "clip.mp4"]


def test_a_clip_with_audio_concatenates_both_video_and_audio():
    args = _args(clip_has_audio=True)
    graph = args[args.index("-filter_complex") + 1]

    assert "concat=n=2:v=1:a=1[outv][outa]" in graph
    assert args[-1] == "out.mp4"
    assert "-map" in args and "[outv]" in args and "[outa]" in args
    assert "-c:a" in args


def test_a_silent_clip_concatenates_video_only():
    args = _args(clip_has_audio=False)
    graph = args[args.index("-filter_complex") + 1]

    assert "concat=n=2:v=1:a=0[outv]" in graph
    assert "[outa]" not in graph
    assert "-c:a" not in args


def test_both_video_branches_are_normalised_to_the_same_fps():
    args = _args(fps=24.0)
    graph = args[args.index("-filter_complex") + 1]

    assert graph.count("fps=24.0000") == 2


def test_the_intro_is_scaled_identically_when_it_already_matches_the_target():
    # Intro native 1080x1920, sortie 1080x1920 : rien a remplir.
    args = _args(intro_src_size=(1080, 1920), target_size=(1080, 1920))
    graph = args[args.index("-filter_complex") + 1]

    assert "scale=1080:1920" in graph
    assert "gblur" not in graph


def test_the_intro_gets_the_same_blur_fill_as_any_mismatched_source_in_landscape():
    # Intro native 1080x1920, sortie 1920x1080 : il faut remplir, comme pour
    # n'importe quelle source verticale postee en paysage.
    args = _args(intro_src_size=(1080, 1920), target_size=(1920, 1080), fill="flou")
    graph = args[args.index("-filter_complex") + 1]

    assert "gblur" in graph


def test_export_settings_reach_the_encoder_flags():
    args = _args(export_settings={"video_preset": "fast", "video_bitrate_crf": 18,
                                  "audio_bitrate": "128k"})

    assert "fast" in args
    assert "18" in args
    assert "128k" in args
