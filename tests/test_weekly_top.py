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


def test_top_3_ou_plus_si_clips_courts():
    from types import SimpleNamespace as O

    from radar.top_compilation import MAX_TOTAL_S, fit_count

    assert MAX_TOTAL_S == 150
    longs = [O(duration_s=d) for d in (50, 45, 40, 20, 10)]
    assert len(fit_count(longs)) == 3
    courts = [O(duration_s=d) for d in (25, 30, 20, 28, 26)]
    assert len(fit_count(courts)) == 5
    # Un clip qui ne tient pas arrete la liste (on ne saute pas au suivant).
    mixte = [O(duration_s=d) for d in (30, 30, 30, 70, 10)]
    assert len(fit_count(mixte)) == 3
    # Trois longs : trois quand meme, ENTIERS (la confirmation decide).
    assert len(fit_count([O(duration_s=60)] * 5)) == 3


def test_trop_long_demande_confirmation(tmp_path):
    # Clips de 30 s ; plafond 60 s : un top 3 fait 90 s, soit 30 s de trop.
    calls = []
    report = weekly_top.run(_config(max_seconds=60), tmp_path / "out", tmp_path / "work",
                            adapter=_FakeTwitch(), builder=_builder(calls),
                            today=TODAY, now=NOW)
    assert not calls
    assert any("CONFIRMATION REQUISE" in e and "1 min 30 s" in e for e in report.errors)
    report = weekly_top.run(_config(max_seconds=60), tmp_path / "out2", tmp_path / "work2",
                            adapter=_FakeTwitch(), builder=_builder(calls),
                            today=TODAY, now=NOW, accept_long=True)
    assert calls and all(len(c["ids"]) == 3 for c in calls)
    assert not any("CONFIRMATION" in e for e in report.errors)


def test_moins_de_15_s_de_trop_passe(tmp_path):
    # Plafond 80 s : 3 x 30 = 90 s, 10 s de trop -- produit sans demander.
    calls = []
    report = weekly_top.run(_config(max_seconds=80), tmp_path / "out", tmp_path / "work",
                            adapter=_FakeTwitch(), builder=_builder(calls),
                            today=TODAY, now=NOW)
    assert calls and not any("CONFIRMATION" in e for e in report.errors)
    assert any("vidéo de 1 min 15 s" in n for n in report.notes)


def test_le_plus_vu_est_le_numero_1(monkeypatch, tmp_path):
    from types import SimpleNamespace as O

    import video.compilation
    from radar import top_compilation

    produced, composed = [], {}
    monkeypatch.setattr(top_compilation, "produce_clip",
                        lambda o, *a, **k: produced.append(o.title) or o.title)
    monkeypatch.setattr(video.compilation, "compose",
                        lambda paths, *a, **k: composed.update(paths=paths, **k) or [])
    clips = [O(title="plus vu"), O(title="deuxieme"), O(title="troisieme")]
    top_compilation.build(clips, {}, tmp_path, name="x", countdown=True)
    # #3, #2, #1 dans cet ordre : le plus vu en dernier, avec le #1.
    assert composed["paths"] == ["troisieme", "deuxieme", "plus vu"]
    top_compilation.build(clips, {}, tmp_path, name="x", countdown=False)
    assert composed["paths"] == ["plus vu", "deuxieme", "troisieme"]


def test_legende_et_ecran_de_fin_demandent_le_prochain_top():
    from video.compilation import ASK_NEXT_TEXT, caption_text

    text = caption_text("Top 3 des moments de Kamet0", creator="Kamet0", ask_next=True)
    assert "Dimanche prochain tu veux voir quel top ? Dis-le moi en commentaire !" in text
    assert "Lequel est ton préféré" not in text          # une seule question
    assert text.endswith("#clip #twitch #stream #kamet0 #drole")
    assert ASK_NEXT_TEXT == ("Dimanche prochain tu veux voir quel top ?",
                             "Dis-le moi en commentaire !")
    # Sans l'option (fenetre du logiciel) : la legende d'avant.
    assert "Abonne-toi pour la prochaine compil" in caption_text("Top", creator="x")


