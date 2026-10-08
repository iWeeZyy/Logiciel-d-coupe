"""Post video 9:16 : la video de l'article (16:9 centree sur fond flou),
l'info par-dessus en transparence. Detection de la video dans la page,
calque de texte, commande ffmpeg, et un vrai rendu sur une video de 2 s."""
import shutil
import subprocess

import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

from news_story.video_composer import (  # noqa: E402
    H, W, build_overlay, compose_still, compose_video, ffmpeg_args)
from news_story.video_source import find_video_url  # noqa: E402

_ALLOCINE = ('<div data-model="{&quot;videos&quot;:[{&quot;id&quot;:20643416,'
             '&quot;idDailymotion&quot;:&quot;xb5ci0m&quot;,&quot;title&quot;:'
             '&quot;Changer l&#039;eau des fleurs Teaser VF&quot;}]}"></div>')


class TestTrouverLaVideo:
    def test_page_allocine_lecteur_dailymotion(self):
        assert find_video_url("https://www.allocine.fr/article/x.html", _ALLOCINE) == \
            "https://www.dailymotion.com/video/xb5ci0m"

    def test_iframe_youtube(self):
        html = '<iframe src="https://www.youtube.com/embed/AAAAAAAAAAA?rel=0"></iframe>'
        assert find_video_url("https://site.test/a", html) == "https://www.youtube.com/watch?v=AAAAAAAAAAA"

    def test_iframe_dailymotion(self):
        html = '<iframe src="https://www.dailymotion.com/embed/video/x8abcde"></iframe>'
        assert find_video_url("https://site.test/a", html) == "https://www.dailymotion.com/video/x8abcde"

    def test_l_article_est_deja_une_video_youtube(self):
        url = "https://www.youtube.com/watch?v=AAAAAAAAAAA"
        assert find_video_url(url, "") == url

    def test_pas_de_video_pas_d_erreur(self):
        assert find_video_url("https://site.test/a", "<p>Rien</p>") == ""
        assert find_video_url("https://site.test/a", "") == ""


class TestCalque:
    def test_le_texte_est_transparent_a_l_opacite_demandee(self):
        overlay = build_overlay(title="Un film", label="", text_opacity=0.5)
        assert overlay.size == (W, H)
        assert 100 <= overlay.getchannel("A").getextrema()[1] <= 135  # ~50 % de 255

    def test_opaque_a_100(self):
        overlay = build_overlay(title="Un film", label="", text_opacity=1.0)
        assert overlay.getchannel("A").getextrema()[1] == 255

    def test_rien_dans_les_zones_recouvertes_par_tiktok(self):
        overlay = build_overlay(title=" ".join(["Un titre assez long"] * 8), label="ACTUALITÉ",
                                text_opacity=1.0)
        alpha = overlay.getchannel("A")
        assert alpha.crop((0, H - 430, W, H)).getextrema()[1] == 0
        assert alpha.crop((W - 130, 0, W, H)).getextrema()[1] == 0

    def test_apercu_16_9_centre_sur_fond(self, tmp_path):
        src = tmp_path / "src.png"
        Image.new("RGB", (1600, 900), (200, 30, 30)).save(src)
        out = compose_still(src, tmp_path / "p.png", title="", label="")
        img = Image.open(out).convert("RGB")
        assert img.size == (W, H)
        r, g, b = img.getpixel((W // 2, H // 2))
        assert r > 150 and g < 80   # la video (rouge) au centre, entiere
        assert sum(img.getpixel((W // 2, 40))) < sum(img.getpixel((W // 2, H // 2)))  # fond assombri


class TestFfmpeg:
    def test_la_16_9_garde_son_format_et_le_son_est_conserve(self):
        args = ffmpeg_args("in.mp4", "overlay.png", "out.mp4")
        graph = args[args.index("-filter_complex") + 1]
        assert "force_original_aspect_ratio=decrease" in graph   # video entiere, pas recadree
        assert "boxblur" in graph                                 # fond flou
        assert args[args.index("-map", args.index("-map") + 1) + 1] == "0:a?"
        assert args[-1] == "out.mp4"

    @pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg absent")
    def test_vrai_rendu_9_16(self, tmp_path):
        src = tmp_path / "src.mp4"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                        "testsrc=size=640x360:rate=25:duration=2", "-f", "lavfi", "-i",
                        "sine=frequency=440:duration=2", "-shortest", "-c:v", "libx264",
                        "-c:a", "aac", str(src)], check=True)
        out = compose_video(src, tmp_path / "out.mp4", title="Un film", label="ACTUALITÉ")
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height",
                                "-of", "csv=p=0", str(out)], capture_output=True, text=True, check=True)
        assert "video,1080,1920" in probe.stdout
        assert "audio" in probe.stdout
