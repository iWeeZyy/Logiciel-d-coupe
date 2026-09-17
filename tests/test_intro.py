"""Video d'intro : lecture de la configuration.

Tout est verifiable sans encoder -- video/intro.py est pur, comme
video/watermark.py.
"""
import pytest

from video import intro as vi


@pytest.fixture
def clip(tmp_path):
    """Un fichier qui existe reellement -- from_config refuse un chemin mort."""
    path = tmp_path / "intro.mp4"
    path.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"0" * 64)
    return path


def test_disabled_gives_none():
    assert vi.from_config({"enabled": False}) is None
    assert vi.from_config(None) is None
    assert vi.from_config({}) is None


def test_enabled_with_no_video_falls_back_to_the_default_asset():
    # Le defaut (assets/branding/intro_follow.mp4) est livre avec le depot :
    # from_config doit le trouver sans qu'on precise 'video'.
    result = vi.from_config({"enabled": True})

    assert result is not None
    assert result.video.endswith("intro_follow.mp4")
    assert result.exists


def test_enabled_with_a_real_video_uses_it(clip):
    result = vi.from_config({"enabled": True, "video": str(clip)})

    assert result is not None
    assert result.video == str(clip)
    assert result.exists


def test_enabled_with_a_missing_video_gives_none(tmp_path):
    missing = tmp_path / "does-not-exist.mp4"

    assert vi.from_config({"enabled": True, "video": str(missing)}) is None


def test_exists_is_false_for_a_dead_path():
    assert vi.Intro(video="/does/not/exist.mp4").exists is False


def test_exists_is_false_for_an_empty_path():
    assert vi.Intro(video="").exists is False