def test_phrase_du_clip(tmp_path):
    import json
    from types import SimpleNamespace as O

    from radar.top_compilation import clip_label

    folder = tmp_path / "projet"
    (folder / "clips").mkdir(parents=True)
    clip = folder / "clips" / "clip_01.mp4"
    (folder / "results.json").write_text(json.dumps([{"metadata": {"titles": [
        {"kind": "punchy", "text": "IL A OSE"},
        {"kind": "direct", "text": "Il a vraiment osé faire ça devant tout le monde en live hier soir."}]}}]),
        encoding="utf-8")
    assert clip_label(str(clip)) == "Il a vraiment osé faire ça devant…"
    # Rien de prononce : le titre Twitch s'il dit quelque chose, sinon rien.
    (folder / "results.json").write_text(json.dumps([{"metadata": {}}]), encoding="utf-8")
    assert clip_label(str(clip), O(title="plus fort zongo")) == "plus fort zongo"
    assert clip_label(str(clip), O(title="W")) == ""


def test_numero_avec_phrase_et_ecran_de_fin_sur_deux_lignes(tmp_path):
    from PIL import Image

    from video.compilation import ASK_NEXT_TEXT, draw_badge, draw_end_card

    plain = draw_badge(3, tmp_path / "a.png")
    with_label = draw_badge(3, tmp_path / "b.png", label="Il a vraiment osé faire ça devant tout le monde")
    assert Image.open(with_label.path).height > Image.open(plain.path).height
    card = draw_end_card(ASK_NEXT_TEXT, tmp_path / "c.png")
    assert Image.open(card.path).width <= 1080


def test_texte_en_haut_question_d_abord():
    from video import hook_text

    titles = [{"kind": "punchy", "text": "C'EST FINI"},
              {"kind": "direct", "text": "On met un terme à la run de T1"},
              {"kind": "curiosite", "text": "Et vous vous allez faire quoi en novembre ?"}]
    assert hook_text.choose_text(titles) == "Et vous vous allez faire quoi en novembre ?"
    assert hook_text.choose_text(titles[:2]) == "On met un terme à la run de T1"
    assert hook_text.choose_text([]) == ""
    assert hook_text.choose_text([{"kind": "direct", "text": "un 3 -2 contre un 3 - 1"}]) \
        == "un 3-2 contre un 3-1"


def test_bandeau_design_e():
    from video import hook_text

    assert hook_text.render_band("", 1080, 1920) is None
    image = hook_text.render_band("Caliste je t'aime", 1080, 1920)
    assert image.width == 1080
    assert image.getpixel((540, 2))[:3] == hook_text.PINK            # filet du haut
    assert image.getpixel((5, 30))[3] == hook_text.BAND_ALPHA         # bande translucide


def test_bandeau_jamais_sur_un_visage():
    from video.hook_text import choose_top

    # Visage du streamer en haut (12-24 %), commentateur au milieu (55-70 %).
    faces = [[(0.12, 0.12), (0.55, 0.15)]] * 10
    top = choose_top(faces, 0.10, preferred=0.50, avoid=())
    assert 0.12 + 0.12 * 1.5 <= top and top + 0.10 <= 0.55 - 0.15 * 0.25
    # Sans visage : la position preferee.
    assert choose_top([[]], 0.10, preferred=0.12, avoid=()) == 0.12
    # Le medaillon de l'intro est evite quand on peut.
    top = choose_top([[]], 0.10, preferred=0.30)
    assert top + 0.10 <= 0.255 or top >= 0.465


def test_texte_en_haut_dans_le_rendu():
    from editing.timeline import EditList
    from video.filter_graph import build_ffmpeg_args
    from video.hook_text import HookText

    args = build_ffmpeg_args(
        video_path="in.mp4", edit_list=EditList.identity(0, 10), framing_plan=None,
        zoom_track=None, src_w=1920, src_h=1080, fps=30, ass_path=None, audio_cfg=None,
        export_settings={}, out_mp4_path="out.mp4", hook=HookText("texte.png", 80, 210))
    assert "texte.png" in args
    graph = args[args.index("-filter_complex") + 1]
    assert "overlay=80:210" in graph


