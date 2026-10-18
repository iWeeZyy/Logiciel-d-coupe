"""Compilation des plus vus : choix sans doublon (radar/compilation_pick.py),
transitions (video/compilation.py), fenetre du Radar."""
import random
from types import SimpleNamespace

from radar.compilation_pick import pick_top, same_moment
from radar.models import Opportunity
from video import compilation as comp


def _clip(cid, views, *, creator="twitch:1", video="V1", offset=None, duration=30,
          title=None, at="2026-10-15T20:00:00Z"):
    return Opportunity(platform="twitch", content_id=cid, kind="clip", creator_key=creator,
                       title=title or f"Clip {cid}", published_at=at, duration_s=duration,
                       view_count=views, extra={"source_video_id": video, "vod_offset": offset})


class TestMemeMoment:
    def test_meme_passage_de_la_vod(self):
        assert same_moment(_clip("a", 1, offset=1000), _clip("b", 1, offset=1015))
        assert not same_moment(_clip("a", 1, offset=1000), _clip("b", 1, offset=1200))

    def test_streamers_differents_jamais_doublons(self):
        assert not same_moment(_clip("a", 1, offset=1000),
                               _clip("b", 1, offset=1000, creator="twitch:2"))

    def test_sans_vod_clippes_a_la_meme_minute(self):
        a = _clip("a", 1, video="", at="2026-10-15T20:00:00Z")
        assert same_moment(a, _clip("b", 1, video="", at="2026-10-15T20:01:10Z"))
        assert not same_moment(a, _clip("b", 1, video="", at="2026-10-15T21:00:00Z"))

    def test_meme_titre(self):
        assert same_moment(_clip("a", 1, title="LE FAIL 😂", at="2026-10-10T10:00:00Z"),
                           _clip("b", 1, title="le fail", at="2026-10-15T10:00:00Z"))


def test_les_plus_vus_sans_doublon():
    clips = [_clip("faible", 100, offset=5000),
             _clip("top", 9000, offset=1000),
             _clip("copie", 7000, offset=1010),            # meme scene que « top »
             _clip("deux", 5000, offset=3000),
             _clip("trois", 2000, offset=4000)]
    kept, skipped = pick_top(clips, 3)
    assert [c.content_id for c in kept] == ["top", "deux", "trois"]
    assert [(d.content_id, k.content_id) for d, k, _ in skipped] == [("copie", "top")]


class TestTransitions:
    def test_sobres_et_jamais_deux_fois_de_suite(self):
        chosen = comp.pick_transitions(40, random.Random(1))
        assert set(chosen) <= set(comp.TRANSITIONS)
        assert all(a != b for a, b in zip(chosen, chosen[1:]))
        assert not {"pixelize", "circlecrop", "radial", "squeezev"} & set(comp.TRANSITIONS)

    def test_fenetres_des_numeros(self):
        windows = comp.segment_windows([10.0, 10.0, 10.0], 0.4)
        assert windows[0] == (0.0, 9.6)
        assert windows[1][0] == 9.6 + 0.4                  # apres la transition
        assert abs(windows[2][1] - 29.2) < 1e-9             # fin de la video

    def test_graphe_avec_transitions(self):
        clips = tuple(comp.Clip(f"c{i}.mp4", 10.0) for i in range(3))
        part = comp.PartInputs(clips=clips, clip_has_audio=(True,) * 3,
                               badges=tuple(comp.Sticker("b.png", 64, 509) for _ in clips),
                               transitions=("fade", "smoothleft"))
        args = comp.ffmpeg_args(part, "out.mp4")
        graph = args[args.index("-filter_complex") + 1]
        assert "xfade=transition=fade:duration=0.4:offset=9.600" in graph
        assert "xfade=transition=smoothleft:duration=0.4:offset=19.200" in graph
        assert graph.count("acrossfade") == 2 and "concat=" not in graph
        assert args[args.index("-t", args.index("-filter_complex")) + 1] == "29.200"

    def test_duree_deduite_des_transitions(self):
        parts = comp.plan([comp.Clip("a", 30), comp.Clip("b", 30), comp.Clip("c", 30)])
        assert abs(parts[0].duration - 89.2) < 1e-9


def test_fenetre_radar(monkeypatch):
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    from gui.radar.auto_compilation_dialog import AutoCompilationDialog

    clips = [_clip("top", 9000, offset=1000, title="Le fou rire"),
             _clip("copie", 7000, offset=1005, title="fou rire mdr"),
             _clip("deux", 5000, offset=3000, title="Le fail")]
    store = SimpleNamespace(list_opportunities=lambda **kw: list(clips))
    creator = SimpleNamespace(platform="twitch", key="twitch:1", label="Squeezie")
    dialog = AutoCompilationDialog(store, {"twitch:1": creator})
    text = dialog.preview.text()
    assert "1. Le fou rire" in text and "2. Le fail" in text
    assert "écarté : fou rire mdr" in text
    assert dialog.name_edit.text() == "Top 2 des moments de Squeezie cette semaine"
    dialog.reject()
    app.processEvents()


