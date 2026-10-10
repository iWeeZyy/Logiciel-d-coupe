"""Carrousels cinema (sorties, box-office, devine le film) et memoire des
news publiees. Les extraits HTML reprennent la structure reelle des pages
AlloCine (cartes « entity-card », tableau du box-office, page photos)."""
from datetime import date
from pathlib import Path

import pytest
from PIL import Image

from news_story import cinema_lists as cl
from news_story import film_carousels as fc
from news_story import published

_CARD = """<li class="mdl"> <div class="card entity-card entity-card-list cf">
<figure class="thumbnail "><span class="thumbnail-container"><img class="thumbnail-img"
 src="data:image/gif;base64,R0lGOD" data-src="https://fr.web.img6.acsta.net/c_310_420/img/7f/e6/affiche.jpg"
 alt="poster" /></span></figure>
<div class="meta"><h2 class="meta-title"><a class="meta-title-link" href="/film/fichefilm_gen_cfilm=1000031247.html">La Maison de nos r&ecirc;ves</a></h2>
<div class="meta-body"><div class="meta-body-item meta-body-info"> <span class="date">7 octobre 2026</span>
<span class="spacer">|</span> 1h 30min <span class="spacer">|</span>
<span class="ACrxyz dark-grey-link">Comédie</span>, <span class="ACrabc dark-grey-link">Famille</span> </div>
<div class="meta-body-item meta-body-direction "><span class="light">De</span> <span class="ACr dark-grey-link">Claude Zidi Jr.</span></div>
<div class="meta-body-item meta-body-actor"><span class="light">Avec</span> <a class="dark-grey-link" href="/p/1">Kev Adams</a>, <span class="ACr dark-grey-link">Chantal Ladesou</span></div>
</div></div>
<div class="rating-holder"><div class="rating-item"><span class="ACr rating-title"> Presse </span>
<div class="stareval"><span class="stareval-note">2,0</span></div></div>
<div class="rating-item"><a class="rating-title" href="/c/"> Spectateurs </a>
<div class="stareval"><span class="stareval-note">2,6</span></div></div>
<div class="rating-item"><a class="rating-title" href="#">Mes amis </a><div class="stareval"><span class="stareval-note no-rating">--</span></div></div></div>
<div class="synopsis"><div class="content-txt "> Un jeune couple pense avoir trouvé​ la solution. Mais la dame va mieux. </div></div>
</div></li>"""

_BOX_OFFICE = """<select><option value="a"> 23 septembre 2026 </option><option value="b" selected> 30 septembre 2026 </option></select>
<table><tbody><tr class="responsive-table-row"><td class="responsive-table-column"><div class="card movie-card-bo cf">
<figure class="thumbnail"><span><img class="thumbnail-img" src="data:image/gif;base64,R0" data-src="https://fr.web.img4.acsta.net/c_150_200/img/6f/af/beast.jpg" />
<div class="label label-text label-sm label-primary-full label-ranking">1</div></span></figure>
<div class="meta"><h2 class="meta-title"><a class="meta-title-link" href="/film/fichefilm_gen_cfilm=1000000085.html">Heart Of The Beast</a></h2>
<div class="meta-body"> Paramount Pictures France </div></div></div></td>
<td data-heading="Entrées" class="responsive-table-column entries col-bg"> 379 236 </td>
<td data-heading="Cumul" class="responsive-table-column entries"> 882 101 </td>
<td data-heading="Semaine" class="responsive-table-column entries"> 2 </td></tr></tbody></table>"""

_PHOTOS = """<h2 class="titlebar-title titlebar-title-md" >Affiches</h2>
<a class="shot-item"><img class="shot-img" src="data:x" data-src="https://fr.web.img2.acsta.net/c_300_300/img/af/fiche.jpg"/></a>
<h2 class="titlebar-title titlebar-title-md" >Photos</h2>
<a class="shot-item"><img class="shot-img" src="data:x" data-src="https://fr.web.img5.acsta.net/c_300_300/pictures/scene1.jpg"/></a>
<a class="shot-item"><img class="shot-img" src="data:x" data-src="https://fr.web.img5.acsta.net/c_300_300/pictures/scene2.jpg"/></a>"""


