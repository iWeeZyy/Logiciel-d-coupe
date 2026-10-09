"""Fil Bandes-annonces : chaines YouTube lues via leur flux Atom, filtre sur
le titre, vignettes YouTube comme images, legende sans les lignes de pub.
Aucun reseau : flux et pages simules."""
from gaming_news.feed_fetcher import parse_feed
from gaming_news.models import NewsSource
from gaming_news.sources import DEFAULT_SOURCES, THEME_LABELS, THEMES, load_sources
from news_story import image_fetcher
from news_story.caption import build_caption
from news_story.image_fetcher import candidates_for_article, youtube_video_id

_YT_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015" xmlns:media="http://search.yahoo.com/mrss/"
      xmlns="http://www.w3.org/2005/Atom">
 <title>Un studio</title>
 <entry>
  <yt:videoId>AAAAAAAAAAA</yt:videoId>
  <title>DUNE 3 | Bande-annonce officielle VF</title>
  <link rel="alternate" href="https://www.youtube.com/watch?v=AAAAAAAAAAA"/>
  <published>2026-10-08T10:00:00+00:00</published>
  <media:group>
   <media:title>DUNE 3 | Bande-annonce officielle VF</media:title>
   <media:thumbnail url="https://i3.ytimg.com/vi/AAAAAAAAAAA/hqdefault.jpg" width="480" height="360"/>
   <media:description>Le synopsis du film.
Suivez-nous sur Instagram : instagram.com/studio</media:description>
  </media:group>
 </entry>
 <entry>
  <yt:videoId>BBBBBBBBBBB</yt:videoId>
  <title>Les coulisses du tournage #shorts</title>
  <link rel="alternate" href="https://www.youtube.com/shorts/BBBBBBBBBBB"/>
  <media:group><media:description>Un extrait.</media:description></media:group>
 </entry>
</feed>"""


def _source(title_filter=""):
    return NewsSource(key="yt", label="Studio", feed_url="https://x.test/feed",
                      theme="trailers", title_filter=title_filter)


class TestFluxYouTube:
    def test_titre_lien_resume_et_vignette(self):
        articles = parse_feed(_YT_FEED, _source())
        assert [a.title for a in articles] == ["DUNE 3 | Bande-annonce officielle VF",
                                               "Les coulisses du tournage #shorts"]
        first = articles[0]
        assert first.url == "https://www.youtube.com/watch?v=AAAAAAAAAAA"
        assert first.summary.startswith("Le synopsis du film.")
        assert first.feed_image_url == "https://i3.ytimg.com/vi/AAAAAAAAAAA/hqdefault.jpg"

    def test_le_filtre_ne_garde_que_les_bandes_annonces(self):
        articles = parse_feed(_YT_FEED, _source("bande[- ]?annonce|teaser|trailer"))
        assert [a.title for a in articles] == ["DUNE 3 | Bande-annonce officielle VF"]

    def test_un_filtre_invalide_est_ignore_plutot_que_de_vider_le_fil(self):
        assert len(parse_feed(_YT_FEED, _source("bande[("))) == 2

    def test_le_plafond_compte_les_entrees_gardees(self):
        assert len(parse_feed(_YT_FEED, _source("bande"), max_articles=1)) == 1


class TestSources:
    def test_le_fil_bandes_annonces_existe(self):
        assert "trailers" in THEMES
        assert THEME_LABELS["trailers"] == "🎞️ Bandes-annonces"
        assert any(s.theme == "trailers" for s in DEFAULT_SOURCES)

    def test_title_filter_est_lu_depuis_la_config(self):
        sources = load_sources({"sources": [
            {"key": "a", "feed_url": "https://x.test", "theme": "trailers", "title_filter": "teaser"}]})
        assert sources[0].theme == "trailers" and sources[0].title_filter == "teaser"

    def test_la_config_livree_contient_le_fil_bandes_annonces(self):
        from core.config_loader import load_gaming_news_config

        trailers = [s for s in load_sources(load_gaming_news_config()) if s.theme == "trailers"]
        assert len(trailers) >= 5
        youtube = [s for s in trailers if s.kind == "rss"]
        assert len(youtube) >= 5
        assert all(s.feed_url.startswith("https://www.youtube.com/feeds/videos.xml?channel_id=UC")
                   for s in youtube)
        # Le top AlloCine est lu depuis sa page, pas depuis un flux.
        assert [s.key for s in trailers if s.kind == "allocine_trailers"] == ["allocine_top_trailers"]


class TestVignettesYouTube:
    def test_identifiant_video(self):
        assert youtube_video_id("https://www.youtube.com/watch?v=AAAAAAAAAAA") == "AAAAAAAAAAA"
        assert youtube_video_id("https://www.youtube.com/watch?feature=x&v=AAAAAAAAAAA") == "AAAAAAAAAAA"
        assert youtube_video_id("https://youtu.be/AAAAAAAAAAA") == "AAAAAAAAAAA"
        assert youtube_video_id("https://www.youtube.com/shorts/AAAAAAAAAAA") == "AAAAAAAAAAA"
        assert youtube_video_id("https://www.allocine.fr/article/x.html") == ""

    def test_haute_definition_d_abord_sans_charger_la_page(self, monkeypatch):
        def fail(*a, **k):
            raise AssertionError("la page YouTube ne doit pas etre chargee")

        monkeypatch.setattr(image_fetcher, "fetch_article_html", fail)
        urls = [c.url for c in candidates_for_article(
            "https://www.youtube.com/watch?v=AAAAAAAAAAA",
            "https://i3.ytimg.com/vi/AAAAAAAAAAA/hqdefault.jpg")]
        assert urls[0] == "https://i.ytimg.com/vi/AAAAAAAAAAA/maxresdefault.jpg"
        assert urls[-1] == "https://i3.ytimg.com/vi/AAAAAAAAAAA/hqdefault.jpg"


class TestLegendeBandeAnnonce:
    def test_les_lignes_de_liens_et_reseaux_sont_retirees(self):
        caption = build_caption(
            "DUNE 3 | Bande-annonce", "Le synopsis.\nAbonnez-vous ► https://youtube.com/x\n"
            "Instagram : instagram.com/studio\nAu cinéma le 3 décembre.", "Studio", "trailers")
        assert "http" not in caption and "instagram" not in caption.lower()
        assert "Le synopsis. Au cinéma le 3 décembre." in caption
        assert caption.startswith("🎞️ DUNE 3")

    def test_un_resume_d_une_seule_ligne_n_est_jamais_ampute(self):
        caption = build_caption("Titre", "Voir www.site.com pour le detail.", "Source", "cinema")
        assert "www.site.com" in caption


class TestCarteDeLaListe:
    def test_le_resume_de_carte_est_nettoye_et_court(self):
        from gui.radar.news_page_tab import _card_summary

        text = "Le synopsis.\nInstagram : https://bit.ly/x\n" + "mot " * 200
        card = _card_summary(text)
        assert "http" not in card and card.startswith("Le synopsis.")
        assert len(card) <= 301 and card.endswith("…")
