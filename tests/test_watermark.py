"""Filigrane : lecture de la configuration, filtres, et integration ffmpeg.

Tout est verifiable sans encoder : `video/watermark.py` est pur, et
`build_ffmpeg_args` ne fait que construire une liste d'arguments.
"""
import pytest

from editing.timeline import EditList
from video import watermark as wm
from video.filter_graph import build_ffmpeg_args


@pytest.fixture
def logo(tmp_path):
    """Une image qui existe reellement -- from_config refuse un chemin mort."""
    path = tmp_path / "logo.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    return path


def _args(watermark=None, edit_list=None, **kwargs):
    return build_ffmpeg_args(
        video_path="in.mp4",
        edit_list=edit_list or EditList.identity(10.0, 40.0),
        framing_plan=None, zoom_track=None, src_w=1920, src_h=1080, fps=25.0,
        face_hint=None, ass_path=None, audio_cfg=None,
        export_settings={}, out_mp4_path="out.mp4", watermark=watermark, **kwargs,
    )


# --------------------------------------------------------------- config

def test_a_disabled_watermark_is_nothing_at_all(logo):
    assert wm.from_config({"enabled": False, "image": str(logo)}) is None


def test_no_config_block_at_all_is_a_disabled_watermark():
    assert wm.from_config(None) is None
    assert wm.from_config({}) is None


def test_a_missing_image_disables_the_watermark_instead_of_crashing(tmp_path):
    # Un chemin qui ne pointe sur rien ne doit pas faire echouer un rendu :
    # le clip sort sans logo, ce qui est exactement le comportement d'avant.
    absent = tmp_path / "jamais-livre.png"

    assert wm.from_config({"enabled": True, "image": str(absent)}) is None


def test_an_empty_image_falls_back_to_the_logo_shipped_with_the_app(logo, monkeypatch):
    monkeypatch.setattr(wm, "default_image_path", lambda: logo)

    mark = wm.from_config({"enabled": True, "image": ""})

    assert mark is not None and mark.image == str(logo)


def test_absurd_values_are_clamped_rather_than_obeyed(logo):
    mark = wm.from_config({
        "enabled": True, "image": str(logo),
        "size_percent": 400, "opacity": 12, "margin_percent": 90,
    })

    assert mark.size_percent == wm.MAX_SIZE_PERCENT
    assert mark.opacity == wm.MAX_OPACITY
    assert mark.margin_percent == 20.0

    invisible = wm.from_config({
        "enabled": True, "image": str(logo), "size_percent": 0, "opacity": 0,
    })
    assert invisible.size_percent == wm.MIN_SIZE_PERCENT
    assert invisible.opacity == wm.MIN_OPACITY


def test_an_unknown_position_falls_back_to_the_default_corner(logo):
    mark = wm.from_config({"enabled": True, "image": str(logo), "position": "au-milieu"})

    assert mark.position == wm.DEFAULT_POSITION


# --------------------------------------------------------------- filtres

def test_the_size_is_a_percentage_of_the_output_width_not_a_pixel_count(logo):
    mark = wm.Watermark(image=str(logo), size_percent=10.0)

    assert "scale=108:-1" in wm.prepare_filter(mark, 1080)      # 9:16
    assert "scale=192:-1" in wm.prepare_filter(mark, 1920)      # 16:9


def test_the_scaled_width_is_even(logo):
    # Une largeur impaire fait echouer certains encodeurs sur du yuv420p.
    mark = wm.Watermark(image=str(logo), size_percent=13.0)

    width = int(wm.prepare_filter(mark, 1077).split("scale=")[1].split(":")[0])
    assert width % 2 == 0


def test_the_opacity_multiplies_the_existing_alpha(logo):
    # colorchannelmixer aa= MULTIPLIE l'alpha : le bord adouci du disque est
    # conserve. Remplacer l'alpha carrerait le logo.
    mark = wm.Watermark(image=str(logo), opacity=0.7)

    chain = wm.prepare_filter(mark, 1080)
    assert "format=rgba" in chain
    assert "colorchannelmixer=aa=0.700" in chain


def test_each_position_has_its_own_expression(logo):
    def pos(where):
        return wm.overlay_position(
            wm.Watermark(image=str(logo), position=where, margin_percent=5.0), 1000)

    assert pos("haut-gauche") == "50:50"
    assert pos("haut-centre") == "(W-w)/2:50"
    assert pos("haut-droite") == "W-w-50:50"
    assert pos("bas-gauche") == "50:H-h-50"
    assert pos("bas-centre") == "(W-w)/2:H-h-50"
    assert pos("bas-droite") == "W-w-50:H-h-50"


