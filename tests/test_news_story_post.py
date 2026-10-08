"""Post de fil 4:5 (gabarit POST) et sa legende, plus le rangement des
sources par theme (gaming / cinema). Images synthetiques, aucun reseau."""
from pathlib import Path

import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

from news_story.caption import build_caption  # noqa: E402
from news_story.post_composer import (  # noqa: E402
    POST_H, POST_W, VERTICAL_H, VERTICAL_W, compose_post)
from news_story.story_composer import StoryOptions, compose_story  # noqa: E402
from news_story.story_templates import TEMPLATE_POST, TEMPLATE_POST_VERTICAL  # noqa: E402


def _source(tmp_path: Path, size=(1600, 900), color=(70, 110, 160)) -> Path:
    path = tmp_path / "src.png"
    Image.new("RGB", size, color).save(path)
    return path


def _logo(tmp_path: Path) -> Path:
    path = tmp_path / "logo.png"
    img = Image.new("RGBA", (400, 120), (0, 0, 0, 0))
    img.paste((255, 0, 255, 255), (0, 0, 400, 120))
    img.save(path)
    return path


class TestPost:
    def test_toujours_1080x1350_quelle_que_soit_la_source(self, tmp_path):
        for size in ((1600, 900), (800, 1600), (500, 500)):
            src = _source(tmp_path, size)
            out = compose_post(src, tmp_path / "post.png", title="Un titre")
            assert Image.open(out).size == (POST_W, POST_H)

    def test_le_bas_est_assombri_par_le_degrade_et_le_haut_intact(self, tmp_path):
        src = _source(tmp_path, color=(200, 200, 200))
        out = compose_post(src, tmp_path / "post.png", title="", label="")
        img = Image.open(out).convert("RGB")
        assert img.getpixel((10, 10)) == (200, 200, 200)
        assert sum(img.getpixel((10, POST_H - 5))) < 120

    def test_le_titre_blanc_est_dessine_dans_la_moitie_basse(self, tmp_path):
        src = _source(tmp_path, color=(0, 0, 0))
        out = compose_post(src, tmp_path / "post.png", title="Warner Bros et Paramount fusionnent",
                           label="")
        img = Image.open(out).convert("L")
        top = img.crop((0, 0, POST_W, POST_H // 2))
        bottom = img.crop((0, POST_H // 2, POST_W, POST_H))
        assert top.getextrema()[1] < 40
        assert bottom.getextrema()[1] > 240

    def test_le_logo_est_pose_en_bas_au_centre(self, tmp_path):
        src = _source(tmp_path, color=(0, 0, 0))
        out = compose_post(src, tmp_path / "post.png", title="", label="", logo_path=_logo(tmp_path))
        img = Image.open(out).convert("RGB")
        assert img.getpixel((POST_W // 2, POST_H - 60)) == (255, 0, 255)

    def test_un_logo_absent_ou_illisible_ne_fait_pas_echouer(self, tmp_path):
        src = _source(tmp_path)
        bad = tmp_path / "pas_une_image.png"
        bad.write_text("rien")
        for logo in (tmp_path / "absent.png", bad):
            assert compose_post(src, tmp_path / "post.png", title="Titre", logo_path=logo).is_file()

    def test_export_jpeg(self, tmp_path):
        out = compose_post(_source(tmp_path), tmp_path / "post.jpg", title="Titre", output_format="JPEG")
        assert Image.open(out).format == "JPEG"


class TestPostVertical:
    """Le meme post en 9:16 pour TikTok / Reels / Story : le texte et le
    logo restent hors de la zone que l'interface de ces applications
    recouvre (bas de l'ecran, colonne de boutons a droite)."""

    def test_toujours_1080x1920(self, tmp_path):
        for size in ((1600, 900), (800, 1600)):
            out = compose_post(_source(tmp_path, size), tmp_path / "v.png", title="Un titre", vertical=True)
            assert Image.open(out).size == (VERTICAL_W, VERTICAL_H)

    def test_rien_dans_la_zone_recouverte_par_tiktok(self, tmp_path):
        src = _source(tmp_path, color=(0, 0, 0))
        out = compose_post(src, tmp_path / "v.png", vertical=True, logo_path=_logo(tmp_path),
                           title="Un titre assez long pour occuper toute la largeur disponible du bloc")
        img = Image.open(out).convert("L")
        assert img.crop((0, VERTICAL_H - 420, VERTICAL_W, VERTICAL_H)).getextrema()[1] < 40
        assert img.crop((VERTICAL_W - 120, 0, VERTICAL_W, VERTICAL_H)).getextrema()[1] < 40
        assert img.crop((0, VERTICAL_H // 2, VERTICAL_W, VERTICAL_H - 420)).getextrema()[1] > 240

    def test_le_gabarit_9_16_passe_par_compose_post(self, tmp_path):
        out = compose_story(_source(tmp_path), tmp_path / "v.png",
                            StoryOptions(template=TEMPLATE_POST_VERTICAL, title="Un film annonce",
                                         summary="Le resume va dans la legende."))
        assert Image.open(out).size == (1080, 1920)


class TestPostViaComposeStory:
    def test_le_gabarit_post_produit_un_4_5(self, tmp_path):
        out = compose_story(_source(tmp_path), tmp_path / "p.png",
                            StoryOptions(template=TEMPLATE_POST, title="Un film annonce",
                                         summary="Un long resume qui ne doit jamais etre sur l'image."))
        assert Image.open(out).size == (1080, 1350)

    def test_le_resume_n_est_jamais_sur_l_image(self, tmp_path, monkeypatch):
        seen = {}

        def fake_compose_post(image_path, out_path, **kwargs):
            seen.update(kwargs)
            return out_path

        import news_story.post_composer as post_composer
        monkeypatch.setattr(post_composer, "compose_post", fake_compose_post)
        compose_story(_source(tmp_path), tmp_path / "p.png",
                      StoryOptions(template=TEMPLATE_POST, title="Titre court", summary="Le detail."))
        assert seen["title"] == "Titre court"

    def test_une_rumeur_passe_dans_l_etiquette(self, tmp_path, monkeypatch):
        seen = {}

        def fake_compose_post(image_path, out_path, **kwargs):
            seen.update(kwargs)
            return out_path

        import news_story.post_composer as post_composer
        monkeypatch.setattr(post_composer, "compose_post", fake_compose_post)
        compose_story(_source(tmp_path), tmp_path / "p.png",
                      StoryOptions(template=TEMPLATE_POST, title="Rumeur : un reboot serait en preparation"))
        assert seen["label"] == "RUMEUR"
        assert not seen["title"].upper().startswith("RUMEUR")


class TestLegende:
    def test_titre_resume_et_source_dans_l_ordre(self):
        caption = build_caption("Warner et Paramount fusionnent", "<p>La fusion est <b>finalisée</b>.</p>",
                                "AlloCiné", theme="cinema")
        assert caption == ("🎬 Warner et Paramount fusionnent\n\nLa fusion est finalisée.\n\n"
                           "Source : AlloCiné")

    def test_un_resume_qui_repete_le_titre_n_est_pas_duplique(self):
        caption = build_caption("Un titre", "Un titre", "VGC", theme="gaming")
        assert caption.count("Un titre") == 1

    def test_jamais_de_texte_invente(self):
        caption = build_caption("Titre exact", "Résumé exact.", "Source X", theme="")
        assert caption == "Titre exact\n\nRésumé exact.\n\nSource : Source X"

    def test_un_resume_trop_long_est_coupe_sur_un_mot(self):
        caption = build_caption("T", "mot " * 1000, "")
        assert len(caption) < 2200 and caption.endswith("…")


class TestThemes:
    def test_les_sources_cinema_par_defaut(self):
        from gaming_news import sources

        cinema = [s.key for s in sources.DEFAULT_SOURCES if s.theme == "cinema"]
        assert {"allocine", "ecranlarge", "cinechronicle", "numerama_cinema"} <= set(cinema)

    def test_le_theme_est_lu_depuis_la_config_et_retombe_sur_gaming(self):
        from gaming_news import sources

        config = {"sources": [
            {"key": "a", "feed_url": "https://x.test/a", "theme": "cinema"},
            {"key": "b", "feed_url": "https://x.test/b"},
            {"key": "c", "feed_url": "https://x.test/c", "theme": "inconnu"},
        ]}
        assert [s.theme for s in sources.load_sources(config)] == ["cinema", "gaming", "gaming"]

    def test_la_config_livree_contient_le_fil_cinema(self):
        import json

        from gaming_news import sources

        root = Path(__file__).resolve().parent.parent
        config = json.loads((root / "config" / "gaming_news.json").read_text(encoding="utf-8"))
        assert any(s.theme == "cinema" for s in sources.load_sources(config))


class TestTypographie:
    def test_un_point_d_interrogation_ne_finit_jamais_seul_sur_une_ligne(self):
        from PIL import ImageDraw

        from news_story.post_composer import _font, _french_spacing, _wrap

        draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
        title = _french_spacing("QUI A VRAIMENT DOMINÉ LES VENTES EN 2026 ?")
        for width in range(200, 1000, 20):
            lines = _wrap(draw, title, _font(70), width)
            assert all(line.strip() != "?" for line in lines)
