"""Compilation des clips Twitch les plus vus -- la logique, sans fenetre.

Utilisee par la fenetre « 🏆 Compilation des plus vus » du Radar
(gui/radar/auto_compilation_dialog.py) ET par la compilation automatique de
la semaine (radar/weekly_top.py, lancee sans ecran par GitHub Actions). Une
seule logique : la compilation du dimanche est exactement celle qu'on aurait
faite a la main.

1. choix (radar/compilation_pick.py) : les N clips les plus vus de la
   periode, un seul par moment ;
2. chaque clip est telecharge puis traite par le pipeline habituel (format,
   sous-titres, filigrane…), SANS l'intro « Follow » -- elle est posee une
   seule fois par la compilation ; chaque clip devient un projet normal de
   « Mes projets » ;
3. montage (video/compilation.py) : numeros, transitions, intro, ecran de
   fin, 1 ou 2 parties, legende .txt a cote.

Aucun import de Qt ici : c'est ce qui permet de tourner sur un serveur.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace


def default_title(count: int, creator_label: str, period_label: str) -> str:
    who = f" de {creator_label}" if creator_label else ""
    return f"Top {count} des moments{who} {period_label}"


def safe_name(name: str) -> str:
    """Nom de fichier sans caractere interdit par Windows."""
    name = " ".join((name or "").split()) or "Compilation"
    return "".join(ch for ch in name if ch not in '<>:"/\\|?*').strip() or "Compilation"


# Reels Instagram : au-dela de 3 minutes, plus recommandes aux nouvelles
# audiences. L'utilisateur fixe 2 min 30 ; jamais un clip coupe ; 15 s ou plus
# au-dela, on lui demande (2026-10-31).
MAX_TOTAL_S = 150.0
ASK_ABOVE_S = 15.0          # depassement a partir duquel on demande
MIN_CLIPS = 3               # un top 3 ; davantage seulement si les clips sont courts
UNKNOWN_CLIP_S = 60.0       # duree max d'un clip Twitch, si elle n'est pas connue


def clip_length(opportunity) -> float:
    return float(opportunity.duration_s or UNKNOWN_CLIP_S)


def fit_count(opportunities, budget: float = MAX_TOTAL_S, minimum: int = MIN_CLIPS) -> list:
    """Les clips retenus, ENTIERS, dans l'ordre du classement : les
    `minimum` premiers, puis les suivants tant que le total tient dans
    `budget` (fonction pure). Un clip qui ne tient pas arrete la liste -- on
    ne saute pas a un suivant plus court mais moins vu, ce ne serait plus un
    top."""
    kept, total = [], 0.0
    for index, opportunity in enumerate(opportunities):
        length = clip_length(opportunity)
        if index >= minimum and total + length > budget:
            break
        kept.append(opportunity)
        total += length
    return kept


def total_length(opportunities) -> float:
    return sum(clip_length(o) for o in opportunities)


def candidates(store, creator_key: str | None, days: int, now=None, limit: int = 300):
    """Clips Twitch de la periode, les plus vus d'abord."""
    from radar.display import ORDER_VIEWS

    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(days=days)).isoformat(timespec="seconds").replace("+00:00", "Z")
    return store.list_opportunities(platform="twitch", creator_key=creator_key or None,
                                    since=since, kinds=("clip",), order=ORDER_VIEWS,
                                    limit=limit)


def select(store, creator_key: str | None, days: int, count: int, now=None):
    """(clips retenus, doublons ecartes) -- radar/compilation_pick.pick_top."""
    from radar.compilation_pick import pick_top

    return pick_top(candidates(store, creator_key, days, now=now), count)


