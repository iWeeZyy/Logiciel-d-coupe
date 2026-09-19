"""Onglet "Gaming News" du Radar : agregation des flux hors du fil de
l'interface (QThread), rendu des cartes d'article, etats vide/erreur.

Meme convention que les autres tests GUI de ce depot (test_home_youtube.py) :
pytest.importorskip("PySide6"), QT_QPA_PLATFORM=offscreen, pas de fenetre reelle.
"""
import os
import time

import pytest


@pytest.fixture
def app():
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _process_until(app, condition, timeout_s=3.0, step_s=0.01):
    # time.sleep(), pas QThread.msleep() sur l'instance du thread principal :
    # cette derniere a bloque la livraison des signaux en file d'attente dans
    # cet environnement (deadlock constate), le sommeil simple n'a pas ce
    # probleme puisqu'il rend la main a app.processEvents() normalement.
    deadline = time.time() + timeout_s
    while not condition() and time.time() < deadline:
        app.processEvents()
        time.sleep(step_s)
    return condition()


def _fake_article(key="a", title="Un jeu annonce"):
    from gaming_news.models import Article

    return Article(source_key=key, source_label="VGC", title=title,
                   url=f"https://example.test/{key}", published_at="")


class TestGamingNewsTab:
    def test_articles_are_rendered_as_cards(self, app, monkeypatch):
        from gui.radar import news_page_tab

        articles = [_fake_article("a", "Premier article"), _fake_article("b", "Second article")]
        monkeypatch.setattr(news_page_tab, "fetch_all_sources", lambda *a, **k: articles)

        tab = news_page_tab.GamingNewsTab()
        tab.refresh()
        assert _process_until(app, lambda: "récupérée" in tab.status_label.text())

        assert tab.list_layout.count() == 2
        tab.cleanup()

    def test_no_articles_shows_an_explanatory_message_not_a_blank_screen(self, app, monkeypatch):
        from gui.radar import news_page_tab

        monkeypatch.setattr(news_page_tab, "fetch_all_sources", lambda *a, **k: [])

        tab = news_page_tab.GamingNewsTab()
        tab.refresh()
        assert _process_until(app, lambda: "Aucune actualité" in tab.status_label.text())
        tab.cleanup()

    def test_an_unexpected_error_is_shown_rather_than_crashing(self, app, monkeypatch):
        from gui.radar import news_page_tab

        def _raise(*a, **k):
            raise RuntimeError("panne reseau simulee")

        monkeypatch.setattr(news_page_tab, "fetch_all_sources", _raise)

        tab = news_page_tab.GamingNewsTab()
        tab.refresh()
        assert _process_until(app, lambda: "panne reseau simulee" in tab.status_label.text())
        tab.cleanup()

    def test_refresh_button_is_disabled_while_a_scan_is_running(self, app, monkeypatch):
        from gui.radar import news_page_tab

        monkeypatch.setattr(news_page_tab, "fetch_all_sources", lambda *a, **k: [_fake_article()])

        tab = news_page_tab.GamingNewsTab()
        tab.refresh()
        assert not tab.refresh_btn.isEnabled()
        assert _process_until(app, lambda: tab.refresh_btn.isEnabled())
        tab.cleanup()

    def test_on_shown_triggers_an_automatic_scan_only_once(self, app, monkeypatch):
        from gui.radar import news_page_tab

        calls = []
        monkeypatch.setattr(news_page_tab, "fetch_all_sources",
                            lambda *a, **k: calls.append(1) or [_fake_article()])

        tab = news_page_tab.GamingNewsTab()
        tab.on_shown()
        # Attend que le SIGNAL soit traite (self._articles rempli), pas
        # seulement que fetch_all_sources ait ete appelee dans le fil du
        # thread -- sinon le second on_shown() peut s'executer avant que
        # l'etat "articles deja la" soit visible du fil principal.
        assert _process_until(app, lambda: len(tab._articles) == 1)
        tab.on_shown()  # les articles sont deja la : ne redemande rien
        app.processEvents()
        assert len(calls) == 1
        tab.cleanup()

    def test_a_story_button_opens_the_story_dialog(self, app, monkeypatch):
        from gui.radar import news_page_tab

        article = _fake_article()
        monkeypatch.setattr(news_page_tab, "fetch_all_sources", lambda *a, **k: [article])

        opened = []

        class _FakeDialog:
            def __init__(self, article, parent=None):
                opened.append(article)

            def exec(self):
                return None

        monkeypatch.setattr("gui.radar.story_dialog.StoryDialog", _FakeDialog)

        tab = news_page_tab.GamingNewsTab()
        tab.refresh()
        assert _process_until(app, lambda: tab.list_layout.count() == 1)

        card = tab.list_layout.itemAt(0).widget()
        story_button = [b for b in card.findChildren(type(tab.refresh_btn))
                        if "Story" in b.text()][0]
        story_button.click()

        assert opened == [article]
        tab.cleanup()