def test_the_default_place_is_the_bottom_centre(logo):
    # Emplacement demande : la colonne d'icones de TikTok et d'Instagram est a
    # droite, la legende a gauche, les sous-titres plus haut.
    assert wm.overlay_position(wm.Watermark(image=str(logo)), 1080) == "(W-w)/2:H-h-130"


def test_the_centring_is_computed_by_ffmpeg_and_not_by_us(logo):
    # (W-w)/2 reste juste quelle que soit la definition de sortie ; un nombre
    # de pixels calcule ici serait faux des qu'on change de format.
    for width in (1080, 1920, 720):
        assert "(W-w)/2" in wm.overlay_position(wm.Watermark(image=str(logo)), width)


# --------------------------------------------- place reservee aux sous-titres

def test_a_logo_at_the_bottom_reserves_the_band_it_occupies(logo, monkeypatch):
    monkeypatch.setattr(wm, "logo_height_px", lambda mark, out_w: 150)
    mark = wm.Watermark(image=str(logo), position="bas-centre", margin_percent=10.0)

    # marge (100) + hauteur du logo (150) + un ecart (24)
    assert wm.reserved_bottom_px(mark, 1000, gap_px=24) == 274


def test_a_logo_that_is_not_at_the_bottom_reserves_nothing(logo):
    for where in ("haut-gauche", "haut-centre", "haut-droite"):
        mark = wm.Watermark(image=str(logo), position=where)
        assert wm.reserved_bottom_px(mark, 1080) == 0


def test_no_logo_reserves_nothing():
    assert wm.reserved_bottom_px(None, 1080) == 0


def test_the_reserved_band_becomes_the_floor_of_the_smart_subtitles():
    # Le placement intelligent rapproche les sous-titres du bas quand un visage
    # occupe le cadre, et il n'a aucune raison de savoir qu'un logo est pose la.
    from editing.captions import choose_margin_v

    reserve = 305
    # Visage assez bas dans le cadre : le texte passe SOUS lui, donc plus bas
    # que la marge du style -- c'est la seule situation ou il peut tomber sur
    # le logo.
    sans_logo = choose_margin_v(0.62, default_margin_v=380, text_height_px=140,
                                min_margin_v=140)
    avec_logo = choose_margin_v(0.62, default_margin_v=380, text_height_px=140,
                                min_margin_v=max(140, reserve))

    assert sans_logo < reserve
    assert avec_logo >= reserve


# ------------------------------------------------------- integration ffmpeg

def test_without_a_watermark_the_command_is_the_one_from_before():
    assert _args(watermark=None) == _args()
    args = _args()
    assert "-vf" in args and "-filter_complex" not in args
    assert args.count("-i") == 1


def test_a_watermark_forces_filter_complex_even_on_an_untouched_clip(logo):
    args = _args(wm.Watermark(image=str(logo)))

    assert "-vf" not in args and "-filter_complex" in args
    graph = args[args.index("-filter_complex") + 1]
    assert "[base][wm]overlay=" in graph
    assert "[1:v]scale=" in graph
    # L'audio de la source est repris tel quel : le logo ne touche pas au son.
    assert "0:a?" in args


def test_the_watermark_is_added_to_the_montage_graph_too(logo):
    edl = EditList.keeping(10.0, 40.0, removed=[(20.0, 22.0)])

    graph = _args(wm.Watermark(image=str(logo)), edit_list=edl)[
        _args(wm.Watermark(image=str(logo)), edit_list=edl).index("-filter_complex") + 1]

    assert "concat=n=2:v=1:a=1" in graph
    assert "[base][wm]overlay=" in graph
    assert graph.count("overlay=") == 1


def test_the_output_duration_limit_still_applies_with_a_watermark(logo):
    # -t place APRES la derniere entree limite la sortie. Glisse entre les deux
    # entrees, il limiterait la lecture du logo et le clip irait jusqu'au bout
    # de la video source.
    args = _args(wm.Watermark(image=str(logo)))

    assert args.index("-t") > args.index(str(logo))
    assert args[args.index("-t") + 1] == "30.000"


def test_the_logo_is_the_second_input(logo):
    args = _args(wm.Watermark(image=str(logo)))

    inputs = [args[i + 1] for i, a in enumerate(args) if a == "-i"]
    assert inputs == ["in.mp4", str(logo)]


# --------------------------------------------------------------- reglages

def test_the_cli_switch_turns_the_watermark_off_without_touching_the_config():
    from core.config_loader import Settings

    config = {"watermark": {"enabled": True}}

    assert Settings(editing=config).editing_module_enabled("watermark") is True
    assert Settings(editing=config,
                    watermark_enabled=False).editing_module_enabled("watermark") is False


def test_the_config_alone_can_turn_the_watermark_off():
    from core.config_loader import Settings

    assert Settings(editing={"watermark": {"enabled": False}}
                    ).editing_module_enabled("watermark") is False
