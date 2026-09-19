"""Recuperation et analyse des flux RSS/Atom du Radar Gaming News.

Tests purs sur parse_feed() avec des flux ecrits a la main -- jamais de
reseau reel (fetch_source() est teste separement, uniquement pour son
comportement de repli sur erreur, via un requests.get monkeypatche).
"""
import pytest

from gaming_news import feed_fetcher, sources
from gaming_news.models import Article, NewsSource

_SOURCE = NewsSource(key="test", label="Test Source", feed_url="https://example.test/feed")

_RSS = """<?xml version="1.0"?>
<rss version="2.0">
  <channel>
    <title>Test Feed</title>
    <item>
      <title>Un nouveau jeu annonce</title>
      <link>https://example.test/article-1</link>
      <pubDate>Mon, 01 Sep 2025 10:00:00 GMT</pubDate>
      <description>&lt;p&gt;Resume avec &lt;img src="https://example.test/img/summary.jpg" /&gt; une image.&lt;/p&gt;</description>
    </item>
    <item>
      <title>Article avec media:content</title>
      <link>https://example.test/article-2</link>
      <media:content xmlns:media="http://search.yahoo.com/mrss/" url="https://example.test/img/media.jpg" />
    </item>
    <item>
      <title>Article avec enclosure</title>
      <link>https://example.test/article-3</link>
      <enclosure url="https://example.test/img/enclosure.jpg" type="image/jpeg" />
    </item>
    <item>
      <title>Article sans lien</title>
    </item>
  </channel>
</rss>
"""

_ATOM = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Test Atom Feed</title>
  <entry>
    <title>Titre Atom</title>
    <link href="https://example.test/atom-article" />
    <published>2025-09-01T10:00:00Z</published>
    <summary>Un resume Atom.</summary>
  </entry>
