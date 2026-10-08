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
    def test_le_texte_est_au_dessus_de_la_video_jamais_dessus(self):
        overlay, (x, y, w, h) = build_overlay(
            title="Leïla Bekhti vient de tourner un film avec cet acteur américain",
            subtitle="Attendue dans Changer l'eau des fleurs, Leïla Bekhti vient de tourner "
                     "avec Jeremy Allen White.", label="ACTUALITÉ", source_size=(1920, 1080))
        assert (w, h) == (1080, 608)                         # 16:9 pleine largeur, format garde
        alpha = overlay.getchannel("A")
        assert alpha.crop((x, y, x + w, y + h)).getextrema()[1] == 0   # rien sur la video
        assert alpha.crop((0, 0, W, y)).getextrema()[1] == 255         # le texte est au-dessus

    def test_opacite_reglable(self):
        overlay, _ = build_overlay(title="Un film", text_opacity=0.5)
        assert 100 <= overlay.getchannel("A").getextrema()[1] <= 135  # ~50 % de 255

    def test_opaque_par_defaut(self):
        overlay, _ = build_overlay(title="Un film")
        assert overlay.getchannel("A").getextrema()[1] == 255

    def test_rien_dans_les_zones_recouvertes_par_tiktok(self):
        overlay, _ = build_overlay(title=" ".join(["Un titre assez long"] * 8), label="ACTUALITÉ",
                                   subtitle="Un chapo. " * 10)
        alpha = overlay.getchannel("A")
        assert alpha.crop((0, H - 430, W, H)).getextrema()[1] == 0
        assert alpha.crop((W - 130, 0, W, H)).getextrema()[1] == 0

    def test_apercu_video_en_bas_texte_en_haut(self, tmp_path):
        src = tmp_path / "src.png"
        Image.new("RGB", (1600, 900), (200, 30, 30)).save(src)
        out = compose_still(src, tmp_path / "p.png", title="", label="")
        img = Image.open(out).convert("RGB")
        assert img.size == (W, H)
        _, (x, y, w, h) = build_overlay(title="", source_size=(1600, 900))
        r, g, b = img.getpixel((W // 2, y + h // 2))
        assert r > 150 and g < 80                          # la video (rouge), entiere
        assert y == (H - h) // 2                           # centree comme un clip quand le texte est court

    def test_titre_en_haut_phrase_et_logo_en_bas(self, tmp_path):
        logo = tmp_path / "logo.png"
        Image.new("RGBA", (200, 200), (255, 0, 255, 255)).save(logo)
        overlay, (x, y, w, h) = build_overlay(
            title="Tempête", label="BANDE-ANNONCE", logo_path=logo, source_size=(1920, 1080),
            cta="N'hésitez pas à me suivre pour plus de contenu cinéma")
        alpha = overlay.getchannel("A")
        assert alpha.crop((x, y, x + w, y + h)).getextrema()[1] == 0          # rien sur la video
        assert alpha.crop((0, 0, W, y)).getextrema()[1] == 255                # titre en haut
        below = overlay.crop((0, y + h, W, H)).convert("RGB")
        colors = below.getcolors(maxcolors=1 << 20)
        assert any(c == (255, 0, 255) for _, c in colors)                     # logo en bas
        # La phrase est au-dessus du logo : du blanc entre la video et le logo.
        bands = overlay.crop((0, y + h, W, H))
        logo_top = min(yy for yy in range(bands.height)
                       if (255, 0, 255, 255) in [bands.getpixel((xx, yy)) for xx in range(380, 700, 20)])
        assert bands.crop((0, 0, W, logo_top)).getchannel("A").getextrema()[1] == 255


class TestFfmpeg:
    def test_la_video_est_posee_a_sa_place_et_le_son_conserve(self):
        args = ffmpeg_args("in.mp4", "overlay.png", "out.mp4", (0, 738, 1080, 608))
        graph = args[args.index("-filter_complex") + 1]
        assert "scale=1080:608" in graph and "overlay=0:738" in graph
        assert "boxblur" in graph                                 # fond flou
        assert args[args.index("-map", args.index("-map") + 1) + 1] == "0:a?"
        assert args[-1] == "out.mp4"

    @pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
                        reason="ffmpeg absent")
    def test_vrai_rendu_9_16(self, tmp_path):
        src = tmp_path / "src.mp4"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                        "testsrc=size=640x360:rate=25:duration=2", "-f", "lavfi", "-i",
                        "sine=frequency=440:duration=2", "-shortest", "-c:v", "libx264",
                        "-c:a", "aac", str(src)], check=True)
        out = compose_video(src, tmp_path / "out.mp4", title="Un film", label="ACTUALITÉ",
                            subtitle="Avec Jeremy Allen White.")
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height",
                                "-of", "csv=p=0", str(out)], capture_output=True, text=True, check=True)
        assert "video,1080,1920" in probe.stdout
        assert "audio" in probe.stdout


class TestChapo:
    def test_le_chapo_est_tire_du_resume(self):
        from news_story.story_composer import default_subtitle

        title = '"Elle est incroyable" : Leïla Bekhti vient de tourner un film avec cet acteur américain'
        summary = ("Attendue dans \"Changer l'eau des fleurs\" le 9 décembre prochain, Leïla Bekhti "
                   "vient de tourner avec Jeremy Allen White qui ne tarit pas d'éloges.")
        assert "Jeremy Allen White" in default_subtitle(title, summary)

    def test_un_resume_qui_repete_le_titre_n_est_pas_repris(self):
        from news_story.story_composer import default_subtitle

        assert default_subtitle("Un film annoncé", "Un film annoncé") == ""

    def test_un_long_resume_est_coupe_sur_une_phrase(self):
        from news_story.post_composer import clean_subtitle

        text = "Première phrase utile. " + "Encore du texte " * 40
        assert clean_subtitle(text, 50) == "Première phrase utile."

    def test_le_post_image_affiche_le_chapo(self, tmp_path):
        from news_story.post_composer import compose_post

        src = tmp_path / "s.png"
        Image.new("RGB", (1080, 1920), (0, 0, 0)).save(src)
        without = compose_post(src, tmp_path / "a.png", title="Un film", label="", vertical=True)
        with_sub = compose_post(src, tmp_path / "b.png", title="Un film", label="", vertical=True,
                                subtitle="Avec Jeremy Allen White, au cinéma le 9 décembre.")

        def white_rows(path):
            img = Image.open(path).convert("L")
            return sum(1 for y in range(img.height)
                       if img.crop((0, y, img.width, y + 1)).getextrema()[1] > 240)
        assert white_rows(with_sub) > white_rows(without)


class TestTitreDuFilm:
    def test_le_nom_du_film_est_tire_du_titre_de_la_video(self):
        from news_story.trailer_title import film_title

        cases = {
            "TEMPÊTE Bande Annonce VF Teaser (2026)": "TEMPÊTE",
            "MALFAISANTE | Nouvelle bande-annonce officielle VOST [Au cinéma le 14 octobre]": "MALFAISANTE",
            "Tempête - Teaser Officiel | Prime Video": "Tempête",
            "GAME MASTER - Official trailer": "GAME MASTER",
            "IL FAUT BRÛLER MAMAN Bande Annonce (2026) Artus": "IL FAUT BRÛLER MAMAN",
            "Spider-Man: Brand New Day - Bande-annonce VF": "Spider-Man: Brand New Day",
            "Bande-annonce": "Bande-annonce",          # rien a garder : titre d'origine
        }
        for video_title, expected in cases.items():
            assert film_title(video_title) == expected, video_title
