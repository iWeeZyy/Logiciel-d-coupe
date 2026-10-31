"""Bande-annonce ActuaFilms automatique (news_story/auto_trailer.py) :
rythme, une seule version par film, memoire des publications."""
from datetime import date
from pathlib import Path

import pytest

from gaming_news.models import Article
from news_story import auto_trailer as at

TODAY = date(2026, 10, 31)


def _a(title, day, vid):
    return Article(source_key="s", source_label="Chaîne", title=title,
                   url=f"https://www.youtube.com/watch?v={vid:0>11}",
                   published_at=f"{day}T12:00:00+00:00")


FEED = [_a("RIVER | Bande-annonce officielle VOST", "2026-10-30", "a"),
        _a("RIVER | Bande-annonce officielle VF", "2026-10-30", "b"),
        _a("SLAYGROUND Bande Annonce VF (2026)", "2026-10-29", "c"),
        _a("Vieux film - Bande-annonce", "2026-10-20", "d")]


def _done(*pairs):
    from news_story.published import url_key

    return {url_key(f"https://www.youtube.com/watch?v={vid:0>11}"): day for vid, day in pairs}


class TestRythme:
    def test_premiere_fois_la_plus_recente_en_vf(self):
        decision = at.decide(FEED, {}, TODAY)
        assert decision.article.title.endswith("VF")          # RIVER VF, pas la VOST
        assert [a.title for a in decision.candidates].count("RIVER | Bande-annonce officielle VOST") == 0

    def test_moins_de_deux_jours_on_attend(self):
        decision = at.decide(FEED, _done(("d", "2026-10-30")), TODAY)
        assert decision.article is None and "2 jours" in decision.reason

    def test_nouvelle_sortie_apres_deux_jours(self):
        decision = at.decide(FEED, _done(("d", "2026-10-28")), TODAY)
        assert decision.article.title.startswith("RIVER")

    def test_rien_de_neuf_on_attend_trois_jours(self):
        feed = [_a("Ancien - Bande-annonce", "2026-10-20", "e"), FEED[3]]
        assert at.decide(feed, _done(("d", "2026-10-29")), TODAY).article is None
        late = at.decide(feed, _done(("d", "2026-10-28")), TODAY)
        assert late.article.title.startswith("Ancien")

    def test_jamais_un_film_deja_publie_meme_autre_version(self):
        decision = at.decide(FEED, _done(("a", "2026-10-25")), TODAY)
        assert all(not a.title.startswith("RIVER") for a in decision.candidates)

    def test_serveur_alloCine_avant_youtube(self):
        allo = Article(source_key="allocine_latest_trailers", source_label="AlloCiné",
                       title="RIVER Bande-annonce VF",
                       url="https://www.allocine.fr/video/player_gen_cmedia=1&cfilm=2.html",
                       published_at="2026-10-30T09:00:00+00:00")
        decision = at.decide(FEED + [allo], {}, TODAY, skip_youtube=True)
        assert [a.url for a in decision.candidates] == [allo.url]

    def test_force_ignore_le_rythme_pas_la_memoire(self):
        decision = at.decide(FEED, _done(("b", "2026-10-31")), TODAY, force=True)
        assert decision.article.title.startswith("SLAYGROUND")


def test_publication_marque_toutes_les_versions_du_film(tmp_path, monkeypatch):
    from news_story import published

    monkeypatch.setattr(published, "FILE", tmp_path / "news_publiees.json")
    tried = []

    def producer(article, out_dir, on_status=None):
        tried.append(article.title)
        if "VF" in article.title and "RIVER" in article.title:
            raise RuntimeError("vidéo disponible seulement en 288p (minimum 720p)")
        video = out_dir / "x.mp4"
        out_dir.mkdir(parents=True, exist_ok=True)
        video.write_bytes(b"x")
        published.mark([article.url])
        return video, video.with_suffix(".txt"), "Légende"

    result = at.run(tmp_path / "out", today=TODAY, fetch=lambda: FEED, producer=producer)
    assert result["status"] == "produit" and result["title"].startswith("SLAYGROUND")
    assert "288p" in result["skipped"][0]                    # RIVER VF ecartee, rapportee
    done = published.load()
    assert published.published_on(FEED[2].url, done)
    # RIVER n'a pas ete publiee : ses versions restent disponibles.
    assert not published.published_on(FEED[0].url, done)


def test_sous_github_actions(tmp_path, monkeypatch):
    out = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary"))
    monkeypatch.setattr(at, "run", lambda out_dir, force=False, skip_youtube=False: {
        "status": "produit", "reason": "r", "title": "T", "video": "v.mp4",
        "caption": "Légende\n#cinema"})
    assert at.main_cli(["--auto-trailer"]) == 0
    assert out.read_text(encoding="utf-8").strip() == "produit=true"
    monkeypatch.setattr(at, "run", lambda out_dir, force=False, skip_youtube=False: {"status": "rien", "reason": "x"})
    assert at.main_cli([]) == 0
    assert out.read_text(encoding="utf-8").strip().endswith("produit=false")


def test_qualite_minimale(tmp_path, monkeypatch):
    from news_story import video_export
    from video import ffmpeg_utils
    from youtube import downloader

    monkeypatch.setattr(downloader, "download_video", lambda *a, **k: str(tmp_path / "s.mp4"))
    monkeypatch.setattr(ffmpeg_utils, "video_resolution", lambda p: (512, 288))
    with pytest.raises(video_export.LowQualitySource):
        video_export.export_video("u", str(tmp_path / "o.mp4"), title="t", label="l",
                                  logo_path=None, text_opacity=1.0, title_scale=1.0,
                                  min_height=720)
    assert not Path(tmp_path / "o.mp4").exists()


def test_deja_sous_titree_pas_de_seconde_couche():
    from news_story.vost import already_subtitled

    assert already_subtitled("Paper Tiger Teaser VO STFR")
    assert already_subtitled("MALFAISANTE | Bande-annonce officielle VOST")
    assert not already_subtitled("Karma Bande-annonce VF")
    assert not already_subtitled("Jusqu'au bout du rêve Bande-annonce VO")