def test_compilation_sans_texte_en_haut():
    from editing.production import QUICK_MODULES, default_production

    assert ("hook_text", "Texte en haut") in QUICK_MODULES
    assert default_production()["editing_overrides"]["hook_text"] is True


def test_texte_en_haut_titre_twitch_et_nettoyage():
    from video import hook_text

    spoken = [{"kind": "direct", "text": "44 000 dégâts mais Kali c 'est plus que mes enfants qui vont naître"}]
    # Le titre Twitch (3 mots ou plus) passe devant la phrase prononcee...
    assert hook_text.choose_text(spoken, preferred='"CALISTE JE T\'AIME PLUS QUE MES ENFANTS"') \
        == "CALISTE JE T'AIME PLUS QUE MES ENFANTS"
    # ... mais pas devant une vraie question.
    question = spoken + [{"kind": "curiosite", "text": "Tu ferais quoi à sa place ?"}]
    assert hook_text.choose_text(question, preferred="Un titre de clip long") \
        == "Tu ferais quoi à sa place ?"
    # Titre trop court (« W ») : la phrase, nettoyee et coupee proprement.
    text = hook_text.choose_text(spoken, max_words=10, preferred="W")
    assert "c'est" in text and not text.endswith("qui…")
    assert text == "44 000 dégâts mais Kali c'est plus que mes enfants…"


def test_badge_neon_replay():
    from video import hook_text, neon_badge

    frames = neon_badge.frames("Kamet0")
    assert len(frames) == round(neon_badge.PERIOD_S * neon_badge.FPS)
    assert len({f.size for f in frames}) == 1                          # taille fixe : boucle propre
    w, h = frames[0].size
    assert w % 2 == 0 and h % 2 == 0
    # Fond rouge pastel derriere, a droite (cote « KAMETO EN LIVE »).
    assert frames[0].getpixel((w - 6, h // 2))[:3] == neon_badge.PASTEL
    # Allumage qui gresille puis respiration, sans saut d'une boucle a l'autre.
    assert neon_badge.intensity(0.0) < 0.5 < neon_badge.intensity(0.1)
    assert abs(neon_badge.intensity(neon_badge.PERIOD_S - 0.001) - 1.0) < 0.01
    assert neon_badge.perimeter_point(0, 0, 10, 4, 0.0) == (0, 0)
    assert neon_badge.perimeter_point(0, 0, 10, 4, 0.5) == (10, 4)
    total, offset = hook_text.stack_layout(200, h, 1.0)
    assert offset == h - hook_text.BADGE_OVERLAP and total == offset + 200


def test_texte_en_haut_jamais_ampute():
    from video import hook_text

    # Constate sur le clip du jour du 2026-11-01 : « Saint -Hurigol et le ».
    assert hook_text.choose_text([{"kind": "direct", "text": "Saint -Hurigol et le"}]) == ""
    assert hook_text.choose_text(
        [{"kind": "direct", "text": "Saint -Hurigol a gagné la finale et le"}]) \
        == "Saint-Hurigol a gagné la finale"


def test_texte_en_haut_phrase_trop_courte_et_repli():
    from video import hook_text

    spoken = [{"kind": "direct", "text": "Ouais, je suis"}]
    assert hook_text.choose_text(spoken) == ""
    assert hook_text.choose_text(spoken, preferred="wssh", fallback="Le clip le plus vu du jour") \
        == "Le clip le plus vu du jour"


def test_curiosite_sans_question_ignoree():
    from video import hook_text

    titles = [{"kind": "curiosite", "text": "Ouais, je suis"},
              {"kind": "direct", "text": "Ouais, je suis"}]
    assert hook_text.choose_text(titles, fallback="Le clip le plus vu du jour") \
        == "Le clip le plus vu du jour"
