"""Compilation automatique de la semaine (radar/weekly_top.py) : liste
d'autorisations, commande sans fenetre, memes reglages que la fenetre."""
import json
import os
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from radar import weekly_top
from radar.models import Creator, Opportunity

ROOT = Path(__file__).resolve().parents[1]
TODAY = date(2026, 10, 31)
NOW = datetime(2026, 10, 31, 17, 0, tzinfo=timezone.utc)


class TestAutorisations:
    def test_date_obligatoire_et_valable(self):
        granted, warnings = weekly_top.parse_authorizations([
            {"login": "Anyme023", "authorized_on": "2026-10-12", "note": "DM"},
            {"login": "sansdate"},
            {"login": "datefausse", "authorized_on": "12/10/2026"},
            {"login": "futur", "authorized_on": "2026-11-15"},
            {"authorized_on": "2026-10-01"},
            "pas un objet",
        ], today=TODAY)
        assert list(granted) == ["anyme023"]
        assert granted["anyme023"].authorized_on == date(2026, 10, 12)
        assert len(warnings) == 5
        assert any("futur" in w for w in warnings)

    def test_login_normalise(self):
        granted, _ = weekly_top.parse_authorizations(
            [{"login": "https://www.twitch.tv/Anyme023", "authorized_on": "2026-10-12"}],
            today=TODAY)
        allowed, refused = weekly_top.check_streamers(["@ANYME023", "anyme023"], granted)
        assert [a.login for a in allowed] == ["anyme023"] and refused == []

    def test_hors_liste_refuse_et_signale(self):
        granted, _ = weekly_top.parse_authorizations(
            [{"login": "anyme023", "authorized_on": "2026-10-12"}], today=TODAY)
        allowed, refused = weekly_top.check_streamers(["inconnu", "anyme023"], granted)
        assert [a.login for a in allowed] == ["anyme023"]
        assert refused[0][0] == "inconnu" and "autorisations" in refused[0][1]


