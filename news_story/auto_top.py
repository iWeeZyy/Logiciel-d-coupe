"""Top news cine du jour prepare automatiquement, chaque matin.

Demande explicite de l'utilisateur : trouver le Top du jour pret au reveil,
sans avoir a ouvrir le logiciel. Le Planificateur de taches de Windows lance
le logiciel avec l'option --daily-top a l'heure choisie ; le logiciel
recupere les news cine (unes des sites en tete), garde les premieres pas
encore publiees, et cree le dossier du Top (images, legende, et la video
narree si elle est demandee) -- exactement ce que fait le bouton « Créer le
top du jour », sans fenetre.

Condition : le PC doit etre allume (pas eteint ni en veille profonde) a
l'heure prevue ; sinon Windows rattrape la tache au demarrage suivant
(option « demarrer des que possible si l'heure est passee »).

Les reglages (dossier, heure, video, voix) sont ceux de la fenetre du Top du
jour, dans QSettings("ClipFarming", "NewsVisuals"), cle « auto_top/… ».
"""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import date
from pathlib import Path

from core.logging_setup import get_logger

logger = get_logger()

TASK_NAME = "ClipFarming - Top news cine du jour"
CLI_FLAG = "--daily-top"
_SETTINGS = ("ClipFarming", "NewsVisuals")


def settings():
    from PySide6.QtCore import QSettings

    return QSettings(*_SETTINGS)


def run(out_root: Path | None = None, *, count: int = 5, video: bool | None = None,
        voice_id: str | None = None, today: date | None = None) -> Path:
    """Cree le Top du jour dans `out_root` (par defaut le dossier choisi dans
    la fenetre) et renvoie son dossier. Leve RuntimeError si rien a faire."""
    from core.config_loader import load_gaming_news_config
    from gaming_news.feed_fetcher import fetch_all_sources
    from gaming_news.sources import load_sources
    from news_story import published
    from news_story.daily_top import compose_daily_top, folder_name, prepare_item

    store = settings()
    out_root = Path(out_root or store.value("auto_top/folder", "") or "")
    if not str(out_root) or not out_root.is_dir():
        raise RuntimeError(f"Dossier du Top automatique introuvable : « {out_root} ».")
    video = store.value("auto_top/video", False, type=bool) if video is None else video
    voice_id = (store.value("top/voice", "") or "") if voice_id is None else voice_id
    today = today or date.today()

    config = load_gaming_news_config()
    scan = config.get("scan", {}) or {}
    sources = [s for s in load_sources(config) if s.theme == "cinema"]
    articles = fetch_all_sources(sources, timeout_s=scan.get("timeout_s", 10),
                                 max_articles=scan.get("max_articles_per_source", 20))
    done = published.load()
    chosen = [a for a in articles if not published.published_on(a.url, done)][:count]
    if not chosen:
        raise RuntimeError("Aucune news ciné nouvelle à mettre dans le Top.")
    items = [prepare_item(article) for article in chosen]
    out_dir = out_root / folder_name(today)

    from gui.radar.daily_top_dialog import _cinema_cta, _cinema_logo

    logo = _cinema_logo()
    written = compose_daily_top(items, out_dir, day=today, cta=_cinema_cta(),
                                logo_path=Path(logo) if logo else None)
    published.mark([a.url for a in chosen], day=today)
    if video:
        from news_story import top_video

        usable = [i for i in items if i.image_path and Path(i.image_path).is_file()]
        shots = top_video.shots_for(written[0], written[1:], usable, today, _cinema_cta())
        try:
            top_video.compose_top_video(shots, out_dir / "top_video.mp4",
                                        voice=top_video.pick_voice(voice_id))
        except Exception as e:  # noqa: BLE001 -- les images restent utilisables
            logger.warning(f"Vidéo du Top automatique impossible : {e}")
    logger.info(f"Top du jour automatique cree : {out_dir}")
    return out_dir


# ------------------------------------------------------------ planification

def launch_command() -> list[str]:
    """Commande que Windows lance : l'executable (version installee) ou
    Python + gui_main.py (depuis le depot)."""
    if getattr(sys, "frozen", False):
        return [sys.executable, CLI_FLAG]
    from core.paths import app_base_dir

    return [sys.executable, str(app_base_dir() / "gui_main.py"), CLI_FLAG]


def _quote(part: str) -> str:
    return f'"{part}"' if " " in part else part


def schtasks_create_args(at: str, command: list[str] | None = None) -> list[str]:
    """Arguments de schtasks pour une tache quotidienne a `at` (« 07:30 »).
    /F remplace une tache existante (changement d'heure). Fonction pure."""
    command = command or launch_command()
    return ["schtasks", "/Create", "/F", "/SC", "DAILY", "/ST", at, "/TN", TASK_NAME,
            "/TR", " ".join(_quote(p) for p in command)]


def schtasks_delete_args() -> list[str]:
    return ["schtasks", "/Delete", "/F", "/TN", TASK_NAME]


def scheduling_available() -> bool:
    return sys.platform == "win32"


def _run(args: list[str]) -> tuple[bool, str]:
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        result = subprocess.run(args, capture_output=True, text=True, creationflags=flags)
    except OSError as e:
        return False, str(e)
    return result.returncode == 0, (result.stderr or result.stdout or "").strip()


def install(at: str) -> tuple[bool, str]:
    """Cree (ou met a jour) la tache planifiee. (succes, message)."""
    if not scheduling_available():
        return False, "La préparation automatique utilise le Planificateur de tâches de Windows."
    ok, detail = _run(schtasks_create_args(at))
    return ok, ("" if ok else f"Planification impossible : {detail}")


def uninstall() -> tuple[bool, str]:
    if not scheduling_available():
        return True, ""
    ok, detail = _run(schtasks_delete_args())
    return ok, ("" if ok else detail)


def main_cli() -> int:
    """Point d'entree de `--daily-top` (sans fenetre). Code 0 si le Top est
    cree ; l'erreur est journalisee sinon."""
    try:
        folder = run()
    except Exception as e:  # noqa: BLE001 -- tache de fond : on journalise
        logger.error(f"Top du jour automatique : {e}")
        return 1
    logger.info(f"Top du jour pret : {folder}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    sys.exit(main_cli())
