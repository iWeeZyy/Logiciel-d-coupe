"""« Top news ciné du jour » : plusieurs news cinema en un seul export.

Demande explicite de l'utilisateur : chaque jour, plusieurs news cinema en un
meme telechargement, a poster ensemble dans un seul TikTok (mode photo) ou
carrousel Instagram. Que des images, que du cinema.

Un export = un dossier :
    00_couverture.png     « TOP NEWS CINÉ » + date, fond tire de la 1re news
    01_….png … NN_….png   une image 9:16 par news (le post 9:16 habituel :
                          titre + chapo, ou reponse a la question du titre),
                          etiquette « NEWS 1/5 »
    legende.txt           la legende du post, prete a copier : une ligne par
                          news, puis les sources

Rien de nouveau dans le rendu des news elles-memes : chaque image passe par
story_composer.compose_story (gabarit post 9:16), exactement comme un visuel
fait a la main -- seule la couverture est propre a ce module.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from core.logging_setup import get_logger

logger = get_logger()

W, H = 1080, 1920
_MONTHS = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
           "septembre", "octobre", "novembre", "décembre")
# Zones sures TikTok / Reels, comme le post 9:16 (post_composer) : marge des
# boutons reprise a gauche, texte centre sur l'ecran.
_MARGIN_LEFT, _MARGIN_RIGHT = 140, 140
_SAFE_TOP, _SAFE_BOTTOM = int(H * 0.10), H - 440
DEFAULT_COUNT = 5


@dataclass
class TopItem:
    """Une news du top : l'article, son image (deja telechargee) et le texte
    sous le titre (chapo, ou reponse a la question du titre)."""

    article: object
    image_path: Path | None
    subtitle: str = ""
    ai_error: str = ""        # resume Claude impossible (cle refusee...) : message


def french_date(day: date) -> str:
    return f"{day.day} {_MONTHS[day.month - 1]} {day.year}"


def _slug(text: str, max_len: int = 40) -> str:
    import unicodedata

    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return text[:max_len].rstrip("-") or "news"


def folder_name(day: date) -> str:
    return f"Top news ciné {day.isoformat()}"


def build_daily_caption(items: list[TopItem], day: date) -> str:
    """Legende du post : titre de la serie, une entree numerotee par news
    (titre puis texte sous le titre), puis les sources. Rien d'invente."""
    lines = [f"🎬 Top news ciné du {french_date(day)}", ""]
    sources: list[str] = []
    for i, item in enumerate(items, 1):
        title = " ".join(item.article.title.split())
        lines.append(f"{i}. {title}")
        if item.subtitle:
            lines.append(item.subtitle)
        lines.append("")
        label = item.article.source_label
        if label and label not in sources:
            sources.append(label)
    if sources:
        lines.append("Sources : " + ", ".join(sources))
    return "\n".join(lines).strip() + "\n"


def compose_cover(out_path, *, day: date, count: int, background: Path | None = None,
                  logo_path: Path | None = None, cta: str = "") -> Path:
    """Couverture 9:16 : « TOP NEWS CINÉ », la date, le nombre de news, le
    logo -- sur l'image de la premiere news floutee et assombrie (ou un fond
    sombre uni si elle manque)."""
    from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

    from news_story.post_composer import _font, _load_logo, _subtitle_font

    out_path = Path(out_path)
    canvas = Image.new("RGB", (W, H), (14, 14, 18))
    if background is not None and Path(background).is_file():
        try:
            with Image.open(background) as raw:
                img = raw.convert("RGB")
            scale = max(W / img.width, H / img.height)
            img = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))),
                             Image.LANCZOS)
            left, top = (img.width - W) // 2, (img.height - H) // 2
            img = img.crop((left, top, left + W, top + H)).filter(ImageFilter.GaussianBlur(18))
            canvas = ImageEnhance.Brightness(img).enhance(0.45)
        except OSError as e:
            logger.warning(f"Fond de couverture illisible : {e}")

    draw = ImageDraw.Draw(canvas)
    center_x = (_MARGIN_LEFT + W - _MARGIN_RIGHT) / 2
    white, black, accent = (255, 255, 255), (0, 0, 0), (224, 162, 76)

    def centered(text, font, y, fill=white, shadow=4):
        x = center_x - draw.textlength(text, font=font) / 2
        draw.text((x + shadow, y + shadow), text, font=font, fill=black)
        draw.text((x, y), text, font=font, fill=fill)

    # « TOP NEWS » remplit la largeur sans deborder des marges (sous les
    # boutons TikTok a droite).
    max_w = W - _MARGIN_LEFT - _MARGIN_RIGHT
    size = 230
    while size > 120 and draw.textlength("TOP NEWS", font=_font(size)) > max_w:
        size -= 4
    big, mid = _font(size), _font(120)
    date_font, info_font = _subtitle_font(64), _subtitle_font(52)
    lines = [("TOP NEWS", big, white), ("CINÉ", mid, accent)]
    heights = [sum(f.getmetrics()) for _, f, _ in lines]
    block_h = sum(heights) + 40 + sum(date_font.getmetrics()) + 24 + sum(info_font.getmetrics())
    y = int(_SAFE_TOP + (_SAFE_BOTTOM - _SAFE_TOP - block_h) / 2) - 60
    for (text, font, fill), h in zip(lines, heights):
        centered(text, font, y, fill)
        y += h
    y += 40
    centered(french_date(day).upper(), date_font, y, shadow=2)
    y += sum(date_font.getmetrics()) + 24
    centered(f"{count} infos ciné à ne pas rater", info_font, y, shadow=2)

    # Bas : logo, puis la phrase d'appel dessous (comme sur chaque news).
    from news_story.video_composer import (_CTA_LINE_HEIGHT, _CTA_SIZE, _cta_block, _draw_rich,
                                           _rich_width)

    cta_font, cta_lines, cta_h = _cta_block(draw, cta, W - _MARGIN_LEFT - _MARGIN_RIGHT)
    y = _SAFE_BOTTOM - cta_h
    logo = _load_logo(logo_path)
    if logo is not None:
        canvas.paste(logo, (int(center_x - logo.width / 2), y - (14 if cta_h else 0) - logo.height),
                     logo)
    if cta_lines:
        layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        ldraw = ImageDraw.Draw(layer)
        for line in cta_lines:
            x = center_x - _rich_width(ldraw, line, cta_font, _CTA_SIZE) / 2
            _draw_rich(layer, ldraw, ldraw, (x, y), line, cta_font, _CTA_SIZE, (255, 255, 255, 255),
                       (0, 0, 0, 255))
            y += int(_CTA_SIZE * _CTA_LINE_HEIGHT)
        canvas = Image.alpha_composite(canvas.convert("RGBA"), layer).convert("RGB")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path, format="PNG")
    return out_path


