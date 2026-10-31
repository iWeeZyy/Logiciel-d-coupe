"""Bande-annonce ActuaFilms preparee automatiquement, sans PC.

Demande de l'utilisateur : une bande-annonce a poster tous les 2-3 jours,
selon les sorties, avec les memes reglages que ClipFarming. Lancement :

    python gui_main.py --auto-trailer --out sortie/ [--force]

1. le fil Bandes-annonces (memes sources que l'onglet : AlloCine, chaines
   Netflix, Prime Video, studios…) ;
2. rythme (`decide`) : rien si la derniere bande-annonce a moins de 2 jours ;
   ensuite la plus recente sortie depuis, pas encore publiee ; a partir de
   3 jours sans nouveaute, la plus recente pas encore publiee ;
3. la fenetre « Créer un visuel » est ouverte SANS ECRAN (Qt offscreen) sur
   cette bande-annonce : elle trouve la video, la date de sortie, le
   synopsis, et ses reglages par defaut (modele video 9:16, logo, phrase,
   intro popcorn, sous-titres francais si VO et cle Claude) sont lus tels
   quels -- aucune deuxieme logique ; la video est faite par
   news_story/video_export.py, comme le bouton « Exporter » ;
4. la legende est celle de la fenetre ; elle est ecrite a cote de la video
   et dans le journal.

Memoire de ce qui a deja ete publie : news_story/published.py (le meme
fichier que sur le PC, news_publiees.json) ; le workflow le garde d'une
fois sur l'autre dans le depot prive.
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from core.logging_setup import get_logger

logger = get_logger()

CLI_FLAG = "--auto-trailer"
MIN_GAP_DAYS = 2           # jamais plus d'une bande-annonce tous les 2 jours
MAX_GAP_DAYS = 3           # au-dela, la plus recente pas encore publiee
MAX_TRIES = 5              # bandes-annonces essayees si la video est introuvable
MIN_HEIGHT = 720           # jamais une source floue sur un post 1080x1920


@dataclass
class Decision:
    article: object = None          # None : rien a faire aujourd'hui
    reason: str = ""
    candidates: tuple = ()           # dans l'ordre d'essai


def _published_day(article) -> date | None:
    try:
        when = datetime.fromisoformat((article.published_at or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return (when if when.tzinfo else when.replace(tzinfo=timezone.utc)).date()


def last_post_day(done: dict) -> date | None:
    """Jour de la derniere publication enregistree, ou None."""
    days = []
    for value in (done or {}).values():
        try:
            days.append(date.fromisoformat(str(value)[:10]))
        except ValueError:
            continue
    return max(days) if days else None


def film_key(article) -> str:
    """Le film, sans la version : « RIVER | Bande-annonce VF » et « RIVER |
    Bande-annonce VOST » donnent la meme cle (le meme film n'est jamais poste
    deux fois)."""
    import unicodedata

    from news_story.trailer_title import film_title

    text = unicodedata.normalize("NFKD", film_title(article.title)).encode("ascii", "ignore")
    return " ".join("".join(ch if ch.isalnum() else " " for ch in text.decode().lower()).split())


def _is_vo(article) -> bool:
    return bool(re.search(r"\b(vost|vostfr|vo)\b", (article.title or "").lower()))


def on_youtube(article) -> bool:
    """La video est sur YouTube -- qui refuse les serveurs (« Sign in to
    confirm you're not a bot », constate le 2026-10-31 sur GitHub Actions et
    ailleurs). Les bandes-annonces AlloCine passent par Dailymotion, qui
    sert du 1080p aux serveurs GitHub (verifie le meme jour)."""
    from news_story.image_fetcher import youtube_video_id

    return bool(youtube_video_id(article.url))


def _preference(article) -> tuple:
    """Plus petit = mieux : pas YouTube, puis VF avant VO."""
    return (on_youtube(article), _is_vo(article))


def one_per_film(articles) -> list:
    """Une seule version par film, a la place de la premiere rencontree ;
    AlloCine avant YouTube, la VF avant la VO (pas de traduction a faire)."""
    best: dict = {}
    order: list = []
    for article in articles:
        key = film_key(article) or article.url
        if key not in best:
            best[key] = article
            order.append(key)
        elif _preference(article) < _preference(best[key]):
            best[key] = article
    return [best[key] for key in order]


def decide(articles, done: dict, today: date, force: bool = False,
           skip_youtube: bool = False) -> Decision:
    """Quelle bande-annonce faire aujourd'hui (fonction pure).

    Les plus recentes d'abord ; une bande-annonce sans date passe apres les
    datees. Jamais une deja publiee."""
    from news_story.published import published_on

    posted_films = {film_key(a) for a in articles if published_on(a.url, done)}
    fresh = [a for a in articles
             if not published_on(a.url, done) and film_key(a) not in posted_films]
    fresh.sort(key=lambda a: (_published_day(a) is not None, _published_day(a) or date.min),
               reverse=True)
    fresh = one_per_film(fresh)
    if skip_youtube:
        fresh = [a for a in fresh if not on_youtube(a)]
    if not fresh:
        return Decision(reason="Aucune bande-annonce nouvelle dans le fil.")
    last = last_post_day(done)
    if force or last is None:
        return Decision(fresh[0], "Essai forcé." if force else "Première bande-annonce.",
                        tuple(fresh[:MAX_TRIES]))
    gap = (today - last).days
    if gap < MIN_GAP_DAYS:
        return Decision(reason=f"Dernière bande-annonce il y a {gap} jour(s) : "
                               f"on attend {MIN_GAP_DAYS} jours.")
    released = [a for a in fresh if (_published_day(a) or date.min) > last]
    if released:
        return Decision(released[0], f"Nouvelle bande-annonce sortie depuis le {last}.",
                        tuple(released[:MAX_TRIES]))
    if gap >= MAX_GAP_DAYS:
        return Decision(fresh[0], f"{gap} jours sans nouveauté : la plus récente pas encore "
                                  "publiée.", tuple(fresh[:MAX_TRIES]))
    return Decision(reason=f"Pas de nouvelle bande-annonce depuis le {last} ; "
                           f"on attend jusqu'à {MAX_GAP_DAYS} jours.")


def fetch_trailers() -> list:
    """Le fil Bandes-annonces, comme l'onglet du logiciel."""
    from core.config_loader import load_gaming_news_config
    from gaming_news.feed_fetcher import fetch_all_sources
    from gaming_news.sources import load_sources

    config = load_gaming_news_config()
    scan = config.get("scan", {}) or {}
    sources = [s for s in load_sources(config) if s.theme == "trailers"]
    return fetch_all_sources(sources, timeout_s=scan.get("timeout_s", 10),
                             max_articles=scan.get("max_articles_per_source", 20))