def test_production_d_un_clip(tmp_path, monkeypatch):
    """Un clip du Radar est telecharge puis traite par le pipeline habituel,
    sans l'intro, et devient un projet de « Mes projets »."""
    import pipeline
    from gui import settings_store
    from gui.radar import auto_compilation_dialog as dialog_module
    from radar.analysis import media

    source = tmp_path / "source.mp4"
    source.write_bytes(b"x")
    monkeypatch.setattr(media, "resolve", lambda opp, **kw: SimpleNamespace(path=str(source)))
    monkeypatch.setattr(settings_store, "projects_dir", lambda: tmp_path / "projets")
    seen = {}

    def fake_run(settings, cancel_token=None, on_progress=None):
        seen["intro"] = settings.editing["intro"]["enabled"]
        seen["whole"] = settings.whole_source
        return [SimpleNamespace(file_name="clips/clip_01.mp4")]

    monkeypatch.setattr(pipeline, "run", fake_run)
    path = dialog_module._produce_clip(_clip("top", 9000), {"editing_overrides": {"intro": False}},
                                       None)
    assert path.endswith("clips/clip_01.mp4") and str(tmp_path / "projets") in path
    assert seen == {"intro": False, "whole": True}
    assert (__import__("pathlib").Path(path).parents[1] / "project.json").is_file()



class TestCadrage:
    """Signale : visages « pas suivis » dans les compilations. Le cadrage est
    celui des options (comme un clip seul) ; la version portrait de Twitch
    n'est prise que si l'utilisateur choisit « Webcam en haut + Gameplay en
    bas ». Un choix automatique par taille de visage a ete retire : un
    streamer filme de loin a un visage aussi petit qu'une webcam incrustee."""

    def _produce(self, tmp_path, monkeypatch, fit, portrait):
        import pipeline
        from gui import settings_store
        from gui.radar import auto_compilation_dialog as dialog_module
        from radar.analysis import media

        landscape = tmp_path / "paysage.mp4"
        landscape.write_bytes(b"x")
        monkeypatch.setattr(media, "resolve", lambda opp, **kw: SimpleNamespace(path=str(landscape)))
        monkeypatch.setattr(settings_store, "projects_dir", lambda: tmp_path / "projets")
        asked = []
        monkeypatch.setattr(dialog_module, "portrait_variant",
                            lambda opp, token: asked.append(1) or portrait)
        seen = {}

        def fake_run(settings, cancel_token=None, on_progress=None):
            seen.update(input=settings.input, fit=settings.fit_mode)
            return [SimpleNamespace(file_name="clips/clip_01.mp4")]

        monkeypatch.setattr(pipeline, "run", fake_run)
        dialog_module._produce_clip(_clip("top", 9000),
                                    {"aspect": "9:16", "fit_mode": fit,
                                     "editing_overrides": {"intro": False}}, None)
        return seen, landscape, asked

    def test_par_defaut_visage_suivi_sur_la_video_d_origine(self, tmp_path, monkeypatch):
        seen, landscape, asked = self._produce(tmp_path, monkeypatch, "recadrer", "/p.mp4")
        assert seen == {"input": str(landscape), "fit": "recadrer"} and not asked

    def test_webcam_choisie_version_portrait_twitch(self, tmp_path, monkeypatch):
        seen, _, _ = self._produce(tmp_path, monkeypatch, "webcam_gameplay", "/clips/portrait/top.mp4")
        assert seen == {"input": "/clips/portrait/top.mp4", "fit": "recadrer"}

    def test_webcam_choisie_sans_version_portrait(self, tmp_path, monkeypatch):
        seen, landscape, _ = self._produce(tmp_path, monkeypatch, "webcam_gameplay", None)
        assert seen == {"input": str(landscape), "fit": "recadrer"}

    def test_version_portrait_dans_un_dossier_a_part(self, tmp_path, monkeypatch):
        from gui.radar import auto_compilation_dialog as dialog_module
        from radar import clip_download
        from radar.analysis import media
        from video import ffmpeg_utils

        calls = {}

        def fake_download(url, out_dir, **kw):
            calls.update(out_dir=out_dir, portrait=kw.get("prefer_portrait"))
            return "/x/top.mp4"

        monkeypatch.setattr(media, "clips_dir", lambda: tmp_path)
        monkeypatch.setattr(clip_download, "download_clip", fake_download)
        monkeypatch.setattr(ffmpeg_utils, "video_resolution", lambda p: (1080, 1920))
        assert dialog_module.portrait_variant(_clip("top", 1), None) == "/x/top.mp4"
        assert calls == {"out_dir": str(tmp_path / "portrait"), "portrait": True}
        monkeypatch.setattr(ffmpeg_utils, "video_resolution", lambda p: (1920, 1080))
        assert dialog_module.portrait_variant(_clip("top", 1), None) is None
