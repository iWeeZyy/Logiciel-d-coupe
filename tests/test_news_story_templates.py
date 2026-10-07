"""Les gabarits (Stories IMAGE/NEWS/BREAKING, post 4:5 POST) : declaration pure, pas
de logique de dessin -- verifie seulement CE que chaque gabarit annonce."""
from news_story.story_templates import (
    TEMPLATE_BREAKING,
    TEMPLATE_IMAGE,
    TEMPLATE_NEWS,
    TEMPLATE_POST,
    TEMPLATES,
    get_template,
)


class TestTemplateCatalogue:
    def test_exactly_four_templates_are_defined(self):
        assert len(TEMPLATES) == 4
        assert {t.key for t in TEMPLATES} == {TEMPLATE_IMAGE, TEMPLATE_NEWS, TEMPLATE_BREAKING, TEMPLATE_POST}

    def test_the_post_template_is_a_4_5_feed_post_with_the_title_only(self):
        spec = get_template(TEMPLATE_POST)
        assert spec.canvas_size == (1080, 1350)
        assert spec.show_title is True
        assert spec.title_uses_summary is False

    def test_stories_stay_9_16(self):
        for key in (TEMPLATE_IMAGE, TEMPLATE_NEWS, TEMPLATE_BREAKING):
            assert get_template(key).canvas_size == (1080, 1920)

    def test_the_image_template_shows_no_title_and_no_badge(self):
        spec = get_template(TEMPLATE_IMAGE)
        assert spec.show_title is False
        assert spec.show_badge is False

    def test_the_news_template_shows_a_title_but_no_badge(self):
        spec = get_template(TEMPLATE_NEWS)
        assert spec.show_title is True
        assert spec.show_badge is False

    def test_the_breaking_template_shows_both_a_title_and_a_badge(self):
        spec = get_template(TEMPLATE_BREAKING)
        assert spec.show_title is True
        assert spec.show_badge is True

    def test_breaking_asks_for_a_shorter_title_than_news(self):
        # La spec demande un titre "tres court" pour BREAKING, "court" pour NEWS.
        assert get_template(TEMPLATE_BREAKING).title_max_chars < get_template(TEMPLATE_NEWS).title_max_chars


class TestGetTemplateFallback:
    def test_an_unknown_key_falls_back_to_image_rather_than_raising(self):
        assert get_template("gabarit-invente").key == TEMPLATE_IMAGE

    def test_an_empty_key_falls_back_to_image(self):
        assert get_template("").key == TEMPLATE_IMAGE
