"""Dernieres bandes-annonces : page AlloCine « plus recentes », secours yt-dlp
quand le flux d'une chaine YouTube ne repond plus, synopsis en legende."""
import requests

from gaming_news import feed_fetcher, featured, youtube_channel
from gaming_news.models import Article, NewsSource
from news_story import release_date as rd
from news_story.caption import build_caption

_YT = NewsSource(key="yt", label="FilmsActu", theme="trailers",
                 feed_url="https://www.youtube.com/feeds/videos.xml?channel_id=UC_i8X3p8oZNaik8X513Zn1Q")


class TestChaineYouTube:
    def test_identifiant_de_chaine(self):
        assert youtube_channel.channel_id(_YT.feed_url) == "UC_i8X3p8oZNaik8X513Zn1Q"
        assert youtube_channel.channel_id("https://site.fr/rss") == ""

    def test_entrees_yt_dlp(self):
        import re

        entries = [{"id": "AAAAAAAAAAA", "title": "ENGLOUTI  Bande Annonce VF", "timestamp": 1791918000},
                   {"id": "BBBBBBBBBBB", "title": "Les coulisses du tournage", "timestamp": 1791910800},
                   {"id": "CCCCCCCCCCC", "title": "THE LAND Teaser"}]
        articles = youtube_channel.entries_to_articles(entries, _YT, 10,
                                                       re.compile("bande[- ]?annonce|teaser", re.I))
        assert [a.title for a in articles] == ["ENGLOUTI Bande Annonce VF", "THE LAND Teaser"]
        assert articles[0].url == "https://www.youtube.com/watch?v=AAAAAAAAAAA"
        assert articles[0].published_at.startswith("2026-10-13T")
        assert articles[1].published_at == ""          # pas de date : jamais inventee

    def test_flux_en_404_lit_la_chaine(self, monkeypatch):
        def not_found(*a, **k):
            raise requests.exceptions.HTTPError("404 Client Error")

        wanted = [Article(source_key="yt", source_label="FilmsActu", title="X Bande Annonce",
                          url="https://www.youtube.com/watch?v=AAAAAAAAAAA")]
        monkeypatch.setattr(requests, "get", not_found)
        monkeypatch.setattr(youtube_channel, "fetch", lambda source, n, title_re=None: wanted)
        assert feed_fetcher.fetch_source(_YT) == wanted

    def test_flux_filtre_complete_par_l_onglet_videos(self, monkeypatch):
        """Flux RSS = 15 dernieres publications, Shorts compris : 2 bandes-
        annonces seulement chez Netflix France. L'onglet Videos complete."""
        def entry(vid, title, date):
            return (f"<entry><yt:videoId>{vid}</yt:videoId><title>{title}</title>"
                    f"<link rel='alternate' href='https://www.youtube.com/watch?v={vid}'/>"
                    f"<published>{date}</published></entry>")

        xml = ("<feed xmlns='http://www.w3.org/2005/Atom' "
               "xmlns:yt='http://www.youtube.com/xml/schemas/2015'>"
               + entry("SSSSSSSSSSS", "oups la boulette", "2026-10-16T10:00:00+00:00")
               + entry("AAAAAAAAAAA", "Ninho | Bande-annonce officielle VF", "2026-10-15T16:00:00+00:00")
               + "</feed>")

        class Ok:
            content = xml.encode()

            def raise_for_status(self):
                pass

        page = [Article(source_key="nf", source_label="Netflix France", title="Ninho | Bande-annonce officielle VF",
                        url="https://www.youtube.com/watch?v=AAAAAAAAAAA", published_at="2026-10-15T00:00:00+00:00"),
                Article(source_key="nf", source_label="Netflix France", title="The Body | Bande-annonce officielle VF",
                        url="https://www.youtube.com/watch?v=BBBBBBBBBBB")]
        monkeypatch.setattr(requests, "get", lambda *a, **k: Ok())
        monkeypatch.setattr(youtube_channel, "fetch", lambda source, n, title_re=None: page)
        source = NewsSource(key="nf", label="Netflix France", theme="trailers", feed_url=_YT.feed_url,
                            title_filter="bande[- ]?annonce|teaser|trailer")
        got = feed_fetcher.fetch_source(source)
        assert [a.url[-11:] for a in got] == ["AAAAAAAAAAA", "BBBBBBBBBBB"]
        assert got[0].published_at.startswith("2026-10-15T16:00")   # date exacte du flux

    def test_un_flux_ordinaire_en_echec_reste_vide(self, monkeypatch):
        def not_found(*a, **k):
            raise requests.exceptions.HTTPError("404")

        monkeypatch.setattr(requests, "get", not_found)
        source = NewsSource(key="s", label="Site", feed_url="https://site.fr/rss")
        assert feed_fetcher.fetch_source(source) == []


def test_doublons_retires_mais_videos_youtube_distinctes():
    def art(url, rank=None):
        return Article(source_key="s", source_label="s", title="t", url=url, rank=rank)

    kept = feed_fetcher.dedupe([art("https://www.allocine.fr/video/a.html", rank=0),
                                art("https://www.youtube.com/watch?v=AAAAAAAAAAA"),
                                art("https://www.youtube.com/watch?v=BBBBBBBBBBB"),
                                art("http://allocine.fr/video/a.html")])
    assert [a.url for a in kept] == ["https://www.allocine.fr/video/a.html",
                                     "https://www.youtube.com/watch?v=AAAAAAAAAAA",
                                     "https://www.youtube.com/watch?v=BBBBBBBBBBB"]


