"""Recuperation de l'image reelle d'un article (jamais une image generee) :
og:image -> twitter:image -> image_src -> image de contenu -> image du flux
RSS en dernier repli. Tests purs sur extract_candidates()/parse a partir de
HTML ecrit a la main ; fetch_article_html()/candidates_for_article() sont
testes uniquement pour leur comportement reseau via un requests.get
monkeypatche -- jamais de reseau reel.
"""
from news_story.image_fetcher import (
    ImageCandidate,
    _extract_content_images,
    candidates_for_article,
    extract_candidates,
    fetch_article_html,
)

_PAGE_URL = "https://example.test/articles/un-jeu-annonce"


class TestExtractCandidatesMetaTags:
    def test_finds_og_image(self):
        html = '<html><head><meta property="og:image" content="https://example.test/img/cover.jpg"></head></html>'
        candidates = extract_candidates(html)
        assert candidates == [ImageCandidate(url="https://example.test/img/cover.jpg", source="og:image", priority=0)]

    def test_finds_twitter_image(self):
        html = '<html><head><meta name="twitter:image" content="https://example.test/img/tw.jpg"></head></html>'
        candidates = extract_candidates(html)
        assert candidates[0].source == "twitter:image"

    def test_finds_image_src_link(self):
        html = '<html><head><link rel="image_src" href="https://example.test/img/src.jpg"></head></html>'
        candidates = extract_candidates(html)
        assert candidates[0].source == "image_src"

    def test_og_image_is_prioritized_over_twitter_image(self):
        html = (
            '<html><head>'
            '<meta name="twitter:image" content="https://example.test/img/tw.jpg">'
            '<meta property="og:image" content="https://example.test/img/og.jpg">'
            '</head></html>'
        )
        candidates = extract_candidates(html)
        assert candidates[0].url == "https://example.test/img/og.jpg"
        assert candidates[1].url == "https://example.test/img/tw.jpg"

    def test_no_metadata_returns_an_empty_list(self):
        html = "<html><head><title>Sans image</title></head><body>Rien.</body></html>"
        assert extract_candidates(html) == []

    def test_a_relative_url_is_resolved_against_the_page_url(self):
        html = '<html><head><meta property="og:image" content="/img/relative.jpg"></head></html>'
        candidates = extract_candidates(html, page_url=_PAGE_URL)
        assert candidates[0].url == "https://example.test/img/relative.jpg"

    def test_duplicate_urls_across_tags_are_not_repeated(self):
        html = (
            '<html><head>'
            '<meta property="og:image" content="https://example.test/img/same.jpg">'
            '<meta property="og:image:secure_url" content="https://example.test/img/same.jpg">'
            '</head></html>'
        )
        assert len(extract_candidates(html)) == 1

    def test_malformed_html_does_not_raise(self):
        html = '<html><head><meta property="og:image" content="https://example.test/img/x.jpg"'  # non ferme
        # HTMLParser est tolerant par nature ; l'important est l'absence d'exception.
        extract_candidates(html)


class TestExtractContentImagesFallback:
    def test_picks_up_a_reasonably_sized_body_image(self):
        html = '<body><img src="https://example.test/img/big.jpg" width="800" height="450"></body>'
        candidates = _extract_content_images(html, page_url=_PAGE_URL)
        assert candidates[0].url == "https://example.test/img/big.jpg"
        assert candidates[0].source == "content"

    def test_skips_small_images_like_icons_or_tracking_pixels(self):
        html = '<body><img src="https://example.test/icon.png" width="16" height="16"></body>'
        assert _extract_content_images(html, page_url=_PAGE_URL) == []

    def test_skips_data_uri_images(self):
        html = '<body><img src="data:image/png;base64,AAAA" width="800" height="600"></body>'
        assert _extract_content_images(html, page_url=_PAGE_URL) == []

    def test_never_returns_more_than_the_requested_limit(self):
        imgs = "".join(f'<img src="https://example.test/img/{i}.jpg" width="800" height="600">' for i in range(10))
        html = f"<body>{imgs}</body>"
        assert len(_extract_content_images(html, page_url=_PAGE_URL, limit=3)) == 3


class TestFetchArticleHtmlNetwork:
    def test_a_network_error_returns_an_empty_string_never_raises(self, monkeypatch):
        import requests

        def _raise(*args, **kwargs):
            raise requests.exceptions.ConnectionError("hors ligne")

        monkeypatch.setattr(requests, "get", _raise)
        assert fetch_article_html(_PAGE_URL) == ""

    def test_a_timeout_returns_an_empty_string(self, monkeypatch):
        import requests

        def _raise(*args, **kwargs):
            raise requests.exceptions.Timeout("delai depasse")

        monkeypatch.setattr(requests, "get", _raise)
        assert fetch_article_html(_PAGE_URL) == ""

    def test_an_http_error_status_returns_an_empty_string(self, monkeypatch):
        import requests

        class _FakeResponse:
            encoding = "utf-8"

            def raise_for_status(self):
                raise requests.exceptions.HTTPError("404")

        monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse())
        assert fetch_article_html(_PAGE_URL) == ""

    def test_a_successful_response_returns_its_text(self, monkeypatch):
        import requests

        class _FakeResponse:
            encoding = "utf-8"
            text = "<html>ok</html>"

            def raise_for_status(self):
                pass

        monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse())
        assert fetch_article_html(_PAGE_URL) == "<html>ok</html>"


class TestCandidatesForArticle:
    def test_metadata_candidates_come_before_the_feed_image(self, monkeypatch):
        html = '<html><head><meta property="og:image" content="https://example.test/img/og.jpg"></head></html>'
        monkeypatch.setattr("news_story.image_fetcher.fetch_article_html", lambda *a, **k: html)

        result = candidates_for_article(_PAGE_URL, feed_image_url="https://example.test/img/feed.jpg")

        assert [c.source for c in result] == ["og:image", "feed"]

    def test_the_feed_image_is_not_duplicated_when_it_matches_a_metadata_candidate(self, monkeypatch):
        html = '<html><head><meta property="og:image" content="https://example.test/img/same.jpg"></head></html>'
        monkeypatch.setattr("news_story.image_fetcher.fetch_article_html", lambda *a, **k: html)

        result = candidates_for_article(_PAGE_URL, feed_image_url="https://example.test/img/same.jpg")

        assert len(result) == 1

    def test_falls_back_to_content_images_when_no_metadata_exists(self, monkeypatch):
        html = '<body><img src="https://example.test/img/content.jpg" width="900" height="500"></body>'
        monkeypatch.setattr("news_story.image_fetcher.fetch_article_html", lambda *a, **k: html)

        result = candidates_for_article(_PAGE_URL)

        assert result[0].source == "content"

    def test_an_unreachable_page_still_returns_the_feed_image_as_last_resort(self, monkeypatch):
        monkeypatch.setattr("news_story.image_fetcher.fetch_article_html", lambda *a, **k: "")

        result = candidates_for_article(_PAGE_URL, feed_image_url="https://example.test/img/feed.jpg")

        assert result == [ImageCandidate(url="https://example.test/img/feed.jpg", source="feed", priority=4)]

    def test_nothing_found_anywhere_returns_an_empty_list(self, monkeypatch):
        monkeypatch.setattr("news_story.image_fetcher.fetch_article_html", lambda *a, **k: "")
        assert candidates_for_article(_PAGE_URL) == []
