"""Phrase d'accroche sous le medaillon de l'intro ClipsOfStreams."""
from PIL import Image

from video.intro import DEFAULT_TEXT, from_config
from video.intro_overlay import IntroOverlay, overlay_position, overlay_rect, text_overlay_spec
from video.intro_text import layout, render_intro_text


def _intro(**kw):
    params = dict(path="intro.mp4", src_w=1080, src_h=1920, duration=7.0)
    params.update(kw)
    return IntroOverlay(**params)


def test_phrase_par_defaut_et_reglable():
    assert DEFAULT_TEXT == "Les clips que tu n'as pas vus en live"
    assert from_config({"enabled": True}).text == DEFAULT_TEXT
    assert from_config({"enabled": True, "text": "  Autre   phrase "}).text == "Autre phrase"
    assert from_config({"enabled": True, "text": ""}).text == ""


def test_la_config_livree_porte_la_phrase():
    import json

    from core.paths import app_base_dir

    config = json.loads((app_base_dir() / "config" / "editing.json").read_text(encoding="utf-8"))
    assert config["intro"]["text"] == DEFAULT_TEXT


def test_rectangle_aligne_sur_la_position_ffmpeg():
    intro = _intro()
    x, y, w, h = overlay_rect(intro, 1080, 1920)
    assert (x, y, w) == (1080 - 378 - 43, 43, 378)
    assert overlay_position(intro, 1080, 1920) == f"W-w-43:43"


def test_sous_le_medaillon_ou_au_dessus_en_bas_d_ecran():
    _, top = layout((659, 43, 378, 430), 80)
    assert top > 43 + 300                                     # sous le dessin
    _, top = layout((659, 1440, 378, 430), 80, above=True)
    assert top + 80 < 1440 + 70                               # au-dessus, en bas d'ecran


def test_calque_en_fondu_sur_la_duree_de_l_intro():
    inputs, filt, position, enable = text_overlay_spec(_intro(text_png="t.png"))
    assert inputs[-1] == "t.png" and "-loop" in inputs
    assert "fade=t=out:st=6.600" in filt and enable == "between(t,0,7.000)"


def test_le_compositeur_pose_la_phrase_apres_le_medaillon():
    from editing.timeline import EditList
    from video.filter_graph import build_ffmpeg_args

    args = build_ffmpeg_args(
        video_path="in.mp4", edit_list=EditList.identity(0.0, 10.0), framing_plan=None,
        zoom_track=None, src_w=1080, src_h=1920, fps=30.0, face_hint=None, ass_path=None,
        audio_cfg=None, export_settings={}, out_mp4_path="out.mp4",
        intro=_intro(text_png="t.png"))
    assert args.count("-i") == 4                               # clip, intro, masque, phrase
    graph = args[args.index("-filter_complex") + 1]
    assert "[3:v]format=rgba,fade=t=in" in graph


def test_dessin_de_la_phrase(tmp_path):
    out = render_intro_text(_intro(), DEFAULT_TEXT, 1080, 1920, tmp_path / "t.png")
    with Image.open(out) as image:
        assert image.size == (1080, 1920)
        box = image.getchannel("A").getbbox()
    assert box is not None and box[1] > 43 + 300 and box[3] < 700   # sous le medaillon
    assert box[0] >= 0 and box[2] <= 1080
