"""Source Breakflip : site sans flux RSS, lu depuis sa page d'actualites
(gaming_news/breakflip.py). HTML et sitemaps ecrits a la main, calques sur
la structure reelle du site -- aucun acces reseau."""
from gaming_news import breakflip, sources
from gaming_news.models import NewsSource

_SOURCE = NewsSource(key="breakflip", label="Breakflip",
                     feed_url="https://www.breakflip.com/actualites/", kind="breakflip")


def _card(article_id, title, summary, image="https://www.breakflip.com/img.jpg"):
    return f'''
    <div class="flex flex-col md:flex-row relative rounded-xl">
      <a href="https://www.breakflip.com/actualites/{article_id}.html" aria-label="Lire: Nouvelle mise à jour majeure disponible" class="absolute inset-0 z-40"></a>
      <div class="absolute left-3 top-3"> </div>
      <img src="{image}" loading="lazy" alt="{title}" class="w-full" />
      <div class="flex flex-col">
        <h3 class="text-base font-semibold">{title}</h3>
        <p class="text-xs text-gray-300"> {summary} </p>
        <p class="text-[11px] uppercase"> Il y a 1 mois par <a href="https://www.breakflip.com/author/x/" class="relative z-20 underline">Auteur</a> </p>
      </div>
    </div>'''


_HUB = ('<nav><a href="https://www.breakflip.com/actualites/">Actus</a></nav>'
        + _card(101, "GTA 6 : la date de sortie officielle", "Rockstar confirme le 26 mai.")
        + _card(100, "Un jeu sans chapeau", "")
        + _card(99, "Titre avec &amp; entité", "Résumé &laquo; échappé &raquo;",
                image="https://www.breakflip.com/a.webp?ver=1&amp;x=2"))


class TestPageActualites:
    def test_lit_titre_resume_image_et_lien_de_chaque_carte(self):
        articles = breakflip.parse_hub(_HUB, _SOURCE)
        assert [a.url for a in articles] == [
            "https://www.breakflip.com/actualites/101.html",
            "https://www.breakflip.com/actualites/100.html",
            "https://www.breakflip.com/actualites/99.html",
        ]
        first = articles[0]
        assert first.title == "GTA 6 : la date de sortie officielle"
        assert first.summary == "Rockstar confirme le 26 mai."
        assert first.feed_image_url == "https://www.breakflip.com/img.jpg"
        assert first.source_key == "breakflip" and first.source_label == "Breakflip"

    def test_un_chapeau_vide_ne_prend_jamais_la_ligne_d_age_et_d_auteur(self):
        assert breakflip.parse_hub(_HUB, _SOURCE)[1].summary == ""

    def test_les_entites_html_sont_decodees(self):
        article = breakflip.parse_hub(_HUB, _SOURCE)[2]
        assert article.title == "Titre avec & entité"
        assert article.summary == "Résumé « échappé »"
        assert article.feed_image_url == "https://www.breakflip.com/a.webp?ver=1&x=2"

    def test_aucune_date_n_est_inventee_depuis_l_age_relatif(self):
        assert all(a.published_at == "" for a in breakflip.parse_hub(_HUB, _SOURCE))

    def test_respecte_le_nombre_maximal(self):
        assert len(breakflip.parse_hub(_HUB, _SOURCE, max_articles=2)) == 2

    def test_structure_inconnue_donne_une_liste_vide(self):
        assert breakflip.parse_hub("<html><body>Refonte</body></html>", _SOURCE) == []


class TestSitemap:
    _INDEX = """<sitemapindex>
      <sitemap><loc>https://www.breakflip.com/guide-sitemap33.xml</loc><lastmod>2026-09-23T13:21:02+00:00</lastmod></sitemap>
      <sitemap><loc>https://www.breakflip.com/actu-sitemap.xml</loc><lastmod>2026-09-01T08:22:30+00:00</lastmod></sitemap>
      <sitemap><loc>https://www.breakflip.com/actu-sitemap32.xml</loc><lastmod>2026-06-06T09:18:45+00:00</lastmod></sitemap>
      <sitemap><loc>https://www.breakflip.com/actu-sitemap33.xml</loc><lastmod>2026-09-01T08:22:30+00:00</lastmod></sitemap>
    </sitemapindex>"""

    def test_choisit_le_sitemap_d_actualites_le_plus_recent(self):
        # Les guides, plus recents, sont ignores ; a date egale, le plus haut
        # numero (celui qui recoit les nouveaux articles) l'emporte.
        assert breakflip.latest_actu_sitemap(self._INDEX) == "https://www.breakflip.com/actu-sitemap33.xml"

    def test_index_sans_actualites(self):
        assert breakflip.latest_actu_sitemap("<sitemapindex></sitemapindex>") == ""

    def test_dates_par_article(self):
        xml = """<urlset><url><loc>https://www.breakflip.com/actualites/101.html</loc>
        <lastmod>2026-08-26T15:00:26+00:00</lastmod><image:image></image:image></url></urlset>"""
        assert breakflip.parse_sitemap_dates(xml) == {
            "https://www.breakflip.com/actualites/101.html": "2026-08-26T15:00:26+00:00"}


class TestConfiguration:
    def test_breakflip_fait_partie_des_sources_par_defaut(self):
        found = [s for s in sources.DEFAULT_SOURCES if s.key == "breakflip"]
        assert found and found[0].kind == "breakflip"

    def test_le_kind_est_lu_depuis_la_config(self):
        config = {"sources": [{"key": "bf", "feed_url": "https://www.breakflip.com/actualites/", "kind": "breakflip"}]}
        assert sources.load_sources(config)[0].kind == "breakflip"

    def test_kind_absent_ou_inconnu_retombe_sur_rss(self):
        config = {"sources": [{"key": "a", "feed_url": "https://x.test/feed"},
                              {"key": "b", "feed_url": "https://y.test/feed", "kind": "inconnu"}]}
        assert [s.kind for s in sources.load_sources(config)] == ["rss", "rss"]

    def test_la_config_livree_contient_breakflip(self):
        import json
        from pathlib import Path
        config = json.loads((Path(__file__).resolve().parent.parent / "config" / "gaming_news.json").read_text(encoding="utf-8"))
        assert any(s.key == "breakflip" and s.kind == "breakflip" for s in sources.load_sources(config))
