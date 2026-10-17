"""Compilation de clips deja produits (video/compilation.py, gui/compilation_dialog.py)."""
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from video import compilation as comp


def _clips(*durations):
    return [comp.Clip(f"c{i}.mp4", d, f"clip {i}") for i, d in enumerate(durations)]


class TestPlan:
    def test_compte_a_rebours_par_defaut(self):
        assert comp.numbering(5) == [5, 4, 3, 2, 1]
        assert comp.numbering(3, countdown=False) == [1, 2, 3]

    def test_coupe_entre_deux_clips_au_plus_equilibre(self):
        assert comp.split_point([30, 30, 30, 30]) == 2
        assert comp.split_point([50, 10, 10, 30]) == 1

    def test_deux_parties_numerotation_continue(self):
        parts = comp.plan(_clips(30, 30, 30, 30), parts=2)
        assert [p.numbers for p in parts] == [[4, 3], [2, 1]]
        assert [p.index for p in parts] == [1, 2] and parts[0].total == 2
        assert parts[0].duration == 60

    def test_au_moins_deux_clips(self):
        with pytest.raises(comp.CompilationError):
            comp.plan(_clips(30), parts=1)


class TestGraphe:
    def _inputs(self, has_audio=(True, False), intro=None):
        clips = tuple(_clips(20, 25))
        badges = (comp.Sticker("b0.png", 64, 509), comp.Sticker("b1.png", 64, 509))
        return comp.PartInputs(clips=clips, clip_has_audio=has_audio, badges=badges,
                               end_card=comp.Sticker("end.png", 200, 1100), intro=intro)

    def test_concat_son_muet_numeros_et_ecran_de_fin(self):
        args = comp.ffmpeg_args(self._inputs(), "out.mp4")
        graph = args[args.index("-filter_complex") + 1]
        assert "concat=n=2:v=1:a=1" in graph
        assert "anullsrc" in " ".join(args)                     # clip sans son
        assert "overlay=64:509:eof_action=pass:enable='between(t,0.000,19.999)'" in graph
        assert "setpts=PTS+20.000/TB" in graph                   # numero du 2e clip
        assert "enable='between(t,42.000,44.999)'" in graph      # 3 dernieres secondes
        assert args[args.index("-t", args.index("-filter_complex")) + 1] == "45.000"

    def test_intro_posee_au_debut(self):
        from video.intro_overlay import IntroOverlay

        intro = IntroOverlay(path="intro.mp4", src_w=1080, src_h=1920, duration=7.0,
                             text_png="text.png")
        args = comp.ffmpeg_args(self._inputs(intro=intro), "out.mp4")
        graph = args[args.index("-filter_complex") + 1]
        assert "intro.mp4" in args and "text.png" in args
        assert "alphamerge" in graph and "between(t,0,7.000)" in graph


def test_numero_dessine_recadre(tmp_path):
    from PIL import Image

    badge = comp.draw_badge(3, tmp_path / "b.png", caption="PARTIE 1")
    with Image.open(badge.path) as image:
        assert image.width < 400 and image.height < 300          # pas un PNG plein cadre
    assert badge.y >= int(1920 * 0.26)


def test_legende():
    text = comp.caption_text("Top 5 de Squeezie", 1, 2)
    assert "Partie 1/2" in text and "partie 2" in text and "#twitch" in text


def test_clips_disponibles(tmp_path):
    from gui.compilation_dialog import available_clips

    folder = tmp_path / "projet"
    (folder / "clips").mkdir(parents=True)
    (folder / "clips" / "clip_01.mp4").write_bytes(b"x")
    summary = SimpleNamespace(folder=str(folder), name="Squeezie novembre", clip_count=1)
    project = SimpleNamespace(settings_used={"editing_modules": {"intro": True}},
                              clips=[{"index": 0, "file_name": "clips/clip_01.mp4", "duration": 42.0},
                                     {"index": 1, "file_name": "clips/absent.mp4", "duration": 9.0}])
    clips = available_clips([summary], lambda folder: project)
    assert clips == [{"path": str(folder / "clips" / "clip_01.mp4"), "label": "Squeezie novembre",
                      "duration": 42.0, "has_intro": True}]


@pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
                    reason="ffmpeg requis")
def test_vrai_rendu_en_deux_parties(tmp_path):
    paths = []
    for i, duration in enumerate((4, 5, 3)):
        path = tmp_path / f"c{i}.mp4"
        cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
               f"testsrc2=s=540x960:r=30:d={duration}"]
        if i != 1:
            cmd += ["-f", "lavfi", "-i", f"sine=d={duration}", "-c:a", "aac"]
        subprocess.run(cmd + ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True)
        paths.append(str(path))
    written = comp.compose(paths, tmp_path / "out", name="Top", parts=2, intro=True)
    assert [p.name for p, _ in written] == ["Top - partie 1.mp4", "Top - partie 2.mp4"]
    for path, duration in written:
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                                "stream=codec_type,width,height,duration", "-of", "csv=p=0",
                                str(path)], capture_output=True, text=True, check=True).stdout
        assert "video,1080,1920" in probe and "audio" in probe
        video_duration = float([l for l in probe.split() if l.startswith("video")][0].split(",")[-1])
        assert abs(video_duration - duration) < 0.15


def test_fenetre():
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    from gui import compilation_dialog as cd

    clips = [{"path": f"/x/{i}.mp4", "label": f"Clip {i}", "duration": 35.0, "has_intro": i == 2}
             for i in range(3)]
    original = cd.available_clips
    cd.available_clips = lambda summaries, load: clips
    try:
        dialog = cd.CompilationDialog(SimpleNamespace(list_projects=lambda: []))
        for row in (1, 0, 2):                                   # ordre de coche = ordre du montage
            dialog.list.item(row).setCheckState(Qt.CheckState.Checked)
        assert dialog._order == ["/x/1.mp4", "/x/0.mp4", "/x/2.mp4"]
        text = dialog.order_label.text()
        assert text.startswith("1. Clip 1") and "1 min 45 s" in text and "intro" in text
        dialog.parts_combo.setCurrentIndex(1)
        assert "Partie 2" in dialog.order_label.text()
        dialog.reject()
    finally:
        cd.available_clips = original
    app.processEvents()
