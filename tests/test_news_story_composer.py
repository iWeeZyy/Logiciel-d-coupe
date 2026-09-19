"""Composition de la Story 1080x1920 : recadrage 9:16 (avec/sans visage
detecte), les trois gabarits, le titre/la source editables, le logo optionnel,
les deux formats d'export. Le vrai detecteur de visage (telechargement de
modele, reseau) est monkeypatche par defaut dans tout ce fichier -- seule la
classe TestFaceAwareCrop simule ses propres visages pour verifier que le
recadrage en tient compte ; les autres tests n'ont besoin que d'un resultat
rapide et previsible.
"""
import pytest
from PIL import Image

from news_story import story_composer
from news_story.story_composer import StoryOptions, compose_story
from news_story.story_templates import TEMPLATE_BREAKING, TEMPLATE_IMAGE, TEMPLATE_NEWS
from video.face_detector import FaceBox


@pytest.fixture(autouse=True)
def _no_real_face_detection(monkeypatch):
    monkeypatch.setattr(story_composer, "detect_faces_in_image", lambda *a, **k: [])


def _solid(path, size, color):
    Image.new("RGB", size, color).save(path, format="JPEG", quality=95)
    return path


def _two_tone_landscape(path, size=(2000, 800)):
    """Moitie gauche rouge, moitie droite bleue -- permet de verifier QUELLE
    moitie le recadrage a retenue sans avoir besoin d'inspecter du texte."""
    img = Image.new("RGB", size)
    px = img.load()
    mid = size[0] // 2
    for x in range(size[0]):
        color = (255, 0, 0) if x < mid else (0, 0, 255)
        for y in range(size[1]):
            px[x, y] = color
    img.save(path, format="JPEG", quality=95)
    return path