def window_job(article, wait_s: float = 120.0):
    """(reglages de l'export video, legende) : ceux de la fenetre « Créer un
    visuel » ouverte sur cette bande-annonce, sans ecran. reglages["video_url"]
    vide si l'article n'a pas de video."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from gui.radar.story_dialog import StoryDialog

    app = QApplication.instance() or QApplication([])
    dialog = StoryDialog(article, theme="trailers")
    try:
        thread = dialog._fetch_thread
        if thread is not None:
            thread.wait(int(wait_s * 1000))
        for _ in range(5):                       # signaux en file d'attente
            app.processEvents()
        return dialog.video_job(), dialog.caption_edit.toPlainText()
    finally:
        dialog.cleanup()
        dialog.deleteLater()
        app.processEvents()


def produce(article, out_dir: Path, on_status=logger.info):
    """(video, legende .txt, texte de la legende), ou None si l'article n'a
    pas de video."""
    from news_story import published
    from news_story.video_export import export_video
    from radar.top_compilation import safe_name

    job, caption = window_job(article)
    if not job.get("video_url"):
        return None
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = safe_name(article.title)[:90]
    video = out_dir / f"{stem}.mp4"
    kwargs = {k: v for k, v in job.items() if k != "video_url"}
    export_video(job["video_url"], str(video), on_status=on_status, min_height=MIN_HEIGHT,
                 **kwargs)
    text_path = video.with_suffix(".txt")
    text_path.write_text(caption, encoding="utf-8")
    published.mark([article.url])                # comme le bouton « Exporter »
    return video, text_path, caption


def run(out_dir: Path, *, force: bool = False, today: date | None = None,
        fetch=fetch_trailers, producer=produce, on_status=logger.info,
        skip_youtube: bool = False) -> dict:
    """{"status": "produit"|"rien"|"erreur", "reason", "title", "video",
    "caption"}. Ne leve pas."""
    from news_story import published

    today = today or date.today()
    try:
        articles = fetch()
    except Exception as e:  # noqa: BLE001 -- rapporte
        return {"status": "erreur", "reason": f"Fil Bandes-annonces illisible : {e}"}
    decision = decide(articles, published.load(), today, force=force,
                      skip_youtube=skip_youtube)
    on_status(decision.reason)
    if decision.article is None:
        return {"status": "rien", "reason": decision.reason}
    errors = []
    for article in decision.candidates:
        on_status(f"Bande-annonce : {article.title} ({article.source_label}, "
                  f"{article.published_at or 'date inconnue'})")
        try:
            made = producer(article, Path(out_dir), on_status=on_status)
        except Exception as e:  # noqa: BLE001 -- on essaie la suivante
            logger.exception(f"Bande-annonce impossible : {article.title}")
            errors.append(f"{article.title} : {e}")
            continue
        if made is None:
            errors.append(f"{article.title} : pas de vidéo trouvée")
            continue
        video, _text_path, caption = made
        # Les autres versions du meme film (VF/VOST, autre chaine) : deja
        # « publiees » elles aussi, pour ne jamais reposter ce film.
        same = [a.url for a in articles if film_key(a) == film_key(article)]
        published.mark(same)
        return {"status": "produit", "reason": decision.reason, "title": article.title,
                "video": str(video), "caption": caption, "skipped": errors}
    return {"status": "erreur", "reason": "Aucune bande-annonce n'a pu être faite : "
                                          + " ; ".join(errors)}


def main_cli(argv=None) -> int:
    """`--auto-trailer` : 0 si une bande-annonce a ete faite OU s'il n'y avait
    rien a faire aujourd'hui (le rythme), 1 en cas d'echec. Sous GitHub
    Actions, `produit=true|false` est ecrit dans GITHUB_OUTPUT."""
    import argparse

    parser = argparse.ArgumentParser(prog=f"gui_main.py {CLI_FLAG}")
    parser.add_argument("--out", type=Path, default=Path("bande_annonce"))
    parser.add_argument("--force", action="store_true",
                        help="ignorer le rythme (essai) ; jamais une deja publiee")
    parser.add_argument("--sans-youtube", action="store_true",
                        help="ecarter les videos YouTube (serveur : YouTube les bloque)")
    args = parser.parse_args([a for a in (argv if argv is not None else sys.argv[1:])
                              if a != CLI_FLAG])
    result = run(args.out, force=args.force, skip_youtube=args.sans_youtube)
    print(f"Résultat : {result['status']} — {result.get('reason', '')}", flush=True)
    for line in result.get("skipped") or []:
        print(f"⚠ écartée : {line}", flush=True)
    if result["status"] == "produit":
        print(f"===== LÉGENDE =====\n{result['caption']}\n===== FIN LÉGENDE =====", flush=True)
    if os.environ.get("GITHUB_ACTIONS") == "true":
        output = os.environ.get("GITHUB_OUTPUT")
        if output:
            with open(output, "a", encoding="utf-8") as f:
                f.write(f"produit={'true' if result['status'] == 'produit' else 'false'}\n")
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a", encoding="utf-8") as f:
                f.write(f"## Bande-annonce ActuaFilms\n\n{result['status']} — "
                        f"{result.get('reason', '')}\n")
                if result["status"] == "produit":
                    f.write(f"\n**{result['title']}**\n\n```\n{result['caption']}\n```\n")
        if result["status"] == "erreur":
            print(f"::error::{result['reason']}", flush=True)
    return 1 if result["status"] == "erreur" else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main_cli())