class TestListesAllocine:
    def test_carte_de_film(self):
        film = cl.parse_releases(_CARD)[0]
        assert film.title == "La Maison de nos rêves"
        assert film.film_id == "1000031247"
        assert film.poster_url == "https://fr.web.img6.acsta.net/img/7f/e6/affiche.jpg"  # pleine taille
        assert (film.release_date, film.duration) == ("7 octobre 2026", "1h 30min")
        assert film.genres == ["Comédie", "Famille"]
        assert film.directors == ["Claude Zidi Jr."]
        assert film.actors == ["Kev Adams", "Chantal Ladesou"]
        assert (film.press_rating, film.spectator_rating) == ("2,0", "2,6")
        assert "​" not in film.synopsis and film.synopsis.startswith("Un jeune couple")

    def test_note_absente_reste_vide(self):
        film = cl.parse_releases(_CARD.replace("<span class=\"stareval-note\">2,6</span>",
                                               "<span class=\"stareval-note no-rating\">--</span>"))[0]
        assert film.spectator_rating == ""

    def test_box_office(self):
        week, entries = cl.parse_box_office(_BOX_OFFICE)
        assert week == "semaine du 30 septembre 2026"
        entry = entries[0]
        assert (entry.rank, entry.film.title, entry.entries, entry.cumulative, entry.week) == \
            (1, "Heart Of The Beast", "379 236", "882 101", "2")
        assert entry.distributor == "Paramount Pictures France"
        assert entry.film.poster_url.endswith("/img/6f/af/beast.jpg")

    def test_photos_sans_les_affiches(self):
        assert cl.parse_film_stills(_PHOTOS) == [
            "https://fr.web.img5.acsta.net/pictures/scene1.jpg",
            "https://fr.web.img5.acsta.net/pictures/scene2.jpg"]


def _image(path: Path, size, color=(90, 60, 40)) -> Path:
    Image.new("RGB", size, color).save(path)
    return path


