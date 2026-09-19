"""Rendu de texte Pillow partage entre thumbnailer.py et
news_story/story_composer.py : chargement de police, retour a la ligne,
reduction de taille jusqu'a tenir dans N lignes.
"""
from PIL import Image, ImageDraw

from video import text_render


def _draw():
    return ImageDraw.Draw(Image.new("RGB", (1080, 1920)))


class TestWrapText:
    def test_short_text_stays_on_one_line(self):
        draw = _draw()
        font = text_render.load_font(40)
        assert text_render.wrap_text(draw, "Un titre court", font, max_width=1000) == ["Un titre court"]

    def test_long_text_wraps_onto_multiple_lines(self):
        draw = _draw()
        font = text_render.load_font(60)
        long_text = "Un titre tres long qui ne peut pas tenir sur une seule ligne etroite"
        lines = text_render.wrap_text(draw, long_text, font, max_width=200)
        assert len(lines) > 1
        # Aucun mot ne doit etre perdu au passage a la ligne.
        assert " ".join(lines).split() == long_text.split()

    def test_never_splits_a_word_across_lines(self):
        draw = _draw()
        font = text_render.load_font(60)
        lines = text_render.wrap_text(draw, "Anticonstitutionnellement", font, max_width=50)
        assert lines == ["Anticonstitutionnellement"]  # trop long pour tenir, mais jamais coupe


class TestFitFontForLines:
    def test_short_text_keeps_the_maximum_size(self):
        draw = _draw()
        font, lines, size = text_render.fit_font_for_lines(
            draw, "Court", max_width=1000, max_size=96, min_size=44, max_lines=2,
        )
        assert size == 96
        assert lines == ["Court"]

    def test_a_very_long_text_is_reduced_until_it_fits_or_hits_the_floor(self):
        draw = _draw()
        long_text = "Un titre extremement long qui ne rentrera jamais en deux lignes courtes"
        font, lines, size = text_render.fit_font_for_lines(
            draw, long_text, max_width=300, max_size=96, min_size=44, max_lines=2,
        )
        assert size == 44  # taille plancher atteinte
        assert len(lines) <= 2  # jamais plus que le nombre de lignes demande, meme si ca deborde

    def test_returned_size_matches_the_font_actually_used(self):
        draw = _draw()
        font, lines, size = text_render.fit_font_for_lines(
            draw, "Texte", max_width=1000, max_size=80, min_size=40, max_lines=2,
        )
        # La taille renvoyee doit correspondre a la police effectivement choisie,
        # jamais une valeur devinee separement (bug potentiel si les deux divergent).
        if hasattr(font, "size"):
            assert font.size == size


class TestDrawOutlinedText:
    def test_draws_without_raising_and_leaves_the_image_non_blank(self):
        image = Image.new("RGB", (1080, 1920), color=(0, 0, 0))
        draw = ImageDraw.Draw(image)
        font, lines, size = text_render.fit_font_for_lines(
            draw, "Un titre", max_width=900, max_size=80, min_size=40, max_lines=2,
        )
        text_render.draw_outlined_text(
            draw, x_center=540, y_center=960, lines=lines, font=font, size=size,
            fill=(255, 255, 255), stroke_fill=(0, 0, 0),
        )
        assert image.getextrema() != ((0, 0), (0, 0), (0, 0))  # quelque chose a ete dessine
