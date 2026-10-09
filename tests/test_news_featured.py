"""Articles « a la une » en tete du fil, et Top bandes-annonces AlloCine.
Pages et flux ecrits a la main, aucun reseau."""
import base64

from gaming_news.featured import (
    decode_allocine_link, page_links, parse_allocine_trailers, rank_featured, url_key)
from gaming_news.feed_fetcher import sort_articles
from gaming_news.models import Article, NewsSource


def _art(url, title, date="", key="site", rank=None):
    return Article(source_key=key, source_label=key, title=title, url=url, published_at=date, rank=rank)


def _obfuscate(url: str) -> str:
    raw = base64.b64encode(url.encode()).decode()
    return raw[:4] + "ACr" + raw[4:]


class TestALaUne:
    def test_lien_allocine_masque(self):
        url = "https://www.allocine.fr/article/fichearticle_gen_carticle=1.html"
        assert decode_allocine_link(_obfuscate(url)) == url

    def test_les_liens_de_la_page_dans_l_ordre(self):
        page = (f'<a href="/b.html">B</a><span class="ACr{_obfuscate("https://site.test/a.html")} x">A</span>'
                '<a href="/b.html">B encore</a>')
        assert page_links(page, "https://site.test/") == ["https://site.test/b.html", "https://site.test/a.html"]

    def test_rang_selon_l_ordre_de_la_page(self):
        articles = [_art("https://www.site.test/1", "Un"), _art("https://site.test/2/", "Deux"),
                    _art("https://site.test/3", "Trois")]
        page = '<a href="/3">x</a><a href="/rubrique">y</a><a href="https://site.test/2?utm=x">z</a>'
        ranked = rank_featured(articles, page, "https://site.test/")
        assert [a.rank for a in ranked] == [None, 1, 0]

    def test_les_unes_d_abord_puis_par_date(self):
        articles = [
            _art("u1", "recent", "2026-10-09T10:00:00+00:00"),
            _art("u2", "une site A n°2", "2026-10-01T10:00:00+00:00", key="a", rank=1),
            _art("u3", "une site B n°1", "2026-10-02T10:00:00+00:00", key="b", rank=0),
            _art("u4", "ancien", "2026-10-03T10:00:00+00:00"),
        ]
        assert [a.title for a in sort_articles(articles)] == ["une site B n°1", "une site A n°2", "recent", "ancien"]

    def test_cle_d_adresse(self):
        assert url_key("https://www.site.test/a/?x=1#y") == url_key("http://site.test/a")


class TestTopBandesAnnonces:
    _PAGE = """<h1 class="item">Top Trailers</h1>
    <div class="card"><img data-src="https://fr.web.img6.acsta.net/c_310/img/a.jpg">
      <a class="meta-title-link" href="/video/player_gen_cmedia=1&amp;cfilm=10.html">Les Misérables Bande-annonce VF</a></div>
    <div class="card"><img data-src="https://fr.web.img6.acsta.net/c_310/img/b.jpg">
      <a class="meta-title-link" href="/video/player_gen_cmedia=2&amp;cfilm=20.html">Le Corset <b>Bande-annonce</b> VF</a></div>
    <a href="/video/player_gen_cmedia=1&amp;cfilm=10.html">Les Misérables Bande-annonce VF</a>"""

    def test_ordre_du_top_titres_et_vignettes(self):
        source = NewsSource(key="allocine_top_trailers", label="AlloCiné", feed_url="x",
                            kind="allocine_trailers", theme="trailers")
        articles = parse_allocine_trailers(self._PAGE, source)
        assert [a.title for a in articles] == ["Les Misérables Bande-annonce VF", "Le Corset Bande-annonce VF"]
        assert [a.rank for a in articles] == [0, 1]
        assert articles[0].url == "https://www.allocine.fr/video/player_gen_cmedia=1&cfilm=10.html"
        assert articles[1].feed_image_url.endswith("/img/b.jpg")

    def test_la_config_livree(self):
        from core.config_loader import load_gaming_news_config
        from gaming_news.sources import load_sources

        sources = {s.key: s for s in load_sources(load_gaming_news_config())}
        assert sources["allocine_top_trailers"].kind == "allocine_trailers"
        assert sources["allocine_top_trailers"].theme == "trailers"
        assert all(s.featured_url for s in sources.values() if s.theme == "cinema")

    def test_badge(self):
        from gui.radar.news_page_tab import _rank_badge

        assert _rank_badge(_art("u", "t", key="allocine_top_trailers", rank=0)) == "🔥 Top n°1"
        assert _rank_badge(_art("u", "t", key="allocine", rank=2)) == "⭐ À la une"
        assert _rank_badge(_art("u", "t")) == ""
