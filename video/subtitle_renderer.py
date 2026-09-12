"""Genere un fichier .ass a partir des timestamps mot-par-mot de Whisper, puis
l'incruste via le filtre ffmpeg `subtitles=` (libass). Styles definis dans
config/subtitles.json (section 9 du cahier des charges).

Trois modes :
- "progressive" : petits groupes de mots qui s'affichent l'un apres l'autre, en
  grand (ex: "CE TRUC" / "VA" / "CHANGER").
- "classic" : sous-titres par phrase avec surlignage karaoke mot courant
  (tags ASS \\k), plus proche d'un sous-titre traditionnel.
- "smart" : sous-titres intelligents (section 4) -- decoupage adapte a la
  longueur et a la ponctuation, mots importants mis en evidence, placement
  vertical qui evite le visage. Les blocs sont construits en amont par
  editing/captions.py et partages avec l'export .srt/.vtt.

Le decoupage en groupes n'est PAS reimplemente ici : "progressive" et "smart"
appellent tous les deux editing/captions.py, pour qu'il n'existe qu'une seule
regle de regroupement et une seule regle de duree d'affichage.
"""
from __future__ import annotations

from core.models import Word
from editing.captions import CaptionGroup, build_captions

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


def _header(style: dict, margin_v: int | None = None,
            play_res: tuple[int, int] | None = None) -> str:
    """En-tete ASS.

    `play_res` est la toile sur laquelle les coordonnees du style sont
    exprimees. Par defaut 1080x1920, la valeur historique : les styles de
    config/subtitles.json y sont calibres, et le rendu des clips ne doit pas
    changer d'un pixel. Un appelant qui produit une image d'un AUTRE format la
    passe explicitement -- sinon libass etire le texte horizontalement (une
    toile portrait posee sur une image paysage) et les marges ne veulent plus
    rien dire.

    `border_style` vaut 1 (un contour autour des lettres, le defaut
    historique) ou 3 (un rectangle opaque derriere le texte). Les deux etaient
    ecrits en dur, donc inatteignables depuis un style.

    DEUX PIEGES VERIFIES PAR L'EXPERIENCE, contre ce qu'on lit partout :
    - avec `border_style: 3`, libass peint le rectangle avec la couleur de
      CONTOUR (`outline_color`), pas avec `back_color` ;
    - et ce rectangle couvre la LIGNE ENTIERE, pas le mot.
      `back_color` reste la couleur de l'ombre portee.
    Le surlignage au marqueur d'UN SEUL MOT ne passe donc pas par ce 3 : il
    passe par un contour epais pose sur ce mot (`emphasis_marker`, plus bas).
    """
    bold = -1 if style.get("bold", True) else 0
    if margin_v is None:
        margin_v = style.get("margin_v", 300)
    res_x, res_y = play_res if play_res else (1080, 1920)
    return (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {int(res_x)}\n"
        f"PlayResY: {int(res_y)}\n"
        "ScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{style.get('font_name', 'Arial')},{style.get('font_size', 64)},"
        f"{style.get('primary_color', '&H00FFFFFF')},{style.get('highlight_color', '&H0035C3FF')},"
        f"{style.get('outline_color', '&H00000000')},"
        f"{style.get('back_color', '&H00000000')},{bold},0,0,0,100,100,0,0,"
        f"{int(style.get('border_style', 1))},"
        f"{style.get('outline_width', 4)},{style.get('shadow', 1)},{style.get('alignment', 2)},"
        f"20,20,{margin_v},1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )


# Les animations disponibles. « none », « fade » et « pop » existaient deja ;
# les trois suivantes sont les looks du marche qui manquaient.
ANIMATIONS = ("none", "fade", "pop", "bounce", "shake", "glow")


def _emphasis_tags(style: dict) -> tuple[str, str]:
    """Balises ASS ouvrantes/fermantes d'un mot mis en evidence.

    L'animation porte sur LE MOT, jamais sur la ligne entiere : faire trembler
    ou rebondir tout un bloc rend la lecture penible, alors qu'un seul mot qui
    bouge attire l'oeil exactement la ou on le veut.
    """
    font_size = style.get("font_size", 64)
    ratio = float(style.get("emphasis_scale", 1.15))
    color = style.get("emphasis_color", style.get("highlight_color", "&H0035C3FF"))
    animation = style.get("animation", "none")

    tags = f"\\fs{int(round(font_size * ratio))}\\c{color}"

    if animation == "pop":
        # Leger effet d'apparition, borne a ~120 ms : au-dela ca "saute" et ca
        # devient fatigant sur un clip entier.
        tags += "\\t(0,120,\\fscx112\\fscy112)"
    elif animation == "bounce":
        # UN VRAI REBOND, c'est-a-dire un DEPASSEMENT puis un retour. Le
        # « pop » ci-dessus grossit et s'arrete : l'oeil n'y lit pas un choc.
        # Trois etapes -- on entre trop grand, on passe sous la cible, on s'y
        # pose -- donnent la detente d'un ressort.
        haut = int(round(100 * 1.18))
        creux = int(round(100 * 0.94))
        tags += (f"\\fscx{haut}\\fscy{haut}"
                 f"\\t(0,90,\\fscx{creux}\\fscy{creux})"
                 f"\\t(90,190,\\fscx100\\fscy100)")
    elif animation == "shake":
        # Une secousse tient en un aller-retour : deux degres suffisent, et
        # au-dela le mot cesse d'etre lisible.
        tags += "\\frz2\\t(0,70,\\frz-2)\\t(70,140,\\frz0)"
    elif animation == "glow":
        # Le neon : le contour est FLOUTE, pas epaissi. Un contour epais reste
        # net et durcit le texte ; c'est le flou qui donne la lueur.
        tags += f"\\blur{float(style.get('glow_blur', 4)):g}"

    if style.get("emphasis_marker"):
        # LE SURLIGNAGE AU MARQUEUR. Un contour tres epais, de la couleur du
        # marqueur, pose sur CE MOT : libass dessine le contour derriere la
        # lettre, ce qui donne une pastille pleine autour du mot et de lui
        # seul. C'est la seule facon d'obtenir l'effet, verifiee au rendu --
        # `border_style: 3` peindrait la ligne entiere (voir _header).
        marker = style.get("marker_color", style.get("highlight_color", "&H0000D7FF"))
        tags += f"\\bord{float(style.get('marker_width', 14)):g}\\3c{marker}\\shad0"

    return "{" + tags + "}", "{\\r}"


def _line_prefix(style: dict) -> str:
    """Balises posees sur la LIGNE entiere."""
    animation = style.get("animation", "none")
    tags = ""
    if animation in ("fade", "pop", "bounce", "shake", "glow"):
        tags += "\\fad(80,60)"
    if animation == "glow":
        # La lueur vaut aussi pour le reste de la ligne, a demi-force : sans
        # cela le mot important aurait l'air d'appartenir a un autre
        # sous-titre.
        tags += f"\\blur{float(style.get('glow_blur', 4)) / 2.0:g}"
    return "{" + tags + "}" if tags else ""


def _render_groups(groups: list[CaptionGroup], style: dict, with_emphasis: bool) -> str:
    uppercase = style.get("uppercase", True)
    prefix = _line_prefix(style)
    open_tag, close_tag = _emphasis_tags(style)

    lines = []
    for group in groups:
        parts = []
        for word in group.words:
            text = word.text.upper() if uppercase else word.text
            escaped = _escape_ass(text)
            if with_emphasis and word.emphasized:
                parts.append(f"{open_tag}{escaped}{close_tag}")
            else:
                parts.append(escaped)
        body = prefix + " ".join(parts)
        lines.append(
            f"Dialogue: 0,{_format_time(group.start)},{_format_time(group.end)},Default,,0,0,0,,{body}"
        )
    return "\n".join(lines)


def _render_progressive(words: list[Word], clip_start: float, style: dict) -> str:
    groups = build_captions(
        words,
        clip_start=clip_start,
        max_words_per_group=style.get("words_per_group", 2),
        sentence_gap_s=_SENTENCE_GAP_S,
        min_display_s=style.get("min_display_ms", 250) / 1000.0,
        gap_s=style.get("gap_ms", 30) / 1000.0,
    )
    return _render_groups(groups, style, with_emphasis=False)


def _render_classic(words: list[Word], clip_start: float, style: dict) -> str:
    """Karaoke : la phrase entiere reste lisible, le remplissage suit la parole.

    DANS QUEL SENS, exactement -- c'est contre-intuitif et ca se lit a l'envers
    si on se trompe. Le tag ASS \k fait passer le texte de la couleur
    SECONDAIRE a la couleur PRIMAIRE au fur et a mesure. Or l'en-tete de ce
    module met `primary_color` en primaire et `highlight_color` en secondaire.
    Donc : `highlight_color` est la couleur du texte PAS ENCORE DIT, et
    `primary_color` celle du texte DEJA DIT.

    Pour le rendu qu'on attend d'un karaoke -- du texte neutre qui se remplit
    d'une couleur d'accent -- il faut donc mettre l'accent dans
    `primary_color` et le neutre dans `highlight_color`, et non l'inverse.
    """
    uppercase = style.get("uppercase", False)
    # Regroupe uniquement sur les pauses : une "phrase" au sens karaoke.
    sentences = [g for g in build_captions(
        words, clip_start=clip_start, max_words_per_group=9999, sentence_gap_s=_SENTENCE_GAP_S,
    )]

    lines = []
    for group in sentences:
        karaoke_parts = []
        words_in_group = group.words
        for i, w in enumerate(words_in_group):
            if i + 1 < len(words_in_group):
                duration_cs = round((words_in_group[i + 1].start - w.start) * 100)
            else:
                duration_cs = round((w.end - w.start) * 100)
            duration_cs = max(duration_cs, 1)
            text = w.text.upper() if uppercase else w.text
            karaoke_parts.append(f"{{\\k{duration_cs}}}{_escape_ass(text)}")

        text = " ".join(karaoke_parts)
        lines.append(
            f"Dialogue: 0,{_format_time(group.start)},{_format_time(group.end)},Default,,0,0,0,,{text}"
        )
    return "\n".join(lines)


def _render_typewriter(words: list[Word], clip_start: float, style: dict) -> str:
    """Le texte s'ecrit lettre par lettre, comme a la machine.

    POURQUOI C'EST UN MODE ET NON UNE ANIMATION. Les autres animations sont une
    balise posee sur un texte deja ecrit ; celle-ci change le TEXTE a chaque
    pas. Il faut donc un evenement ASS par etape, ce qu'aucune balise ne sait
    faire -- d'ou un mode a part plutot qu'une option de plus.

    LE RYTHME SUIT LA PAROLE, pas une cadence fixe. Les lettres d'un groupe
    sont reparties sur la duree reellement prononcee : un mot dit lentement
    s'ecrit lentement. Une cadence fixe aurait pris de l'avance sur la voix ou
    du retard, et le decalage se voit immediatement.

    Le texte deja ecrit reste affiche : seul le curseur avance. Sans cela on
    lirait un clignotement, pas une frappe.
    """
    groups = build_captions(
        words,
        clip_start=clip_start,
        max_words_per_group=style.get("words_per_group", 3),
        sentence_gap_s=_SENTENCE_GAP_S,
        min_display_s=style.get("min_display_ms", 250) / 1000.0,
        gap_s=style.get("gap_ms", 30) / 1000.0,
    )
    uppercase = style.get("uppercase", True)
    prefix = _line_prefix(style)
    curseur = str(style.get("caret", "") or "")
    # Au-dela d'une dizaine de pas par groupe, on ecrit par paquets de
    # lettres : un evenement par caractere sur un texte long produit des
    # centaines de lignes pour un effet que l'oeil ne distingue plus.
    pas_max = max(2, int(style.get("typewriter_steps", 12)))

    lignes = []
    for group in groups:
        texte = " ".join(w.text for w in group.words)
        if uppercase:
            texte = texte.upper()
        if not texte:
            continue

        duree = max(0.05, group.end - group.start)
        # On ne fait defiler que la duree de FRAPPE : le groupe reste ensuite
        # affiche en entier jusqu'a sa fin.
        frappe = duree * float(style.get("typewriter_ratio", 0.6))
        nombre = min(len(texte), pas_max)
        # -(-a//b) : la division ENTIERE arrondie vers le haut. Arrondie vers
        # le bas, un texte non divisible produisait un pas de plus que demande,
        # et le plafond n'en etait plus un.
        taille = max(1, -(-len(texte) // nombre))

        coupes = list(range(taille, len(texte), taille)) + [len(texte)]
        precedent = group.start
        for index, coupe in enumerate(coupes):
            part = frappe * (index + 1) / len(coupes)
            fin = group.start + part if index < len(coupes) - 1 else group.end
            fin = min(fin, group.end)
            if fin <= precedent:
                continue
            visible = _escape_ass(texte[:coupe])
            queue = curseur if index < len(coupes) - 1 else ""
            lignes.append(
                f"Dialogue: 0,{_format_time(precedent)},{_format_time(fin)},"
                f"Default,,0,0,0,,{prefix}{visible}{_escape_ass(queue)}")
            precedent = fin
    return "\n".join(lignes)


def render_ass_file(
    words: list[Word],
    clip_start: float,
    style: dict,
    out_ass_path: str,
    caption_groups: list[CaptionGroup] | None = None,
    margin_v: int | None = None,
    play_res: tuple[int, int] | None = None,
) -> str:
    """Ecrit out_ass_path et le renvoie. Vide (mais valide) si words est vide,
    pour ne jamais faire echouer ffmpeg a cause d'un clip sans mot detecte.

    `caption_groups` n'est utilise que par le mode "smart" : ce sont les memes
    blocs que ceux exportes en .srt/.vtt, construits une seule fois en amont.
    `margin_v` remplace la marge du style quand le placement intelligent a
    decide de remonter les sous-titres au-dessus du visage.
    """
    mode = style.get("mode", "progressive")

    if mode == "smart":
        groups = caption_groups
        if groups is None:
            # Style "smart" sans blocs fournis (module de sous-titres desactive) :
            # meme decoupage, mais sans mise en evidence ni positionnement.
            groups = build_captions(
                words,
                clip_start=clip_start,
                max_words_per_group=style.get("words_per_group", 4),
                min_display_s=style.get("min_display_ms", 250) / 1000.0,
                gap_s=style.get("gap_ms", 30) / 1000.0,
            )
        body = _render_groups(groups, style, with_emphasis=True)
    elif mode == "classic":
        body = _render_classic(words, clip_start, style)
    elif mode == "typewriter":
        body = _render_typewriter(words, clip_start, style)
    else:
        body = _render_progressive(words, clip_start, style)

    with open(out_ass_path, "w", encoding="utf-8") as f:
        f.write(_header(style, margin_v=margin_v, play_res=play_res))
        f.write(body)
        f.write("\n")
    return out_ass_path


def subtitle_filter(ass_path: str) -> str:
    # ffmpeg attend des ':' et '\' echappes dans le chemin passe au filtre subtitles=.
    escaped = ass_path.replace("\\", "\\\\").replace(":", "\\:")
    return f"subtitles='{escaped}'"