def test_reglages_par_defaut_identiques_a_la_fenetre():
    """La compilation du dimanche doit ressembler a celle faite a la main :
    les reglages par defaut viennent du meme code que la fenetre."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from editing.production import default_production
    from gui.widgets.production_options import ProductionOptionsBox

    _app = QApplication.instance() or QApplication([])
    box = ProductionOptionsBox(columns=3)
    assert default_production() == {
        "aspect": box.aspect(), "fill_mode": box.fill_mode(), "fit_mode": box.fit_mode(),
        "subtitle_style": box.subtitle_style(), "editing_overrides": box.editing_overrides()}
    box.deleteLater()


def test_reglage_modifie_par_la_configuration():
    from editing.production import default_production

    production = default_production(aspect="16:9", modules={"captions": False})
    assert production["aspect"] == "16:9"
    assert production["editing_overrides"]["captions"] is False
    assert production["editing_overrides"]["framing"] is False      # rien a recadrer en 16:9


# ------------------------------------------------------------- commande

class _FakeTwitch:
    """Twitch simule : deux streamers, des clips dont deux du meme moment."""

    content_kinds = ("clip",)

    def __init__(self):
        self.resolved = []

    def status(self):
        return None

    def resolve_creator(self, login):
        self.resolved.append(login)
        if login == "fantome":
            return None
        return Creator(platform="twitch", platform_id=f"id-{login}", username=login,
                       display_name=login.capitalize())

    def scan(self, creator, since_iso, max_results=50):
        def clip(cid, views, offset, days_ago=1):
            at = (NOW - timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")
            return Opportunity(platform="twitch", content_id=f"{creator.username}-{cid}",
                               kind="clip", creator_key=creator.key, title=f"Clip {cid}",
                               url=f"https://clips.twitch.tv/{cid}", published_at=at,
                               duration_s=30, view_count=views,
                               extra={"source_video_id": "V1", "vod_offset": offset})
        if creator.username == "maigre":
            return [clip("seul", 10, 100)]
        return [clip("top", 9000, 1000), clip("copie", 8000, 1010), clip("deux", 5000, 3000),
                clip("trois", 2000, 5000), clip("vieux", 99999, 9000, days_ago=12)]


def _builder(calls):
    def build(kept, production, out_dir, *, name, parts, **kw):
        calls.append({"ids": [o.content_id for o in kept], "name": name, "parts": parts,
                      "production": production, "intro": kw["intro"]})
        path = Path(out_dir) / f"{name}.mp4"
        path.write_bytes(b"mp4")
        return [(str(path), 75.0)]
    return build


def _config(**changes):
    config = {"streamers": ["anyme023", "inconnu", "maigre", "fantome"],
              "authorized": [{"login": "anyme023", "authorized_on": "2026-10-12"},
                             {"login": "maigre", "authorized_on": "2026-10-01"},
                             {"login": "fantome", "authorized_on": "2026-10-01"}],
              "count": 5, "parts": 1}
    config.update(changes)
    return config


def test_compilation_de_la_semaine(tmp_path):
    calls, twitch = [], _FakeTwitch()
    report = weekly_top.run(_config(), tmp_path / "out", tmp_path / "work", adapter=twitch,
                            builder=_builder(calls), today=TODAY, now=NOW)
    # Le refuse n'est meme pas demande a Twitch.
    assert "inconnu" not in twitch.resolved
    assert report.refused[0][0] == "inconnu"
    # Les plus vus de la semaine, un seul par moment ; le clip de 12 jours est exclu.
    assert calls[0]["ids"] == ["anyme023-top", "anyme023-deux", "anyme023-trois"]
    assert calls[0]["name"] == "Top 3 des moments de Anyme023 cette semaine"
    assert calls[0]["intro"] is True and calls[0]["parts"] == 1
    assert any("copie" in n for n in report.notes)
    login, files = report.produced[0]
    assert login == "anyme023"
    caption = Path(files[-1])
    assert caption.suffix == ".txt" and "Top 3 des moments" in caption.read_text(encoding="utf-8")
    assert any("maigre" in e and "au moins 2" in e for e in report.errors)
    assert any("fantome" in e and "introuvable" in e for e in report.errors)
    # Base du Radar propre au run, jamais celle de l'utilisateur.
    assert (tmp_path / "work" / "radar.sqlite3").is_file()


def test_personne_d_autorise_rien_n_est_demande(tmp_path):
    calls, twitch = [], _FakeTwitch()
    report = weekly_top.run(_config(authorized=[]), tmp_path / "out", tmp_path / "work",
                            adapter=twitch, builder=_builder(calls), today=TODAY, now=NOW)
    assert calls == [] and twitch.resolved == [] and len(report.refused) == 4


def test_un_seul_streamer_pour_l_essai(tmp_path):
    calls, twitch = [], _FakeTwitch()
    report = weekly_top.run(_config(), tmp_path / "out", tmp_path / "work", only="@Anyme023",
                            adapter=twitch, builder=_builder(calls), today=TODAY, now=NOW)
    assert twitch.resolved == ["anyme023"] and len(calls) == 1 and report.refused == []
    # Meme en essai, un streamer non autorise reste refuse.
    calls, twitch = [], _FakeTwitch()
    report = weekly_top.run(_config(), tmp_path / "o2", tmp_path / "w2", only="inconnu",
                            adapter=twitch, builder=_builder(calls), today=TODAY, now=NOW)
    assert calls == [] and twitch.resolved == [] and report.refused[0][0] == "inconnu"


def test_configuration_invalide(tmp_path):
    with pytest.raises(RuntimeError):
        weekly_top.run(_config(count=20), tmp_path / "o", tmp_path / "w",
                       adapter=_FakeTwitch(), builder=_builder([]), today=TODAY, now=NOW)


def test_point_d_entree(tmp_path, monkeypatch):
    """main_cli : la configuration passee par --config, le rapport ecrit, et
    sous GitHub Actions le refus en annotation + dans le resume du run."""
    from radar import top_compilation
    from radar.platforms import twitch

    calls = []
    monkeypatch.setattr(twitch, "TwitchAdapter", lambda **kw: _FakeTwitch())
    monkeypatch.setattr(top_compilation, "build", _builder(calls))
    monkeypatch.setattr(weekly_top, "DAYS", 3650)       # clips simules dates d'octobre 2026
    config = tmp_path / "config.json"
    config.write_text(json.dumps(_config()), encoding="utf-8")
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    code = weekly_top.main_cli(["--weekly-top", "--config", str(config),
                                "--out", str(tmp_path / "out")])
    assert code == 0 and calls
    report = (tmp_path / "out" / "rapport.txt").read_text(encoding="utf-8")
    assert "✓ anyme023" in report and "REFUSÉ inconnu" in report
    text = summary.read_text(encoding="utf-8")
    assert "REFUSÉ inconnu" in text and "#anyme023 #drole" in text


def test_sans_ecran_et_sans_qt(tmp_path):
    """La commande tourne sans affichage et n'importe jamais Qt."""
    config = tmp_path / "vide.json"
    config.write_text(json.dumps({"streamers": [], "authorized": []}), encoding="utf-8")
    env = {k: v for k, v in os.environ.items()
           if k not in ("DISPLAY", "WAYLAND_DISPLAY", "QT_QPA_PLATFORM", "GITHUB_ACTIONS")}
    code = (
        "import sys, gui_main\n"
        f"sys.argv = ['gui_main.py', '--weekly-top', '--config', {str(config)!r}, "
        f"'--out', {str(tmp_path / 'out')!r}]\n"
        "rc = gui_main.main()\n"
        "assert not [m for m in sys.modules if m.startswith('PySide6')], 'Qt importe'\n"
        "sys.exit(rc)\n")
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 1, result.stderr        # rien a compiler -> 1
    assert "Qt importe" not in result.stderr
    assert (tmp_path / "out" / "rapport.txt").is_file()
