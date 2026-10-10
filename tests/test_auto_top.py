"""Top news cine du jour prepare automatiquement (news_story/auto_top.py)."""
from datetime import date
from pathlib import Path

import pytest
from PIL import Image

from gaming_news.models import Article, NewsSource
from news_story import auto_top, published


def test_tache_planifiee_quotidienne():
    args = auto_top.schtasks_create_args("07:30", ["C:\\Program Files\\ClipFarming\\ClipFarming.exe",
                                                   "--daily-top"])
    assert args[:6] == ["schtasks", "/Create", "/F", "/SC", "DAILY", "/ST"]
    assert args[6] == "07:30"
    assert args[args.index("/TN") + 1] == auto_top.TASK_NAME
    # Chemin avec espaces : entre guillemets, sinon Windows coupe la commande.
    assert args[args.index("/TR") + 1] == \
        '"C:\\Program Files\\ClipFarming\\ClipFarming.exe" --daily-top'
    assert auto_top.schtasks_delete_args() == ["schtasks", "/Delete", "/F", "/TN",
                                               auto_top.TASK_NAME]


def test_commande_depuis_le_depot():
    command = auto_top.launch_command()
    assert command[-1] == "--daily-top" and command[-2].endswith("gui_main.py")


def test_option_ligne_de_commande(monkeypatch):
    import sys

    import gui_main

    monkeypatch.setattr(sys, "argv", ["gui_main.py", "--daily-top"])
    monkeypatch.setattr(auto_top, "main_cli", lambda: 7)
    assert gui_main.main() == 7


def _articles():
    source = NewsSource(key="s", label="AlloCiné", feed_url="u", theme="cinema")
    return source, [Article(source_key="s", source_label="AlloCiné", title=f"News {i}",
                            url=f"https://site.fr/{i}") for i in range(4)]


def test_top_cree_sans_les_news_deja_publiees(tmp_path, monkeypatch):
    from news_story import daily_top

    source, articles = _articles()
    image = tmp_path / "img.png"
    Image.new("RGB", (800, 600), (40, 60, 90)).save(image)
    monkeypatch.setattr("core.config_loader.load_gaming_news_config", lambda: {})
    monkeypatch.setattr("gaming_news.sources.load_sources", lambda config: [source])
    monkeypatch.setattr("gaming_news.feed_fetcher.fetch_all_sources", lambda *a, **k: articles)
    monkeypatch.setattr(daily_top, "prepare_item",
                        lambda a: daily_top.TopItem(article=a, image_path=image, subtitle="Texte."))
    published.mark(["https://site.fr/0"])
    folder = auto_top.run(tmp_path, count=2, video=False, voice_id="", today=date(2026, 10, 10))
    assert folder == tmp_path / "Top news ciné 2026-10-10"
    names = sorted(p.name for p in folder.iterdir())
    assert names[0] == "00_couverture.png" and "legende.txt" in names
    caption = (folder / "legende.txt").read_text(encoding="utf-8")
    assert "News 1" in caption and "News 2" in caption and "News 0" not in caption
    # Les news du Top sont memorisees : pas reprises demain.
    assert published.published_on("https://site.fr/1") == "2026-10-10"


def test_dossier_absent(tmp_path):
    with pytest.raises(RuntimeError, match="Dossier"):
        auto_top.run(tmp_path / "absent", video=False, voice_id="")
