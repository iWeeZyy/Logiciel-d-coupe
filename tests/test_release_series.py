"""Fiche SERIE AlloCine (news_story/release_date.py) : 2026-11-04, la serie
Harry Potter avait recu la date et le synopsis du film de 2001."""
from news_story import release_date as rd

PAGE = """
<div class="banner-coming-soon">
    Nouvelle saison prévue le 25 décembre 2026 sur
    <img class="banner-coming-soon-img" src="x.png" alt="HBO Max" />
</div>
<section id="synopsis-details"><div class="content-txt ">
<p class="bo-p">Harry Potter n'a rien de spécial, du moins c'est ce que lui répète sa tante.</p>
<p class="bo-p"><i>D'après les romans.</i></p></div></section>
"""


def test_date_et_plateforme_de_la_serie():
    release = rd.parse_series_page(PAGE)
    assert release.day == "25 décembre 2026" and release.where == "sur HBO Max"
    assert rd.line(release, today=__import__("datetime").date(2026, 11, 4)) \
        == "Sur HBO Max le 25 décembre 2026"
    assert rd.parse_series_page("<p>rien</p>") is None


def test_synopsis_de_la_serie():
    assert rd.parse_series_synopsis(PAGE).startswith("Harry Potter n'a rien de spécial")


def test_jamais_la_fiche_d_un_film_homonyme(monkeypatch):
    fetched = []

    def fake_fetch(url, timeout_s=15):
        fetched.append(url)
        return PAGE if "ficheserie" in url else "<p>film</p>"

    import gaming_news.featured as featured
    monkeypatch.setattr(featured, "fetch_page", fake_fetch)
    info = rd.find_film("https://www.allocine.fr/video/player_gen_cmedia=1&cserie=28244.html",
                        "Harry Potter à l'école des sorciers")
    assert info.release.where == "sur HBO Max"
    assert not any("rechercher/movie" in u for u in fetched)
