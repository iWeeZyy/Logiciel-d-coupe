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

    result = at.run(tmp_path / "out", today=TODAY, fetch=lambda: FEED, producer=producer,
                    lookup=None)
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



# --------------------------------------------------- popularite (AlloCine)

def _info(rank, views=0, release="2026-11-20", video="123", film="456", label="X"):
    from news_story.popularity import FilmInfo

    return FilmInfo(film, label, views, rank,
                    date.fromisoformat(release) if release else None, video)


def test_seulement_les_films_populaires_le_plus_populaire_d_abord():
    infos = {"RIVER": _info(800), "SLAYGROUND": _info(40, label="Slayground"),
             "Vieux film": _info(3, release="2026-08-12")}
    decision = at.decide(FEED, {}, TODAY, lookup=lambda title: infos.get(title))
    titles = [a.title for a in decision.candidates]
    # RIVER : rang 800, peu connu ; Vieux film : en salle depuis aout.
    assert titles == ["SLAYGROUND Bande Annonce VF (2026)"]


def test_film_youtube_remplace_par_la_video_allocine():
    infos = {"SLAYGROUND": _info(40, video="20646154", film="324508", label="Slayground")}
    decision = at.decide(FEED, {}, TODAY, skip_youtube=True, lookup=lambda title: infos.get(title))
    article = decision.article
    assert article.url == ("https://www.allocine.fr/video/player_gen_cmedia=20646154"
                           "&cfilm=324508.html")
    assert article.title == "Slayground Bande-annonce VF"


def test_aucun_film_populaire_rien_a_faire():
    decision = at.decide(FEED, {}, TODAY, force=True, lookup=lambda title: _info(1118))
    assert decision.article is None and "populaire" in decision.reason


def test_popularite_lecture_et_titre_qui_ne_correspond_pas():
    from news_story import popularity

    payload = {"results": [
        {"entity_type": "movie", "entity_id": "1", "label": "Mystic River", "viewcount": 9,
         "irankpopular": 900, "last_release": "2003-10-15T00:00:00+01:00", "data": {}},
        {"entity_type": "movie", "entity_id": "324508", "label": "Dune : Troisième partie",
         "original_label": "Dune: Part Three", "viewcount": 222566, "irankpopular": 12,
         "last_release": "2026-12-16T00:00:00.000000+01:00",
         "data": {"default_video": 20646154}}]}
    info = popularity.parse(payload, "Dune : Troisième Partie")
    assert info.rank == 12 and info.release == date(2026, 12, 16) and info.video_id == "20646154"
    assert popularity.parse(payload, "RIVER") is None             # pas « Mystic River »
    assert popularity.is_popular(info, TODAY)
    assert not popularity.is_popular(_info(1118, release="2026-08-12"), TODAY)
    assert popularity.is_popular(_info(None, views=250_000), TODAY)
