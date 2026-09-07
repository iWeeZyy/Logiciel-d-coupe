"""Rendu des sous-titres : fichier .ass incruste et export .srt/.vtt.

Verifie surtout deux choses : que les styles historiques produisent toujours le
meme decoupage qu'avant les sous-titres intelligents, et que le .srt exporte
affiche exactement les memes blocs aux memes instants que la video.
"""
from core.models import Word
from editing.captions import build_captions, score_emphasis
from export.subtitles_export import build_srt, build_vtt, write_subtitles
from video.subtitle_renderer import render_ass_file

SMART_STYLE = {
    "mode": "smart", "words_per_group": 2, "uppercase": True, "font_size": 100,
    "emphasis_color": "&H004CA2E0", "emphasis_scale": 1.2, "animation": "pop",
    "margin_v": 380, "min_display_ms": 240, "gap_ms": 25,
}
LEGACY_STYLE = {
    "mode": "progressive", "words_per_group": 2, "uppercase": True, "font_size": 96,
    "margin_v": 380, "min_display_ms": 260, "gap_ms": 30,
}


def _words(texts, start=0.0, step=0.5):
    return [Word(text=t, start=start + i * step, end=start + (i + 1) * step)
            for i, t in enumerate(texts)]


def _dialogue_lines(ass_text):
    return [l for l in ass_text.splitlines() if l.startswith("Dialogue:")]


def test_legacy_progressive_style_groups_exactly_as_before(tmp_path):
    words = _words(["ce", "truc", "va", "tout", "changer"])
    path = tmp_path / "clip.ass"

    render_ass_file(words, clip_start=0.0, style=LEGACY_STYLE, out_ass_path=str(path))
    lines = _dialogue_lines(path.read_text(encoding="utf-8"))

    # 5 mots, 2 par groupe -> 3 blocs, comme le rendu historique.
    assert len(lines) == 3
    assert "CE TRUC" in lines[0]
    # Aucune balise de mise en evidence sur un style historique.
    assert "\\fs" not in path.read_text(encoding="utf-8").split("[Events]")[1]


def test_smart_style_marks_the_emphasised_word(tmp_path):
    words = _words(["voici", "le", "secret", "ultime"])
    groups = build_captions(
        words, max_words_per_group=2,
        emphasis_scores=score_emphasis(words, keyword_terms=["secret"]),
        max_emphasis_ratio=1.0, min_emphasis_score=0.5,
    )
    path = tmp_path / "clip.ass"

    render_ass_file(words, clip_start=0.0, style=SMART_STYLE, out_ass_path=str(path),
                    caption_groups=groups)
    content = path.read_text(encoding="utf-8")

    events = content.split("[Events]")[1]
    assert "SECRET" in events
    assert "\\fs120" in events                 # 100 * 1.2
    assert "&H004CA2E0" in events              # couleur de mise en evidence
    assert "\\t(0,120," in events              # animation "pop", bornee
    assert "{\\r}" in events                   # retour au style apres le mot


def test_smart_style_without_groups_still_renders_readable_subtitles(tmp_path):
    # Module de sous-titres desactive : le style smart doit quand meme afficher
    # quelque chose de correct, sans mise en evidence.
    words = _words(["un", "deux", "trois", "quatre"])
    path = tmp_path / "clip.ass"

    render_ass_file(words, clip_start=0.0, style=SMART_STYLE, out_ass_path=str(path))
    content = path.read_text(encoding="utf-8")

    assert len(_dialogue_lines(content)) == 2
    assert "\\fs120" not in content.split("[Events]")[1]


def test_margin_override_lands_in_the_style_header(tmp_path):
    path = tmp_path / "clip.ass"

    render_ass_file(_words(["a", "b"]), clip_start=0.0, style=SMART_STYLE,
                    out_ass_path=str(path), margin_v=612)
    header = path.read_text(encoding="utf-8").split("[Events]")[0]

    assert ",612,1" in header
    assert ",380,1" not in header


def test_empty_word_list_still_writes_a_valid_ass_file(tmp_path):
    path = tmp_path / "clip.ass"

    render_ass_file([], clip_start=0.0, style=SMART_STYLE, out_ass_path=str(path))
    content = path.read_text(encoding="utf-8")

    assert "[Events]" in content
    assert not _dialogue_lines(content)


# ------------------------------------------------------------- srt / vtt

def test_srt_matches_the_blocks_shown_in_the_video():
    words = _words(["un", "deux", "trois", "quatre"], start=12.0)
    groups = build_captions(words, clip_start=12.0, max_words_per_group=2)

    srt = build_srt(groups)

    assert srt.startswith("1\n00:00:00,000 --> ")
    assert "un deux" in srt
    assert "\n2\n" in srt


def test_vtt_has_the_required_header_and_dot_separator():
    groups = build_captions(_words(["salut", "toi"]), max_words_per_group=2)

    vtt = build_vtt(groups)

    assert vtt.startswith("WEBVTT")
    assert "00:00:00.000 --> " in vtt


def test_write_subtitles_creates_only_the_requested_formats(tmp_path):
    groups = build_captions(_words(["a", "b"]), max_words_per_group=2)

    written = write_subtitles(str(tmp_path), "clip_01", groups, srt=True, vtt=False)

    assert len(written) == 1
    assert (tmp_path / "subtitles" / "clip_01.srt").exists()
    assert not (tmp_path / "subtitles" / "clip_01.vtt").exists()


def test_a_clip_without_words_writes_no_subtitle_file_at_all(tmp_path):
    # Un .srt de zero octet ressemble a un bug ; une absence de fichier se comprend.
    assert write_subtitles(str(tmp_path), "clip_01", [], srt=True, vtt=True) == []
    assert not (tmp_path / "subtitles").exists() or not list((tmp_path / "subtitles").iterdir())