class TestFiches:
    def test_fiche_sortie(self):
        film = cl.parse_releases(_CARD)[0]
        slide = fc.release_slide(film, None, 1, 6)
        assert slide.label == "SORTIE LE 7 octobre 2026 · 1/6"
        assert "Comédie, Famille · 1h 30min" in slide.lines
        assert "De Claude Zidi Jr. · Avec Kev Adams, Chantal Ladesou" in slide.lines
        assert "Presse 2,0/5 · Spectateurs 2,6/5" in slide.lines

    def test_fiche_box_office(self):
        _, entries = cl.parse_box_office(_BOX_OFFICE)
        slide = fc.box_office_slide(entries[0], None)
        assert slide.label == "N°1 DU BOX-OFFICE"
        assert slide.lines[:2] == ["379 236 entrées cette semaine", "882 101 au total · 2e semaine"]

    def test_l_indice_ne_donne_ni_le_titre_ni_les_acteurs(self):
        film = cl.parse_releases(_CARD)[0]
        hint = fc.guess_hint(film)
        assert "Maison" not in hint and "Kev Adams" not in hint
        assert "Claude Zidi Jr." in hint

    def test_rien_sous_l_interface_tiktok(self, tmp_path):
        """Fond uni (pas d'image) : le bas de l'ecran, recouvert par TikTok,
        reste vide meme avec un long synopsis."""
        film = cl.parse_releases(_CARD)[0]
        film.synopsis = "Une phrase assez longue pour remplir des lignes. " * 12
        slide = fc.release_slide(film, None, 1, 1)
        out = fc.compose_slide(slide, tmp_path / "s.png", cta="N'hésitez pas à me suivre 🎬")
        img = Image.open(out).convert("RGB")
        bottom = img.crop((0, fc.H - 430, fc.W, fc.H))
        assert bottom.getextrema() == ((14, 14), (14, 14), (18, 18))

    def test_carrousel_sorties(self, tmp_path):
        film = cl.parse_releases(_CARD)[0]
        poster = _image(tmp_path / "p.jpg", (400, 600))
        written = fc.compose_releases([film, film], [poster, None], tmp_path / "out",
                                      day=date(2026, 10, 10))
        assert [p.name for p in written][:2] == ["00_couverture.png", "01_la-maison-de-nos-reves.png"]
        assert len(written) == 3
        caption = (tmp_path / "out" / "legende.txt").read_text(encoding="utf-8")
        assert "7 octobre 2026" in caption            # le mercredi des sorties
        assert "1. La Maison de nos rêves — Comédie, Famille · 1h 30min" in caption

    def test_devine_le_film(self, tmp_path):
        film = cl.parse_releases(_CARD)[0]
        still = tmp_path / "still.jpg"
        img = Image.new("RGB", (1600, 900))
        for x in range(0, 1600, 20):          # des details fins, que la mosaique efface
            img.paste((255, 255, 255) if x // 20 % 2 else (0, 0, 0), (x, 0, x + 20, 900))
        img.save(still)
        written = fc.compose_guess(film, still, tmp_path / "g")
        assert [p.name for p in written] == ["01.png", "02.png", "03.png"]
        assert "Réponse en dernière image" in (tmp_path / "g" / "legende.txt").read_text("utf-8")

    def test_mosaique(self):
        img = Image.new("RGB", (800, 450))
        img.paste((255, 255, 255), (0, 0, 10, 450))
        assert fc.pixelate(img, 14).getpixel((5, 200)) != (255, 255, 255)

    def test_photo_de_scene_choisie_pas_une_affiche(self, tmp_path):
        portrait = _image(tmp_path / "a.jpg", (600, 900))
        paysage = _image(tmp_path / "b.jpg", (1600, 900))
        files = {"u1": portrait, "u2": paysage}
        assert fc.pick_still(["u1", "u2"], files.get) == paysage

    def test_titre_sans_mention_de_version(self):
        assert fc.clean_title_for_answer(
            "Le Seigneur des anneaux : les deux tours (version longue)") == \
            "Le Seigneur des anneaux : les deux tours"


class TestNewsPubliees:
    def test_memoire(self):
        assert published.published_on("https://www.numerama.com/a.html") == ""
        published.mark(["https://www.numerama.com/a.html/"], day=date(2026, 10, 9))
        # Meme news, adresse ecrite autrement : reconnue.
        assert published.published_on("https://numerama.com/a.html?utm_source=x") == "2026-10-09"
        assert published.badge("https://numerama.com/a.html") == "✅ Déjà publiée le 9/10"

    def test_date_du_premier_export_gardee_et_purge(self):
        published.mark(["https://x.fr/a"], day=date(2026, 1, 1))
        published.mark(["https://x.fr/a"], day=date(2026, 1, 5))
        assert published.published_on("https://x.fr/a") == "2026-01-01"
        published.mark(["https://x.fr/b"], day=date(2026, 6, 1))     # 90 jours plus tard
        assert published.published_on("https://x.fr/a") == ""

    def test_requete_gardee_pour_youtube(self):
        assert published.url_key("https://www.youtube.com/watch?v=AAA") != \
            published.url_key("https://www.youtube.com/watch?v=BBB")


def test_le_top_du_jour_ne_pre_coche_pas_une_news_deja_publiee(monkeypatch):
    import os
    from types import SimpleNamespace

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    from gui.radar.daily_top_dialog import DailyTopDialog

    articles = [SimpleNamespace(title=f"News {i}", url=f"https://site.fr/{i}", source_label="S",
                                rank=None) for i in range(4)]
    published.mark(["https://site.fr/0"])
    dialog = DailyTopDialog(articles, preselect=2)
    states = [dialog.list.item(i).checkState() for i in range(4)]
    assert states == [Qt.CheckState.Unchecked, Qt.CheckState.Checked, Qt.CheckState.Checked,
                      Qt.CheckState.Unchecked]
    assert "Déjà publiée" in dialog.list.item(0).text()


class TestPresseVsPublic:
    def test_ecart_et_ordre(self):
        a = cl.Film("A", press_rating="2,0", spectator_rating="4,0")
        b = cl.Film("B", press_rating="3,5", spectator_rating="3,0")
        c = cl.Film("C", press_rating="", spectator_rating="4,0")       # pas de note presse
        assert cl.rating_gap(a) == 2.0 and cl.rating_gap(b) == -0.5 and cl.rating_gap(c) is None
        assert [f.title for f in fc.critics_films([b, c, a])] == ["A", "B"]

    def test_fiche(self):
        film = cl.Film("B", press_rating="3,5", spectator_rating="2,0", genres=["Drame"],
                       release_date="30 septembre 2026")
        slide = fc.critics_slide(film, None, 1, 3)
        assert slide.lines[0] == "Presse 3,5/5 · Spectateurs 2,0/5"
        assert slide.lines[1] == "La presse a plus aimé que le public (écart de 1,5)"

    def test_semaine_passee(self):
        assert cl.agenda_url(date(2026, 9, 30)) == \
            "https://www.allocine.fr/film/agenda/sem-2026-09-30/"


_AGENDA = """<article><h2 class="bo-h2">L'incontournable de la semaine</h2>
<p class="bo-p"><a href="/film/fichefilm_gen_cfilm=326107.html"><b>Animals</b></a><b> - Film : </b>Un enlèvement.</p>
<h2 class="bo-h2">Vendredi 9 octobre</h2>
<p class="bo-p"><a href="/series/ficheserie_gen_cserie=36717.html" class="bo-link"><b>Haunted Hotel</b></a><b>, saison 2 - Série : </b>Un hôtel hanté.</p>
<p class="bo-p"><b>Animals</b><b> - Film : </b>Un enlèvement.</p>
<h2 class="bo-h2">À lire aussi</h2><p class="bo-p">Autre chose - sans rapport : x</p>
</article>"""


class TestStreaming:
    def test_agenda(self):
        items = cl.parse_streaming_agenda(_AGENDA, "Netflix")
        assert [(i.day, i.title, i.kind) for i in items] == [
            ("L'incontournable de la semaine", "Animals", "Film"),
            ("Vendredi 9 octobre", "Haunted Hotel, saison 2", "Série")]
        assert items[1].url == "https://www.allocine.fr/series/ficheserie_gen_cserie=36717.html"
        assert items[1].synopsis == "Un hôtel hanté."

    def test_article_de_la_semaine(self):
        page = ('<a class="meta-title-link" href="/article/a1.html">Netflix : la bande-annonce</a>'
                '<a class="meta-title-link" href="/article/a2.html">Netflix : 19 nouveautés '
                'débarquent cette semaine</a>')
        assert cl.find_agenda_article(page, "Netflix") == "https://www.allocine.fr/article/a2.html"
        assert cl.find_agenda_article(page, "Disney+") == ""

    def test_affiche_de_la_fiche(self):
        page = '<meta property="og:image" content="https://fr.web.img3.acsta.net/c_1200_630/img/d5/a.jpg" />'
        assert cl.page_poster(page) == "https://fr.web.img3.acsta.net/img/d5/a.jpg"

    def test_carrousel(self, tmp_path):
        items = cl.parse_streaming_agenda(_AGENDA, "Netflix")
        written = fc.compose_streaming(items, [None, None], tmp_path / "s", day=date(2026, 10, 10))
        assert len(written) == 3
        caption = (tmp_path / "s" / "legende.txt").read_text(encoding="utf-8")
        assert "2. Haunted Hotel, saison 2 (Netflix, vendredi 9 octobre)" in caption
