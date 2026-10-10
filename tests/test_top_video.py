"""Top news cine du jour en video avec voix off (news_story/top_video.py)."""
import shutil
import subprocess
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from news_story import top_video


def _item(title, subtitle=""):
    return SimpleNamespace(article=SimpleNamespace(title=title), subtitle=subtitle)


def test_texte_lisible():
    assert top_video.speakable("N'hésitez pas à me suivre 🎬 « vraiment »…") == \
        "N'hésitez pas à me suivre vraiment ."


def test_plans_dans_l_ordre_du_carrousel():
    shots = top_video.shots_for(Path("c.png"), [Path("1.png"), Path("2.png")],
                                [_item("Un film sort", "Le détail."), _item("Une série")],
                                date(2026, 10, 10), "Suis-moi 🎬")
    assert [s.image.name for s in shots] == ["c.png", "1.png", "2.png"]
    assert shots[0].text == "Top news ciné du 10 octobre 2026. Voici 2 infos à ne pas rater."
    assert shots[1].text == "Numéro 1. Un film sort. Le détail."
    assert shots[2].text == "Numéro 2. Une série. Suis-moi"           # phrase d'appel a la fin


def test_plan_ffmpeg():
    args = top_video.shot_args(Path("i.png"), Path("v.wav"), Path("o.mp4"), 4.0)
    assert args[args.index("-t") + 1] == "4.000"
    graph = args[args.index("-filter_complex") + 1]
    assert "zoompan" in graph and "s=1080x1920" in graph


def test_sans_voix_message_clair(tmp_path):
    with pytest.raises(top_video.TopVideoError, match="voix"):
        top_video.compose_top_video([], tmp_path / "v.mp4", voice=None)


@pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
                    reason="ffmpeg requis")
def test_video_complete(tmp_path, monkeypatch):
    from voice_studio import tts

    def fake_synthesize(text, out_wav, voice=None, rate=1.0, **kwargs):
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                        "sine=frequency=300:duration=1", "-ar", "22050", "-ac", "1", out_wav],
                       check=True)
        return out_wav

    monkeypatch.setattr(tts, "synthesize", fake_synthesize)
    images = []
    for i in range(2):
        path = tmp_path / f"{i}.png"
        Image.new("RGB", (1080, 1920), (40 * i, 60, 90)).save(path)
        images.append(path)
    shots = [top_video.Shot(p, "Bonjour.") for p in images]
    out = top_video.compose_top_video(shots, tmp_path / "top.mp4", voice=SimpleNamespace(id="x"))
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                            "format=duration:stream=width,height", "-of", "csv=p=0", str(out)],
                           capture_output=True, text=True).stdout
    assert "1080,1920" in probe
    duration = float(probe.strip().splitlines()[-1])
    assert 2.6 <= duration <= 3.6          # 2 plans de 1 s de voix + 0,5 s chacun
