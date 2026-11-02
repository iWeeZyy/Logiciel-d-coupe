"""Clip du jour (radar/daily_clip.py), sans reseau ni ffmpeg."""
from pathlib import Path

from radar import daily_clip
from test_weekly_top import NOW, TODAY, _config, _FakeTwitch


def _producer(calls):
    def produce(opportunity, production, token, on_status, *, projects_dir, clips_dir):
        calls.append(opportunity)
        path = Path(projects_dir) / f"{opportunity.content_id}.mp4"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"mp4")
        return str(path)
    return produce


def test_clip_du_jour_le_plus_vu_puis_jamais_le_meme_moment(tmp_path):
    memory = tmp_path / "clips_publies.json"
    calls = []
    report, files = daily_clip.run(_config(), tmp_path / "out", tmp_path / "w", memory_path=memory,
                                   adapter=_FakeTwitch(), producer=_producer(calls),
                                   today=TODAY, now=NOW)
    assert [o.content_id for o in calls] == ["anyme023-top"]       # 9000 vues
    assert files[0].name == "Clip_Anyme023.mp4"
    text = files[1].read_text(encoding="utf-8")
    assert text.endswith("#clip #twitch #stream #anyme023 #drole")
    assert "Abonne-toi pour le prochain clip" in text
    # Le lendemain : « copie » montre le meme passage du live -> ecarte.
    report, files = daily_clip.run(_config(), tmp_path / "out2", tmp_path / "w2",
                                   memory_path=memory, adapter=_FakeTwitch(),
                                   producer=_producer(calls), today=TODAY, now=NOW)
    assert [o.content_id for o in calls][-1] == "anyme023-deux"
    assert len(daily_clip.load_memory(memory)) == 2


def test_streamer_non_autorise_jamais_clippe(tmp_path):
    calls = []
    report, files = daily_clip.run(_config(authorized=[]), tmp_path / "o", tmp_path / "w",
                                   adapter=_FakeTwitch(), producer=_producer(calls),
                                   today=TODAY, now=NOW)
    assert not calls and not files
    assert report.refused


def test_sans_qt():
    import subprocess
    import sys

    code = ("import sys, radar.daily_clip as d; d.choose([], []); "
            "assert not any(m.startswith('PySide6') for m in sys.modules)")
    subprocess.run([sys.executable, "-c", code], check=True,
                   cwd=Path(__file__).resolve().parent.parent)


def test_legende_sans_guillemets_du_titre():
    assert daily_clip.caption('"CALISTE JE T\'AIME"', "Kamet0").startswith(
        "Kamet0 — CALISTE JE T'AIME 🔥")


def test_le_frais_seulement_a_qualite_comparable():
    """Le live de la veille au soir passe devant seulement s'il vaut au
    moins 70 % des vues du plus vu des 24 h (2026-11-02 : un clip frais a
    154 vues etait passe devant)."""
    from types import SimpleNamespace as O

    from radar import daily_clip as dc

    best = O(view_count=1000)
    assert dc.pick(O(view_count=800), best).view_count == 800
    assert dc.pick(O(view_count=154), best) is best
    assert dc.pick(None, best) is best and dc.pick(best, None) is best
    assert dc.best_of(None, O(view_count=5), O(view_count=9)).view_count == 9


def test_phrase_brouillee_jamais_affichee():
    from video.hook_text import choose_text, garbled

    assert garbled("C'est très comamé on sait parti parti parti parti")
    assert garbled("MARTIIIIIIIIII")
    assert not garbled("Il a vraiment cru que c'était fini")
    titles = [{"kind": "direct", "text": "C'est très comamé on sait parti parti parti parti"}]
    assert choose_text(titles, preferred="MARTIIIIIIII", fallback="Le clip du moment") \
        == "Le clip du moment"


def test_titre_twitch_court_et_mots_sensibles():
    from video.hook_text import choose_text, unsafe

    spoken = [{"kind": "direct", "text": "Coyve on fait coyve on me suicide les gars"}]
    assert unsafe("on me suicide les gars") and unsafe("Je vais me tuer")
    assert not unsafe("Il a tué le game")
    assert choose_text(spoken, preferred="HOLD ON", fallback="x") == "HOLD ON"
    assert choose_text(spoken, preferred="", fallback="Le clip du moment") == "Le clip du moment"