def produce_clip(opportunity, production: dict, cancel_token, on_status=lambda m: None,
                 *, projects_dir: Path | None = None, clips_dir: Path | None = None) -> str:
    """Telecharge puis traite UN clip par le pipeline habituel ; renvoie le
    fichier produit. Meme preparation que la fenetre d'analyse du Radar.
    `projects_dir`/`clips_dir` : par defaut ceux des Parametres."""
    import pipeline
    from core.config_loader import load_settings
    from editing.overrides import apply_editing_overrides
    from gui import settings_store
    from projects import store as project_store
    from radar.analysis import media
    from radar.bridge import _safe_project_name
    from video.filter_graph import FIT_CROP, FIT_SPLIT_WEBCAM

    title = opportunity.title or opportunity.content_id
    on_status(f"Téléchargement : {title}")
    source = media.resolve(opportunity, cancel_token=cancel_token,
                           download_dir=str(clips_dir) if clips_dir else None)
    source_path = source.path
    fit_mode = production.get("fit_mode")
    if fit_mode == FIT_SPLIT_WEBCAM:
        # Exactement comme un clip seul (gui/controller.py, AnalysisThread) :
        # « Webcam en haut + Gameplay en bas » = la version PORTRAIT que
        # Twitch genere lui-meme, puis le cadrage classique dessus -- notre
        # propre decoupage en deux bandes cadrait la webcam trop large.
        # Sans cette version pour ce clip, cadrage classique sur le paysage.
        on_status(f"Version portrait Twitch : {title}")
        source_path = portrait_variant(opportunity, cancel_token, clips_dir=clips_dir) \
            or source_path
        fit_mode = FIT_CROP
    name = _safe_project_name(getattr(opportunity, "title", "") or opportunity.content_id)
    folder = project_store.create_project_folder(
        name, projects_dir=projects_dir or settings_store.projects_dir())
    duration = opportunity.duration_s
    cli_args = SimpleNamespace(
        input=source_path, output=str(folder), overwrite=True,
        clip_duration=max(5, int(duration)) if duration else 45, nb_clips=1, whole_source=True,
        model=settings_store.get("default_model") or "small", language=None,
        pre_roll=None, post_roll=None, min_gap=None,
        subtitle_style=production.get("subtitle_style") or settings_store.get("default_subtitle_style"),
        device=settings_store.get("default_device"), no_cache=False, debug_scores=False,
        aspect=production.get("aspect"), fill_mode=production.get("fill_mode"),
        fit_mode=fit_mode,
    )
    settings = load_settings(cli_args)
    overrides = dict(production.get("editing_overrides") or {})
    if overrides.get("hook_text", True) and getattr(opportunity, "title", ""):
        # « Texte en haut » : le titre donne au clip sur Twitch est un
        # candidat (video/hook_text.choose_text).
        current = overrides.get("hook_text", True)
        block = dict(current) if isinstance(current, dict) else {"enabled": bool(current)}
        block["preferred"] = opportunity.title
        overrides["hook_text"] = block
    apply_editing_overrides(settings, overrides)
    project_store.write_manifest(
        folder, name=name, source_label=title, source_kind="local",
        settings_used={
            "clip_duration": settings.clip_duration, "nb_clips": settings.nb_clips,
            "model": settings.model, "language": settings.language,
            "subtitle_style": settings.subtitle_style, "device": settings.device,
            "editing_modules": {key: bool(block.get("enabled", False))
                                for key, block in settings.editing.items()
                                if isinstance(block, dict) and "enabled" in block},
        },
        source_url=opportunity.url or None)
    on_status(f"Montage du clip : {title}")
    results = pipeline.run(settings, cancel_token=cancel_token)
    if not results:
        raise RuntimeError(f"Aucun clip produit pour « {title} ».")
    return str(Path(folder) / results[0].file_name)


def portrait_variant(opportunity, cancel_token, clips_dir: Path | None = None) -> str | None:
    """La version portrait (webcam en haut, jeu en bas) que Twitch genere pour
    ce clip, ou None s'il n'en a pas. Rangee dans un sous-dossier : le rendu
    paysage du meme clip porte le meme nom de fichier, et yt-dlp le
    prendrait pour la version deja telechargee."""
    from core.logging_setup import get_logger
    from radar.analysis import media
    from radar.clip_download import download_clip
    from utils.errors import CancelledError
    from video.ffmpeg_utils import video_resolution

    try:
        path = download_clip(opportunity.url or opportunity.content_id,
                             str(Path(clips_dir or media.clips_dir()) / "portrait"),
                             cancel_token=cancel_token, prefer_portrait=True)
        width, height = video_resolution(path)
    except CancelledError:
        raise
    except Exception as error:  # noqa: BLE001 -- repli : cadrage classique sur le paysage
        get_logger().warning(f"Version portrait indisponible ({error}).")
        return None
    return path if height > width else None