class TestAlloCinePlusRecentes:
    _PAGE = """<span>Top Trailers</span>
    <div class="card video-card-trailer " ><figure>
      <a href="/video/player_gen_cmedia=2&amp;cfilm=20.html" class="layer-link">Out of Place Bande-annonce</a>
      <img class="thumbnail-img" src="https://fr.web.img2.acsta.net/c_222_296/img/a.jpg" /></figure></div>
    <div class="card video-card-trailer " ><figure>
      <a href="/video/player_gen_cmedia=1&amp;cfilm=10.html" class="layer-link">Sans affiche Bande-annonce VF</a>
      <img src="https://fr.web.img2.acsta.net/c_222_296/commons/v9/common/empty/empty_portrait.png" /></figure></div>"""

    def test_vignette_de_sa_propre_carte_et_pas_de_rang(self):
        source = NewsSource(key="l", label="AlloCiné", feed_url="x",
                            kind="allocine_latest_trailers", theme="trailers")
        articles = featured.parse_allocine_trailers(self._PAGE, source, ranked=False)
        assert [a.title for a in articles] == ["Out of Place Bande-annonce", "Sans affiche Bande-annonce VF"]
        assert articles[0].feed_image_url.endswith("/img/a.jpg")
        assert articles[1].feed_image_url == ""        # image vide d'AlloCine ecartee
        assert all(a.rank is None for a in articles)

    def test_date_et_synopsis_de_la_page_de_lecture(self):
        page = ('<script type="application/ld+json">{"@type": "VideoObject", "uploadDate": '
                '"2026-10-12T14:48:57+02:00", "description": "Bande annonce du film"}</script>'
                '<script type="application/ld+json">{"@type": "Movie", "description": '
                '"Wan Ye revient \\u00e0 Wenzhou."}</script>')
        assert featured.player_details(page) == ("2026-10-12T14:48:57+02:00", "Wan Ye revient à Wenzhou.")

    def test_la_source_est_livree(self):
        from core.config_loader import load_gaming_news_config
        from gaming_news.sources import load_sources

        sources = {s.key: s for s in load_sources(load_gaming_news_config())}
        assert sources["allocine_latest_trailers"].kind == "allocine_latest_trailers"
        assert sources["allocine_latest_trailers"].theme == "trailers"


class TestSynopsis:
    _SEARCH = ('<li class="mdl"><span class="ACrL2ZACrpbG0vZmljaGVmaWxtX2dlbl9jZmlsbT0zMjE3OTAuaHRtbA== '
               'meta-title-link">Le Corset</span><span class="date">14 octobre 2026</span></li>')
    _FICHE = ('<div class="meta-body-item meta-body-info"><span class="date">14 octobre 2026</span>'
              '<span class="meta-release-type">en salle</span></div>'
              '<script type="application/ld+json">{"@type": "Movie", "description": '
              '"Dans une ferme de la Beauce, Christophe, 11 ans, suit son p\\u00e8re."}</script>')

    def test_lien_de_la_fiche_au_titre_exact(self):
        assert rd.search_page_link(self._SEARCH, "Le Corset") == \
            "https://www.allocine.fr/film/fichefilm_gen_cfilm=321790.html"
        assert rd.search_page_link(self._SEARCH, "Corset") == ""

    def test_synopsis_long_coupe_en_fin_de_phrase(self):
        text = "Une phrase assez longue pour remplir. " * 30
        page = '<script type="application/ld+json">{"@type": "Movie", "description": "%s"}</script>' % text
        synopsis = rd.parse_synopsis(page)
        assert len(synopsis) <= 700 and synopsis.endswith(".")

    def test_recherche_puis_fiche(self, monkeypatch):
        pages = {"https://www.allocine.fr/rechercher/movie/?q=Le%20Corset": self._SEARCH,
                 "https://www.allocine.fr/film/fichefilm_gen_cfilm=321790.html": self._FICHE}
        monkeypatch.setattr(featured, "fetch_page", lambda url, timeout_s: pages.get(url, ""))
        film = rd.find_film("https://www.youtube.com/watch?v=AAAAAAAAAAA", "Le Corset",
                            "LE CORSET Bande Annonce VF")
        assert film.release.day == "14 octobre 2026" and film.release.where == "en salle"
        assert film.synopsis.startswith("Dans une ferme de la Beauce")

    def test_rien_sans_titre_exact(self, monkeypatch):
        monkeypatch.setattr(featured, "fetch_page", lambda url, timeout_s: self._SEARCH)
        assert rd.find_film("x", "Corset").synopsis == ""

    def test_legende(self):
        caption = build_caption("LE CORSET Bande Annonce VF", "Abonnez-vous ! Description promo.",
                                "FilmsActu", "trailers", answer="Au cinéma le 14 octobre 2026",
                                synopsis="Christophe, 11 ans, suit son père.")
        assert "📖 Synopsis : Christophe, 11 ans, suit son père." in caption
        assert "Description promo" not in caption
        assert "Au cinéma le 14 octobre 2026" in caption
