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


def test_clip_deja_passe_dans_un_top_jamais_repris():
    """2026-11-02 : « HOLD ON » (Kamet0) etait dans le top de la semaine et
    est revenu en clip du jour. Une entree de memoire ajoutee depuis un top
    (login + titre, sans creator_key) doit l'ecarter."""
    from types import SimpleNamespace as O

    from radar import daily_clip as dc

    memory = dc.with_keys([{"id": "top:kamet0:1", "creator": "kamet0", "title": "HOLD ON"}],
                          {"kamet0": "twitch:27115917"})
    hold_on = O(kind="clip", is_live=False, content_id="Suspicious", creator_key="twitch:27115917",
                title="HOLD ON", view_count=2291, duration_s=60, published_at="", extra={})
    other = O(kind="clip", is_live=False, content_id="Other", creator_key="twitch:27115917",
              title="Autre moment", view_count=900, duration_s=30, published_at="", extra={})
    assert dc.choose([hold_on, other], memory) is other


def test_top_ecrit_ses_clips_dans_la_memoire(tmp_path):
    from types import SimpleNamespace as O

    from radar import daily_clip as dc

    clip = O(content_id="X1", creator_key="twitch:1", title="Moment", view_count=10,
             duration_s=30, published_at="", extra={"vod_offset": 5})
    entry = dc.memory_entry(clip, "anyme023")
    assert entry["id"] == "X1" and entry["creator"] == "anyme023"
    path = tmp_path / "m.json"
    dc.save_memory(path, [entry])
    assert dc.load_memory(path)[0]["title"] == "Moment"


def test_paroles_en_anglais_jamais_dans_le_bandeau():
    from video.hook_text import choose_text, foreign

    assert foreign("I bet it's on the beat of the door")
    assert foreign("Y que fue y si te nada te quiero")
    assert not foreign("Non mais pour qui elles se prennent celle-là")
    assert not foreign("Mais il est fou c'est pas possible")
    assert not foreign("Il a vraiment cru que c'était fini")
    assert not foreign("On fait le check les gars")
    spoken = [{"kind": "direct", "text": "I bet it's on the beat of the door"}]
    assert choose_text(spoken, preferred="thriller", fallback="Le clip du moment") \
        == "Le clip du moment"


def test_fleche_vers_le_plus_tiktok():
    from video import follow_arrow as fa

    frames = fa.frames()
    assert len(frames) == 90                                       # 3 s a 30 i/s
    assert frames[-1].getchannel("A").getbbox() is None             # derniere image vide
    assert frames[15].getchannel("A").getbbox() is not None         # trait en cours
    assert fa.start_for(60) == 28.5 and fa.start_for(2) == 0.0      # au milieu du clip
    # Elle s'arrete juste a gauche du + (vers (955, 1027) dans la video).
    end = fa.bez(1.0)
    assert end[0] < 955 and abs(end[1] - 1027) < 60


def test_bandeau_evite_la_zone_de_la_fleche():
    from video import follow_arrow as fa
    from video.hook_text import choose_top

    zone = (*fa.ZONE, 0.3)
    top = choose_top([[]], 0.10, preferred=0.52, avoid=(zone,))
    assert top + 0.10 <= fa.ZONE[0] or top >= fa.ZONE[1]


def test_clip_du_jour_demande_la_fleche():
    import inspect

    from radar import daily_clip

    assert 'block["follow_arrow"] = True' in inspect.getsource(daily_clip.run)


def test_nom_tel_qu_on_le_tape_et_commentaire_epingle():
    from radar import daily_clip, naming

    config = {"search_names": {"kamet0": "Kameto", "anyme023": "Anyme"}}
    assert naming.search_name(config, "Kamet0", "Kamet0") == "Kameto"
    assert naming.search_name(config, "squeezie", "Squeezie") == "Squeezie"
    assert naming.of("Anyme") == "d'Anyme" and naming.of("Kameto") == "de Kameto"
    text = daily_clip.caption("HOLD ON", "Kamet0", "Kameto")
    assert text.startswith("Kameto — HOLD ON")
    assert "#kamet0 #kameto #drole" in text
    assert "#squeezie #drole" in daily_clip.caption("x y", "Squeezie", "Squeezie")
    assert naming.clip_comment("abc") == naming.clip_comment("abc")     # stable
    assert naming.clip_comment("abc").endswith("👇")
    assert naming.top_comment(5) == "Ton préféré : #1, #2, #3, #4 ou #5 ? 👇"


def test_retour_a_la_ligne_voulu_dans_le_bandeau():
    from video.hook_text import choose_text, clean, render_band

    text = "Date de sortie du son Numéro\nVendredi 6 Novembre"
    assert clean(text) == text
    assert choose_text([], preferred=text) == text
    assert clean("c 'est  bon") == "c'est bon"
    one = render_band("Date de sortie du son Numéro Vendredi 6 Novembre", 1080, 1920)
    two = render_band(text, 1080, 1920)
    assert two.height >= one.height


def test_legende_sans_titre_vide_de_sens():
    from radar.daily_clip import caption

    assert caption("x", "Squeezie").startswith("Squeezie — Le clip du moment 🔥")
    assert caption(".", "Squeezie").startswith("Squeezie — Le clip du moment")
    assert caption("MDRRR", "Anyme023", "Anyme").startswith("Anyme — MDRRR")


def test_pas_de_virgule_en_fin_de_bandeau():
    """Phrase prononcee coupee au milieu : « LES GARS, CHANTEZ MINI, MINI, »
    (Squeezie, 2026-11-04). La virgule finale est retiree, pas celles du
    milieu, ni un point d'exclamation ou d'interrogation."""
    from video.hook_text import clean

    assert clean("Les gars, chantez mini, mini,") == "Les gars, chantez mini, mini"
    assert clean("Attends :") == "Attends"
    assert clean("T'as vu ça ?") == "T'as vu ça ?"
    assert clean("HOLD ON !") == "HOLD ON !"