def compose_daily_top(items: list[TopItem], out_dir, *, day: date | None = None,
                      logo_path: Path | None = None, on_progress=None,
                      cta: str | None = None) -> list[Path]:
    """Ecrit la couverture, une image par news et legende.txt dans `out_dir`.
    Une news sans image est ignoree (jamais une image vide). Renvoie les
    images ecrites, dans l'ordre du carrousel."""
    from news_story.story_composer import StoryOptions, compose_story
    from news_story.story_templates import TEMPLATE_POST_VERTICAL

    from news_story.video_composer import DEFAULT_CTA

    day = day or date.today()
    cta = DEFAULT_CTA["cinema"] if cta is None else cta
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    usable = [item for item in items if item.image_path and Path(item.image_path).is_file()]
    written: list[Path] = []
    written.append(compose_cover(out_dir / "00_couverture.png", day=day, count=len(usable),
                                 background=usable[0].image_path if usable else None,
                                 logo_path=logo_path, cta=cta))
    for i, item in enumerate(usable, 1):
        if on_progress:
            on_progress(i, len(usable))
        options = StoryOptions(
            template=TEMPLATE_POST_VERTICAL, title=item.article.title,
            summary=getattr(item.article, "summary", ""),
            source_label=item.article.source_label, label=f"NEWS {i}/{len(usable)}",
            branding_enabled=logo_path is not None,
            branding_path=str(logo_path) if logo_path else None,
            subtitle=item.subtitle, cta=cta)
        path = out_dir / f"{i:02d}_{_slug(item.article.title)}.png"
        compose_story(item.image_path, path, options)
        written.append(path)
    (out_dir / "legende.txt").write_text(build_daily_caption(usable, day), encoding="utf-8")
    return written


def prepare_item(article) -> TopItem:
    """Telecharge l'image de l'article et choisit le texte sous le titre :
    la reponse a la question du titre quand c'en est une, sinon le chapo.
    Reseau ; jamais d'exception (une news sans image sera simplement ignoree)."""
    from news_story.article_text import answer_from_article, is_teaser, page_chapo
    from news_story.image_cache import ImageFetchError, download
    from news_story.image_fetcher import candidates_for_article, fetch_article_html
    from news_story.post_composer import clean_subtitle
    from news_story.story_composer import default_subtitle

    image_path = None
    try:
        for candidate in candidates_for_article(article.url, article.feed_image_url)[:4]:
            try:
                image_path = Path(download(candidate.url).path)
                break
            except ImageFetchError:
                continue
    except Exception as e:  # noqa: BLE001 -- une news en echec ne bloque pas le top
        logger.warning(f"Image introuvable pour « {article.title} » : {e}")

    try:
        page = fetch_article_html(article.url) or ""
    except Exception:  # noqa: BLE001 -- sans page, le resume du flux suffit
        page = ""
    # Le chapo de la page quand elle en declare un assez long, sinon le
    # resume du flux.
    chapo = page_chapo(page)
    chapo = chapo if len(chapo) >= 60 and not chapo.endswith("...") else article.summary
    subtitle = default_subtitle(article.title, chapo)
    # Claude lit l'article et redige l'info principale (et la reponse au
    # titre) ; sans cle ou en erreur, extraction de phrases de l'article.
    from news_story import ai_summary

    ai_error = ""
    if page and ai_summary.is_configured():
        try:
            summary = ai_summary.summarize(article.title, clean_subtitle(chapo), page,
                                           url=article.url)
        except ai_summary.AiSummaryError as error:
            ai_error = str(error)
        else:
            return TopItem(article=article, image_path=image_path, subtitle=summary.text)
    if is_teaser(article.title, chapo) and page:
        answer = answer_from_article(article.title, page, chapo=clean_subtitle(chapo))
        if answer:
            subtitle = answer
    return TopItem(article=article, image_path=image_path, subtitle=subtitle, ai_error=ai_error)
