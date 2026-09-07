"""Genere un fichier .ass a partir des timestamps mot-par-mot de Whisper, puis
l'incruste via le filtre ffmpeg `subtitles=` (libass). Styles definis dans
config/subtitles.json (section 9 du cahier des charges).

Deux modes :
- "progressive" (defaut) : petits groupes de mots qui s'affichent l'un apres
  l'autre, en grand (ex: "CE TRUC" / "VA" / "CHANGER").
- "classic" : sous-titres par phrase avec surlignage karaoke mot courant
  (tags ASS \\k), plus proche d'un sous-titre traditionnel.
"""
from __future__ import annotations

from core.models import Word

_SENTENCE_GAP_S = 0.6


def _escape_ass(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}").replace("\n", "\\N")


def _format_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    cs_total = round(seconds * 100)
    h = cs_total // 360000
    cs_total %= 360000
    m = cs_total // 6000
    cs_total %= 6000
    s = cs_total // 100
    cs = cs_total % 100
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _header(style: dict) -> str:
    bold = -1 if style.get("bold", True) else 0
    return (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "PlayResX: 1080\n"
        "PlayResY: 1920\n"
        "ScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{style.get('font_name', 'Arial')},{style.get('font_size', 64)},"
        f"{style.get('primary_color', '&H00FFFFFF')},{style.get('highlight_color', '&H0035C3FF')},"
        f"{style.get('outline_color', '&H00000000')},&H00000000,{bold},0,0,0,100,100,0,0,1,"
        f"{style.get('outline_width', 4)},{style.get('shadow', 1)},{style.get('alignment', 2)},"
        f"20,20,{style.get('margin_v', 300)},1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )


def _group_words(words: list[Word], words_per_group: int) -> list[list[Word]]:
    groups: list[list[Word]] = []
    current: list[Word] = []
    for w in words:
        if current and (
            len(current) >= words_per_group
            or (w.start - current[-1].end) > _SENTENCE_GAP_S
        ):
            groups.append(current)
            current = []
        current.append(w)
    if current:
        groups.append(current)
    return groups


def _render_progressive(words: list[Word], clip_start: float, style: dict) -> str:
    words_per_group = style.get("words_per_group", 2)
    uppercase = style.get("uppercase", True)
    min_display = style.get("min_display_ms", 250) / 1000.0
    gap = style.get("gap_ms", 30) / 1000.0

    groups = _group_words(words, words_per_group)
    lines = []
    for i, group in enumerate(groups):
        start = group[0].start - clip_start
        end = group[-1].end - clip_start
        if end - start < min_display:
            end = start + min_display
        if i + 1 < len(groups):
            next_start = groups[i + 1][0].start - clip_start
            end = min(end, next_start - gap)
        end = max(end, start + 0.05)

        text = " ".join(w.text for w in group)
        if uppercase:
            text = text.upper()
        text = _escape_ass(text)
        lines.append(f"Dialogue: 0,{_format_time(start)},{_format_time(end)},Default,,0,0,0,,{text}")
    return "\n".join(lines)


def _render_classic(words: list[Word], clip_start: float, style: dict) -> str:
    uppercase = style.get("uppercase", False)
    sentences = _group_words(words, words_per_group=9999)  # regroupe uniquement sur les pauses

    lines = []
    for sentence in sentences:
        start = sentence[0].start - clip_start
        end = sentence[-1].end - clip_start

        karaoke_parts = []
        for i, w in enumerate(sentence):
            if i + 1 < len(sentence):
                duration_cs = round((sentence[i + 1].start - w.start) * 100)
            else:
                duration_cs = round((w.end - w.start) * 100)
            duration_cs = max(duration_cs, 1)
            text = w.text.upper() if uppercase else w.text
            karaoke_parts.append(f"{{\\k{duration_cs}}}{_escape_ass(text)}")

        text = " ".join(karaoke_parts)
        lines.append(f"Dialogue: 0,{_format_time(start)},{_format_time(end)},Default,,0,0,0,,{text}")
    return "\n".join(lines)


def render_ass_file(words: list[Word], clip_start: float, style: dict, out_ass_path: str) -> str:
    """Ecrit out_ass_path et le renvoie. Vide (mais valide) si words est vide,
    pour ne jamais faire echouer ffmpeg a cause d'un clip sans mot detecte."""
    mode = style.get("mode", "progressive")
    body = _render_progressive(words, clip_start, style) if mode == "progressive" else _render_classic(words, clip_start, style)

    with open(out_ass_path, "w", encoding="utf-8") as f:
        f.write(_header(style))
        f.write(body)
        f.write("\n")
    return out_ass_path


def subtitle_filter(ass_path: str) -> str:
    # ffmpeg attend des ':' et '\' echappes dans le chemin passe au filtre subtitles=.
    escaped = ass_path.replace("\\", "\\\\").replace(":", "\\:")
    return f"subtitles='{escaped}'"