def clip_label(clip_path: str, opportunity=None, max_words: int = 9) -> str:
    """Le texte affiche sous le numero d'un clip : les MEMES regles que le
    bandeau des clips seuls (video/hook_text.choose_text) -- une vraie
    question posee dans le clip, sinon le titre Twitch du clip (3 mots ou
    plus), sinon une phrase dite d'au moins 5 mots sans petit mot ampute en
    fin ; rien sinon. Extraction, jamais invention. C'est le texte unique
    qu'Instagram demande pour ne pas classer le compte en simple
    republication (2026-11-01). Avant : la phrase dite passait d'abord,
    meme mal transcrite (« Mon écompte d'Abiens habillé sur la L »)."""
    import json

    from video.hook_text import choose_text

    try:
        results = json.loads((Path(clip_path).parent.parent / "results.json")
                             .read_text(encoding="utf-8"))
        titles = (results[0].get("metadata") or {}).get("titles") or []
    except (OSError, ValueError, IndexError, AttributeError):
        titles = []
    title = getattr(opportunity, "title", "") if opportunity is not None else ""
    return choose_text(titles, max_words, preferred=title or "").strip(" .")


def build(opportunities, production: dict, out_dir, *, name: str, parts: int = 1,
          countdown: bool = True, intro: bool = True, transitions: bool = True,
          cancel_token=None, on_status=lambda m: None, projects_dir: Path | None = None,
          clips_dir: Path | None = None, clip_titles: bool = False,
          ask_next: bool = False, streamer: str = "") -> list:
    """Produit chaque clip puis monte la compilation ; [(chemin, duree)] des
    parties ecrites. L'intro par clip est toujours coupee : la compilation
    la pose une seule fois au debut. `streamer` (tops de la semaine) : chaque
    clip porte le design des clips seuls -- badge neon « REPLAY » + « DE
    <STREAMER> », son numero « #N » sur la meme ligne et sa phrase dans le
    bandeau dessous (demande du 2026-11-01) -- au lieu de la pastille et de
    la petite phrase de la compilation."""
    from video.compilation import compose
    from video.hook_text import INTRO_SECONDS, replay_label

    production = dict(production)
    overrides = dict(production.get("editing_overrides") or {})
    overrides["intro"] = False
    # Dans une compilation, la phrase de chaque clip est sous son numero
    # (clip_label) : pas de second texte en haut.
    overrides["hook_text"] = False
    if clip_titles:
        # Pour la phrase affichee sous chaque numero (clip_label) ; ce module
        # ne produit que du texte, le rendu du clip n'en depend pas.
        overrides["metadata"] = True
    production["editing_overrides"] = overrides
    # Classes du plus vu au moins vu ; en compte a rebours le #1 passe en
    # DERNIER (video/compilation.numbering) : on inverse, sinon le clip le
    # plus vu ouvrait la video en « #3 » (constate le 2026-10-31).
    opportunities = list(opportunities)
    if countdown:
        opportunities.reverse()
    paths = []
    total = len(opportunities)
    for index, opportunity in enumerate(opportunities, 1):
        clip_production = production
        if streamer:
            number = total - index + 1 if countdown else index
            block = {"enabled": True, "tag": streamer, "badge_label": replay_label(streamer),
                     "number": number,
                     # L'intro « Follow » de la compilation passe sur le 1er clip.
                     "avoid_intro_s": INTRO_SECONDS if index == 1 and intro else 0.0}
            if index == 1:
                # Titre ephemere en neon au-dessus du 1er numero (2026-11-01).
                block["headline"] = [f"TOP {total}",
                                     f"DES CLIPS {replay_label(streamer)} CETTE SEMAINE"]
            clip_production = {**production,
                               "editing_overrides": {**overrides, "hook_text": block}}
        paths.append(produce_clip(
            opportunity, clip_production, cancel_token,
            lambda message, i=index: on_status(f"Clip {i}/{total} — {message}"),
            projects_dir=projects_dir, clips_dir=clips_dir))
    from video.compilation import ASK_NEXT_TEXT

    labels = ([clip_label(path, o) for path, o in zip(paths, opportunities)]
              if clip_titles and not streamer else None)
    return compose(paths, out_dir, name=name, parts=parts, countdown=countdown, labels=labels,
                   end_text=ASK_NEXT_TEXT if ask_next else None,
                   intro=intro, transitions=transitions, numbers=not streamer,
                   on_progress=on_status, cancel_token=cancel_token)


def write_captions(written, title: str, creator: str = "", ask_next: bool = False) -> list[Path]:
    """La legende .txt a cote de chaque partie ; renvoie leurs chemins."""
    from video.compilation import caption_text

    title = " ".join((title or "").split())
    captions = []
    for index, (path, _duration) in enumerate(written, 1):
        caption = Path(path).with_suffix(".txt")
        caption.write_text(caption_text(title, index, len(written), creator=creator,
                                        ask_next=ask_next),
                           encoding="utf-8")
        captions.append(caption)
    return captions