def _is_mostly_red(image: Image.Image) -> bool:
    samples = [image.getpixel((x, image.height // 2)) for x in range(0, image.width, 30)]
    red_like = sum(1 for r, g, b in samples if r > 150 and b < 100)
    return red_like / len(samples) > 0.85


class TestOutputSize:
    def test_a_landscape_source_is_cropped_to_the_target_size(self, tmp_path):
        src = _solid(tmp_path / "src.jpg", (1920, 1080), (20, 20, 20))
        out = tmp_path / "out.png"
        compose_story(src, out, StoryOptions(template=TEMPLATE_IMAGE, source_label="VGC"))
        with Image.open(out) as result:
            assert result.size == (1080, 1920)

    def test_a_portrait_source_is_resized_to_the_target_size(self, tmp_path):
        src = _solid(tmp_path / "src.jpg", (600, 1000), (20, 20, 20))
        out = tmp_path / "out.png"
        compose_story(src, out, StoryOptions(template=TEMPLATE_IMAGE, source_label="VGC"))
        with Image.open(out) as result:
            assert result.size == (1080, 1920)

    def test_a_square_source_is_cropped_to_the_target_size(self, tmp_path):
        src = _solid(tmp_path / "src.jpg", (800, 800), (20, 20, 20))
        out = tmp_path / "out.png"
        compose_story(src, out, StoryOptions(template=TEMPLATE_IMAGE, source_label="VGC"))
        with Image.open(out) as result:
            assert result.size == (1080, 1920)

    def test_a_low_resolution_source_is_upscaled_rather_than_refused(self, tmp_path):
        src = _solid(tmp_path / "tiny.jpg", (120, 90), (20, 20, 20))
        out = tmp_path / "out.png"
        # Ne doit jamais lever : la decision d'avertir sur une resolution
        # insuffisante appartient a l'appelant (image_cache.CachedImage),
        # pas au compositeur.
        compose_story(src, out, StoryOptions(template=TEMPLATE_IMAGE, source_label="VGC"))
        with Image.open(out) as result:
            assert result.size == (1080, 1920)


class TestFaceAwareCrop:
    def test_a_detected_face_pulls_the_crop_toward_it(self, monkeypatch, tmp_path):
        face = FaceBox(x=0.02, y=0.4, w=0.06, h=0.2, confidence=0.95)
        monkeypatch.setattr(story_composer, "detect_faces_in_image", lambda *a, **k: [face])

        src = _two_tone_landscape(tmp_path / "src.jpg")
        out = tmp_path / "out.png"
        compose_story(src, out, StoryOptions(template=TEMPLATE_IMAGE, source_label=""))

        with Image.open(out) as result:
            assert _is_mostly_red(result)

    def test_without_a_detected_face_the_crop_stays_centered(self, tmp_path):
        # Fixture par defaut : aucun visage detecte -- le crop centre couvre
        # la frontiere rouge/bleu, donc jamais majoritairement rouge.
        src = _two_tone_landscape(tmp_path / "src.jpg")
        out = tmp_path / "out.png"
        compose_story(src, out, StoryOptions(template=TEMPLATE_IMAGE, source_label=""))

        with Image.open(out) as result:
            assert not _is_mostly_red(result)

    def test_several_faces_are_averaged_rather_than_only_the_first(self, monkeypatch, tmp_path):
        # Deux visages, l'un tres a gauche l'autre tres a droite : leur
        # moyenne ponderee doit rester proche du centre, jamais basculer
        # entièrement sur l'un des deux.
        left = FaceBox(x=0.02, y=0.4, w=0.06, h=0.2, confidence=0.9)
        right = FaceBox(x=0.92, y=0.4, w=0.06, h=0.2, confidence=0.9)
        monkeypatch.setattr(story_composer, "detect_faces_in_image", lambda *a, **k: [left, right])

        src = _two_tone_landscape(tmp_path / "src.jpg")
        out = tmp_path / "out.png"
        compose_story(src, out, StoryOptions(template=TEMPLATE_IMAGE, source_label=""))

        with Image.open(out) as result:
            assert not _is_mostly_red(result)


class TestTemplates:
    def test_image_template_draws_only_the_source_line(self, monkeypatch, tmp_path):
        calls = []
        monkeypatch.setattr(story_composer, "draw_outlined_text",
                            lambda draw, x, y, lines, *a, **k: calls.append(list(lines)))
        src = _solid(tmp_path / "src.jpg", (1920, 1080), (20, 20, 20))
        compose_story(src, tmp_path / "out.png",
                     StoryOptions(template=TEMPLATE_IMAGE, title="Jamais affiche", source_label="VGC"))
        assert len(calls) == 1
        assert "Source : VGC" in calls[0][0]

    def test_news_template_draws_a_title_and_the_source(self, monkeypatch, tmp_path):
        calls = []
        monkeypatch.setattr(story_composer, "draw_outlined_text",
                            lambda draw, x, y, lines, *a, **k: calls.append(list(lines)))
        src = _solid(tmp_path / "src.jpg", (1920, 1080), (20, 20, 20))
        compose_story(src, tmp_path / "out.png",
                     StoryOptions(template=TEMPLATE_NEWS, title="Un jeu est annonce", source_label="VGC"))
        assert len(calls) == 2  # le titre, puis la source

    def test_a_template_with_no_source_label_draws_no_source_line(self, tmp_path):
        src = _solid(tmp_path / "src.jpg", (1920, 1080), (20, 20, 20))
        out = tmp_path / "out.png"
        compose_story(src, out, StoryOptions(template=TEMPLATE_IMAGE, source_label=""))
        with Image.open(out) as result:
            # Sans source ni titre, le bas de l'image reste la couleur unie
            # d'origine -- aucun bandeau sombre n'a ete dessine.
            assert result.getpixel((540, 1900)) == (20, 20, 20)

    def test_breaking_template_draws_the_badge(self, tmp_path):
        src = _solid(tmp_path / "src.jpg", (1920, 1080), (20, 20, 20))
        out = tmp_path / "out.png"
        compose_story(src, out, StoryOptions(template=TEMPLATE_BREAKING, title="Un evenement", source_label="VGC"))
        with Image.open(out) as result:
            assert result.getpixel((60, 60)) == story_composer._BADGE_COLOR

    def test_image_and_news_templates_never_draw_the_badge(self, tmp_path):
        for template in (TEMPLATE_IMAGE, TEMPLATE_NEWS):
            src = _solid(tmp_path / f"src_{template}.jpg", (1920, 1080), (20, 20, 20))
            out = tmp_path / f"out_{template}.png"
            compose_story(src, out, StoryOptions(template=template, title="Un titre", source_label="VGC"))
            with Image.open(out) as result:
                assert result.getpixel((60, 60)) != story_composer._BADGE_COLOR


class TestManualOverrides:
    def test_title_override_is_used_verbatim_instead_of_auto_shortening(self, monkeypatch, tmp_path):
        calls = []
        monkeypatch.setattr(story_composer, "draw_outlined_text",
                            lambda draw, x, y, lines, *a, **k: calls.append(list(lines)))
        src = _solid(tmp_path / "src.jpg", (1920, 1080), (20, 20, 20))
        compose_story(src, tmp_path / "out.png", StoryOptions(
            template=TEMPLATE_NEWS, title="Titre original ignore", title_override="Titre edite a la main",
            source_label="VGC",
        ))
        drawn_texts = [" ".join(lines) for lines in calls]
        assert any("Titre edite a la main" in t for t in drawn_texts)
        assert not any("Titre original ignore" in t for t in drawn_texts)

    def test_source_override_replaces_the_detected_source_label(self, monkeypatch, tmp_path):
        calls = []
        monkeypatch.setattr(story_composer, "draw_outlined_text",
                            lambda draw, x, y, lines, *a, **k: calls.append(list(lines)))
        src = _solid(tmp_path / "src.jpg", (1920, 1080), (20, 20, 20))
        compose_story(src, tmp_path / "out.png", StoryOptions(
            template=TEMPLATE_IMAGE, source_label="VGC", source_override="Source personnalisee",
        ))
        drawn_texts = [" ".join(lines) for lines in calls]
        assert any("Source personnalisee" in t for t in drawn_texts)

    def test_a_rumor_title_is_clearly_labeled_in_the_drawn_text(self, monkeypatch, tmp_path):
        calls = []
        monkeypatch.setattr(story_composer, "draw_outlined_text",
                            lambda draw, x, y, lines, *a, **k: calls.append(list(lines)))
        src = _solid(tmp_path / "src.jpg", (1920, 1080), (20, 20, 20))
        compose_story(src, tmp_path / "out.png", StoryOptions(
            template=TEMPLATE_NEWS, title="Rumeur : le studio fermerait", source_label="VGC",
        ))
        drawn_texts = " ".join(" ".join(lines) for lines in calls)
        assert "RUMEUR" in drawn_texts


def _top_right_region_differs_from_background(image: Image.Image, background: tuple) -> bool:
    """Vrai si au moins un pixel de la zone haut-droite (celle du logo) ne
    correspond plus au fond uni d'origine -- une sonde sur un pixel unique
    tomberait parfois sur un coin anti-aliase transparent du logo et donnerait
    un faux negatif."""
    for x in range(900, 1080, 4):
        for y in range(10, 200, 4):
            if image.getpixel((x, y)) != background:
                return True
    return False


class TestBranding:
    def test_branding_disabled_leaves_the_top_right_corner_untouched(self, tmp_path):
        background = (20, 20, 20)
        src = _solid(tmp_path / "src.jpg", (1920, 1080), background)
        out = tmp_path / "out.png"
        compose_story(src, out, StoryOptions(template=TEMPLATE_IMAGE, source_label="VGC", branding_enabled=False))
        with Image.open(out) as result:
            assert not _top_right_region_differs_from_background(result, background)

    def test_branding_enabled_changes_the_top_right_corner(self, tmp_path):
        background = (20, 20, 20)
        src = _solid(tmp_path / "src.jpg", (1920, 1080), background)
        out = tmp_path / "out.png"
        compose_story(src, out, StoryOptions(template=TEMPLATE_IMAGE, source_label="VGC", branding_enabled=True))
        with Image.open(out) as result:
            assert _top_right_region_differs_from_background(result, background)

    def test_a_missing_logo_file_does_not_break_the_export(self, monkeypatch, tmp_path):
        from pathlib import Path

        monkeypatch.setattr("video.watermark.default_image_path", lambda: Path("/introuvable/logo.png"))
        src = _solid(tmp_path / "src.jpg", (1920, 1080), (20, 20, 20))
        out = tmp_path / "out.png"
        compose_story(src, out, StoryOptions(template=TEMPLATE_IMAGE, source_label="VGC", branding_enabled=True))
        assert out.is_file()


class TestExportFormats:
    def test_png_export_produces_a_readable_png(self, tmp_path):
        src = _solid(tmp_path / "src.jpg", (1920, 1080), (20, 20, 20))
        out = tmp_path / "out.png"
        compose_story(src, out, StoryOptions(template=TEMPLATE_IMAGE, source_label="VGC", output_format="PNG"))
        with Image.open(out) as result:
            assert result.format == "PNG"

    def test_jpeg_export_produces_a_readable_jpeg(self, tmp_path):
        src = _solid(tmp_path / "src.jpg", (1920, 1080), (20, 20, 20))
        out = tmp_path / "out.jpg"
        compose_story(src, out, StoryOptions(template=TEMPLATE_IMAGE, source_label="VGC", output_format="JPEG"))
        with Image.open(out) as result:
            assert result.format == "JPEG"


class TestErrorHandling:
    def test_a_missing_source_image_raises_rather_than_producing_a_blank_story(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            compose_story(tmp_path / "introuvable.jpg", tmp_path / "out.png",
                         StoryOptions(template=TEMPLATE_IMAGE, source_label="VGC"))
