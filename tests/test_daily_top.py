"""« Top news ciné du jour » : couverture + une image 9:16 par news + legende,
dans un seul dossier. Images synthetiques, aucun reseau."""
from datetime import date
from pathlib import Path

import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

from gaming_news.models import Article  # noqa: E402
from news_story.daily_top import (  # noqa: E402
    TopItem, build_daily_caption, compose_daily_top, folder_name, french_date)


def _article(title, source="AlloCiné", summary=""):
    return Article(source_key="allocine", source_label=source, title=title,
                   url=f"https://site.test/{abs(hash(title))}", summary=summary)


def _image(tmp_path, name, color):
    path = tmp_path / f"{name}.png"
    Image.new("RGB", (1600, 900), color).save(path)
    return path


class TestTopDuJour:
    def test_date_en_francais(self):
        assert french_date(date(2026, 10, 9)) == "9 octobre 2026"
        assert folder_name(date(2026, 10, 9)) == "Top news ciné 2026-10-09"

    def test_un_dossier_couverture_puis_une_image_par_news(self, tmp_path):
        items = [TopItem(_article("Un film sort"), _image(tmp_path, "a", (200, 30, 30)), "Le détail A."),
                 TopItem(_article("Une série revient", "Écran Large"), _image(tmp_path, "b", (30, 30, 200)), ""),
                 TopItem(_article("Sans image"), None, "")]
        out = compose_daily_top(items, tmp_path / "top", day=date(2026, 10, 9))
        names = [p.name for p in out]
        assert names[0] == "00_couverture.png"
        assert names[1].startswith("01_un-film-sort") and names[2].startswith("02_une-serie-revient")
        assert len(out) == 3                                    # la news sans image est ignoree
        for path in out:
            assert Image.open(path).size == (1080, 1920)
        caption = (tmp_path / "top" / "legende.txt").read_text(encoding="utf-8")
        assert caption.startswith("🎬 Top news ciné du 9 octobre 2026")
        assert "1. Un film sort\nLe détail A." in caption
        assert "Sources : AlloCiné, Écran Large" in caption
        assert "Sans image" not in caption

    def test_legende_sans_texte_invente(self):
        caption = build_daily_caption([TopItem(_article("Titre"), Path("x"), "")], date(2026, 1, 1))
        assert caption == ("🎬 Top news ciné du 1 janvier 2026\n\n1. Titre\n\n"
                           "Laquelle de ces news t'intéresse le plus ? 👇\n\nSources : AlloCiné\n")


class TestFenetre:
    def test_les_cinq_premieres_cochees_dix_au_plus(self, qtbot=None):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance() or QApplication([])
        from gui.radar.daily_top_dialog import DailyTopDialog

        articles = [_article(f"News {i}") for i in range(12)]
        dialog = DailyTopDialog(articles)
        assert [a.title for a in dialog.selected_articles()] == [f"News {i}" for i in range(5)]
        assert dialog.export_btn.isEnabled()
        for i in range(12):
            dialog.list.item(i).setCheckState(Qt.CheckState.Checked)
        assert not dialog.export_btn.isEnabled()       # plus de 10
        dialog.cleanup()
        app.processEvents()


class TestPhraseSousLeLogo:
    def test_la_phrase_est_sous_le_logo(self, tmp_path):
        from news_story.post_composer import compose_post
        from news_story.video_composer import DEFAULT_CTA

        src = tmp_path / "s.png"
        Image.new("RGB", (1080, 1920), (0, 0, 0)).save(src)
        logo = tmp_path / "logo.png"
        Image.new("RGBA", (200, 200), (255, 0, 255, 255)).save(logo)
        out = compose_post(src, tmp_path / "p.png", title="Un film", label="", vertical=True,
                           logo_path=logo, cta=DEFAULT_CTA["cinema"])
        img = Image.open(out).convert("RGB")
        logo_rows = [y for y in range(img.height) if img.getpixel((530, y)) == (255, 0, 255)]
        below = img.crop((0, max(logo_rows) + 1, img.width, img.height - 400)).convert("L")
        assert below.getextrema()[1] > 240          # du texte blanc sous le logo
        assert DEFAULT_CTA["cinema"].endswith("🎬")


def test_la_tache_de_fond_est_attendue_avant_d_etre_lachee(monkeypatch):
    """Le signal de fin part de run() juste avant sa fin : lacher le QThread a
    ce moment le detruirait encore actif, et Qt arreterait toute l'appli."""
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    from gui.radar import daily_top_dialog as dd

    monkeypatch.setattr(dd.QDesktopServices, "openUrl", lambda *a: None)
    monkeypatch.setattr(dd.QMessageBox, "warning", lambda *a: None)
    waited = []

    class _Thread:
        def wait(self, *a):
            waited.append(True)
            return True

    dialog = dd.DailyTopDialog([])
    for finish in (lambda: dialog._on_ready("dossier", "légende"), lambda: dialog._on_failed("x")):
        dialog._thread = _Thread()
        finish()
        assert dialog._thread is None
    assert waited == [True, True]