</feed>
"""

_HTML_ERROR_PAGE = "<html><body><h1>503 Service Unavailable</h1></body></html>"


class TestParseFeedRss:
    def test_extracts_title_link_and_date(self):
        articles = feed_fetcher.parse_feed(_RSS, _SOURCE)
        first = articles[0]
        assert first.title == "Un nouveau jeu annonce"
        assert first.url == "https://example.test/article-1"
        assert first.published_at == "Mon, 01 Sep 2025 10:00:00 GMT"

    def test_extracts_feed_image_from_img_tag_in_summary(self):
        articles = feed_fetcher.parse_feed(_RSS, _SOURCE)
        assert articles[0].feed_image_url == "https://example.test/img/summary.jpg"

    def test_extracts_feed_image_from_media_content(self):
        articles = feed_fetcher.parse_feed(_RSS, _SOURCE)
        assert articles[1].feed_image_url == "https://example.test/img/media.jpg"

    def test_extracts_feed_image_from_enclosure(self):
        articles = feed_fetcher.parse_feed(_RSS, _SOURCE)
        assert articles[2].feed_image_url == "https://example.test/img/enclosure.jpg"

    def test_an_entry_missing_a_link_is_skipped_rather_than_crashing(self):
        articles = feed_fetcher.parse_feed(_RSS, _SOURCE)
        assert all(a.url for a in articles)
        assert len(articles) == 3  # les 4 <item> moins celui sans lien

    def test_source_key_and_label_are_stamped_on_every_article(self):
        articles = feed_fetcher.parse_feed(_RSS, _SOURCE)
        assert all(a.source_key == "test" and a.source_label == "Test Source" for a in articles)

    def test_max_articles_truncates(self):
        articles = feed_fetcher.parse_feed(_RSS, _SOURCE, max_articles=1)
        assert len(articles) == 1


class TestParseFeedAtom:
    def test_title_is_read_with_the_atom_namespace_prefix(self):
        # Bug reel corrige : sans le prefixe "atom:", _text(entry, "title")
        # ne trouve rien puisque toutes les balises filles d'un <entry> Atom
        # portent le namespace Atom.
        articles = feed_fetcher.parse_feed(_ATOM, _SOURCE)
        assert len(articles) == 1
        assert articles[0].title == "Titre Atom"

    def test_link_href_attribute_is_used_as_the_url(self):
        articles = feed_fetcher.parse_feed(_ATOM, _SOURCE)
        assert articles[0].url == "https://example.test/atom-article"

    def test_published_date_is_read(self):
        articles = feed_fetcher.parse_feed(_ATOM, _SOURCE)
        assert articles[0].published_at == "2025-09-01T10:00:00Z"


class TestParseFeedRobustness:
    def test_invalid_xml_returns_an_empty_list_rather_than_raising(self):
        assert feed_fetcher.parse_feed("<not><valid", _SOURCE) == []

    def test_an_html_error_page_returned_instead_of_a_feed_returns_empty(self):
        assert feed_fetcher.parse_feed(_HTML_ERROR_PAGE, _SOURCE) == []

    def test_an_empty_feed_returns_an_empty_list(self):
        empty_rss = '<?xml version="1.0"?><rss version="2.0"><channel></channel></rss>'
        assert feed_fetcher.parse_feed(empty_rss, _SOURCE) == []


class TestFetchSourceNetworkFailures:
    def test_a_network_error_returns_an_empty_list_never_raises(self, monkeypatch):
        import requests

        def _raise(*args, **kwargs):
            raise requests.exceptions.ConnectionError("host injoignable")

        monkeypatch.setattr(requests, "get", _raise)
        assert feed_fetcher.fetch_source(_SOURCE) == []

    def test_an_http_error_status_returns_an_empty_list(self, monkeypatch):
        import requests

        class _FakeResponse:
            def raise_for_status(self):
                raise requests.exceptions.HTTPError("404")

        monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse())
        assert feed_fetcher.fetch_source(_SOURCE) == []

    def test_a_successful_response_is_handed_to_parse_feed(self, monkeypatch):
        import requests

        class _FakeResponse:
            content = _RSS.encode("utf-8")

            def raise_for_status(self):
                pass

        monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse())
        articles = feed_fetcher.fetch_source(_SOURCE)
        assert len(articles) == 3


class TestArticleId:
    def test_is_a_stable_hash_of_the_url(self):
        a = Article(source_key="s", source_label="S", title="T", url="https://example.test/x")
        b = Article(source_key="other", source_label="Other", title="Different", url="https://example.test/x")
        assert a.article_id == b.article_id  # meme URL -> meme identifiant, quoi qu'il change autour

    def test_different_urls_give_different_ids(self):
        a = Article(source_key="s", source_label="S", title="T", url="https://example.test/x")
        b = Article(source_key="s", source_label="S", title="T", url="https://example.test/y")
        assert a.article_id != b.article_id


class TestFetchAllSources:
    def test_aggregates_articles_from_every_source(self, monkeypatch):
        source_a = NewsSource(key="a", label="A", feed_url="https://example.test/a")
        source_b = NewsSource(key="b", label="B", feed_url="https://example.test/b")

        def fake_fetch(source, timeout_s=10, max_articles=20):
            return [Article(source_key=source.key, source_label=source.label,
                            title=f"Article {source.key}", url=f"https://example.test/{source.key}",
                            published_at="Mon, 01 Sep 2025 10:00:00 GMT")]

        monkeypatch.setattr(feed_fetcher, "fetch_source", fake_fetch)
        articles = feed_fetcher.fetch_all_sources([source_a, source_b])
        assert {a.source_key for a in articles} == {"a", "b"}

    def test_sorts_by_publication_date_descending(self, monkeypatch):
        source = NewsSource(key="s", label="S", feed_url="https://example.test/feed")
        older = Article(source_key="s", source_label="S", title="Vieux", url="https://example.test/old",
                        published_at="Mon, 01 Sep 2025 08:00:00 GMT")
        newer = Article(source_key="s", source_label="S", title="Recent", url="https://example.test/new",
                        published_at="Mon, 01 Sep 2025 20:00:00 GMT")
        monkeypatch.setattr(feed_fetcher, "fetch_source", lambda *a, **k: [older, newer])

        articles = feed_fetcher.fetch_all_sources([source])

        assert [a.title for a in articles] == ["Recent", "Vieux"]

    def test_an_unparseable_date_sinks_to_the_bottom_rather_than_crashing(self, monkeypatch):
        source = NewsSource(key="s", label="S", feed_url="https://example.test/feed")
        dated = Article(source_key="s", source_label="S", title="Date", url="https://example.test/d",
                        published_at="Mon, 01 Sep 2025 08:00:00 GMT")
        undated = Article(source_key="s", source_label="S", title="Sans date", url="https://example.test/u",
                          published_at="")
        garbled = Article(source_key="s", source_label="S", title="Illisible", url="https://example.test/g",
                          published_at="pas une date")
        monkeypatch.setattr(feed_fetcher, "fetch_source", lambda *a, **k: [undated, dated, garbled])

        articles = feed_fetcher.fetch_all_sources([source])

        assert articles[0].title == "Date"

    def test_no_sources_returns_an_empty_list(self):
        assert feed_fetcher.fetch_all_sources([]) == []


class TestLoadSources:
    def test_no_config_falls_back_to_defaults(self):
        assert sources.load_sources(None) == list(sources.DEFAULT_SOURCES)

    def test_empty_sources_list_falls_back_to_defaults(self):
        assert sources.load_sources({"sources": []}) == list(sources.DEFAULT_SOURCES)

    def test_a_malformed_entry_is_skipped_but_valid_ones_survive(self):
        config = {"sources": [
            {"key": "ok", "label": "OK", "feed_url": "https://example.test/feed"},
            {"key": "no-feed-url"},
            "not-even-a-dict",
        ]}
        result = sources.load_sources(config)
        assert result == [NewsSource(key="ok", label="OK", feed_url="https://example.test/feed")]

    def test_a_missing_label_falls_back_to_the_key(self):
        config = {"sources": [{"key": "abc", "feed_url": "https://example.test/feed"}]}
        assert sources.load_sources(config)[0].label == "abc"
