#!/usr/bin/env python3
"""CLI de clip_farming -- transforme une video longue en clips courts 9:16
optimises pour Instagram Reels, 100% local (voir README.md).

Trois sources possibles, mutuellement exclusives : --input (fichier local,
inchange depuis la V1), --youtube (telecharge puis traite), --search
(recherche seule, n'affiche que des resultats, ne traite rien).

Exemples :
    python main.py --input video.mp4 --clip-duration 45 --nb-clips 5
    python main.py --search "podcast entrepreneuriat francais" --sort potential
    python main.py --youtube dQw4w9WgXcQ --confirm-rights --nb-clips 5
"""
from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import traceback

import pipeline
from core.config_loader import load_settings, load_youtube_config
from core.logging_setup import get_logger
from core.migrate_user_data import migrate_legacy_user_data
from utils.errors import ClipFarmingError

logger = get_logger()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description=(
            "Transforme une video longue en plusieurs clips courts 9:16 "
            "optimises pour Instagram Reels -- 100%% local, sans API externe "
            "pour le traitement (la recherche YouTube, optionnelle, a besoin "
            "d'internet -- voir README.md)."
        ),
        epilog=(
            "Exemples :\n"
            "  python main.py --input video.mp4\n"
            "  python main.py --input video.mp4 --clip-duration 45 --nb-clips 5\n"
            "  python main.py --input video.mp4 --model medium --device cuda\n"
            "  python main.py --search \"podcast entrepreneuriat francais\" --sort potential\n"
            "  python main.py --youtube dQw4w9WgXcQ --confirm-rights --nb-clips 5\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    source = parser.add_argument_group("Source (un seul des trois)")
    source.add_argument("--input", default=None, help="Chemin de la video source locale.")
    source.add_argument("--youtube", default=None,
                         help="ID ou URL YouTube a telecharger puis traiter (necessite --confirm-rights).")
    source.add_argument("--search", default=None,
                         help="Recherche YouTube seule (n'affiche que des resultats, ne traite rien).")
    source.add_argument("--confirm-rights", dest="confirm_rights", action="store_true",
                         help="Confirme disposer des droits necessaires -- obligatoire pour --youtube.")

    parser.add_argument("--output", default=None, help="Dossier de sortie (defaut: output/).")
    parser.add_argument("--clip-duration", dest="clip_duration", type=int, default=None,
                         help="Duree cible d'un clip en secondes (defaut: 45).")
    parser.add_argument("--nb-clips", dest="nb_clips", type=int, default=None,
                         help="Nombre de clips a generer (defaut: 5).")
    parser.add_argument("--language", default=None,
                         help="Code langue ISO (ex: fr, en). Omis = detection automatique.")
    parser.add_argument("--pre-roll", dest="pre_roll", type=int, default=None,
                         help="Secondes de contexte ajoutees avant le hook (defaut: 5).")
    parser.add_argument("--post-roll", dest="post_roll", type=int, default=None,
                         help="Secondes de contexte ajoutees apres le hook (defaut: 3).")
    parser.add_argument("--min-gap", dest="min_gap", type=int, default=None,
                         help="Distance minimale en secondes entre deux clips (defaut: 20).")
    parser.add_argument("--subtitle-style", dest="subtitle_style", default=None,
                         help="Style de sous-titres (voir config/subtitles.json ; defaut: progressive).")
    parser.add_argument("--model", default=None,
                         help="Modele Whisper : tiny|base|small|medium|large-v3 (defaut: small).")
    parser.add_argument("--device", default=None, help="cpu|cuda|auto (defaut: auto).")
    parser.add_argument("--overwrite", action="store_true",
                         help="Ecrase les clips existants dans le dossier de sortie.")
    parser.add_argument("--no-cache", dest="no_cache", action="store_true",
                         help="Ignore/n'ecrit pas le cache de transcription (.cache/).")
    parser.add_argument("--debug-scores", dest="debug_scores", action="store_true",
                         help="Affiche le detail des scores de chaque clip retenu.")
    parser.add_argument("--verbose", action="store_true",
                         help="Affiche la trace complete en cas d'erreur inattendue.")

    production = parser.add_argument_group("Options de production")
    production.add_argument("--no-subtitles", dest="no_subtitles", action="store_true",
                            help="Ne pas incruster de sous-titres et ne pas exporter de fichier de sous-titres.")
    production.add_argument("--aspect", dest="aspect", choices=["9:16", "16:9"], default=None,
                            help="Format de sortie. 9:16 par defaut (Reels, Shorts, TikTok) ; "
                                 "16:9 conserve l'image d'origine sans recadrage.")
    production.add_argument("--no-smart-framing", dest="no_smart_framing", action="store_true",
                            help="Desactive le cadrage intelligent (suivi du visage) : recadrage centre.")
    production.add_argument("--no-auto-montage", dest="no_auto_montage", action="store_true",
                            help="Desactive le montage automatique (coupe des silences, zooms).")
    production.add_argument("--no-watermark", dest="no_watermark", action="store_true",
                            help="Ne pas poser le logo en filigrane sur les clips.")
    production.add_argument("--black-bars", dest="black_bars", action="store_true",
                            help="En 16:9, completer avec des bandes noires au lieu du fond flou.")
    production.add_argument("--whole-source", dest="whole_source", action="store_true",
                            help="La source est deja un clip : la traiter en entier, "
                                 "sans y chercher ni y recadrer un passage.")

    yt = parser.add_argument_group("Filtres de recherche YouTube (--search uniquement)")
    yt.add_argument("--max-results", dest="max_results", type=int, default=10,
                     help="Nombre de resultats (max 50, defaut: 10).")
    yt.add_argument("--sort", choices=["relevance", "views", "date", "potential"], default="potential",
                     help="Tri des resultats (defaut: potential -- Video Potential Score).")
    yt.add_argument("--yt-language", dest="yt_language", default=None, help="Code langue (ex: fr).")
    yt.add_argument("--yt-duration", dest="yt_duration", choices=["short", "medium", "long"], default=None,
                     help="short(<4min)|medium(4-20min)|long(>20min), filtre cote API.")
    yt.add_argument("--yt-min-duration-s", dest="yt_min_duration_s", type=int, default=None,
                     help="Duree minimale exacte en secondes (filtre applique apres coup).")
    yt.add_argument("--yt-max-duration-s", dest="yt_max_duration_s", type=int, default=None,
                     help="Duree maximale exacte en secondes (filtre applique apres coup).")
    yt.add_argument("--yt-published-after", dest="yt_published_after", default=None,
                     help="Date minimale de publication, format YYYY-MM-DD.")
    yt.add_argument("--yt-published-before", dest="yt_published_before", default=None,
                     help="Date maximale de publication, format YYYY-MM-DD.")
    yt.add_argument("--yt-channel", dest="yt_channel", default=None, help="Restreint a une chaine (ID YouTube).")
    yt.add_argument("--yt-category", dest="yt_category", default=None, help="ID de categorie YouTube.")
    yt.add_argument("--yt-creative-commons", dest="yt_creative_commons", action="store_true",
                     help="Ne montre que les videos marquees Creative Commons (indicatif, pas une garantie juridique).")

    return parser


def run_search(args: argparse.Namespace) -> int:
    from youtube.models import SearchFilters
    from youtube.quota import QuotaTracker
    from youtube.ranking import rank_videos
    from youtube.search import search_videos

    yt_config = load_youtube_config()
    quota = QuotaTracker(daily_limit=yt_config.get("daily_quota_limit", 10000))

    order_map = {"views": "viewCount", "date": "date", "relevance": "relevance", "potential": "relevance"}
    filters = SearchFilters(
        language=args.yt_language,
        order=order_map.get(args.sort, "relevance"),
        video_duration_bucket=args.yt_duration,
        min_duration_s=args.yt_min_duration_s,
        max_duration_s=args.yt_max_duration_s,
        published_after=f"{args.yt_published_after}T00:00:00Z" if args.yt_published_after else None,
        published_before=f"{args.yt_published_before}T00:00:00Z" if args.yt_published_before else None,
        channel_id=args.yt_channel,
        category_id=args.yt_category,
        creative_commons_only=args.yt_creative_commons,
    )

    videos = search_videos(args.search, args.max_results, filters, quota)
    ranked = rank_videos(
        videos, args.search,
        clip_duration=args.clip_duration or 45,
        min_gap=args.min_gap or 20,
        weights=yt_config["weights"],
        params=yt_config["params"],
        sort_by=args.sort,
    )

    if not ranked:
        print("Aucun resultat.")
        return 0

    print(f"\n{len(ranked)} resultat(s) pour \"{args.search}\" :\n")
    for i, r in enumerate(ranked, start=1):
        v = r.video
        views = f"{v.view_count:,}".replace(",", " ") if v.view_count is not None else "?"
        duration = f"{v.duration_seconds // 60}:{v.duration_seconds % 60:02d}" if v.duration_seconds else "?"
        cc = " [CC]" if v.license == "creativeCommon" else ""
        print(f"[{i}] potentiel={r.score.total:.0f}/100{cc}  {v.title}")
        print(f"    {v.channel_title} -- {views} vues -- {duration} -- {v.published_at[:10]}")
        print(f"    {v.url}  (id: {v.video_id})")
        print()

    print(f"Pour analyser : python main.py --youtube <id> --confirm-rights --nb-clips {args.nb_clips or 5}")
    print(
        "\n⚠️  Trouver une video ne donne pas le droit de la reutiliser. "
        "Verifie tes droits avant de republier ou monetiser un extrait -- "
        "voir README.md, section recherche YouTube."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    # Les versions precedentes ecrivaient cache et projets dans le dossier
    # d'installation : on les recupere avant de lire quoi que ce soit.
    migrate_legacy_user_data()

    parser = build_arg_parser()
    args = parser.parse_args(argv)
    youtube_tmp_dir = None

    try:
        if args.search:
            return run_search(args)

        if args.youtube:
            from youtube.downloader import RIGHTS_WARNING, download_video

            print(f"\n{RIGHTS_WARNING}\n", file=sys.stderr)
            youtube_tmp_dir = tempfile.mkdtemp(prefix="clip_farming_youtube_")
            args.input = download_video(args.youtube, youtube_tmp_dir, consent_confirmed=args.confirm_rights)

        if not args.input:
            parser.error("un de --input, --youtube ou --search est requis (voir --help)")

        settings = load_settings(args)
        results = pipeline.run(settings)
    except ClipFarmingError as e:
        print(f"\nErreur : {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrompu par l'utilisateur.", file=sys.stderr)
        return 130
    except Exception as e:  # noqa: BLE001 -- dernier filet, message clair plutot qu'un crash brut
        print(f"\nErreur inattendue : {e}", file=sys.stderr)
        if args.verbose:
            traceback.print_exc()
        else:
            print("(relance avec --verbose pour la trace complete)", file=sys.stderr)
        return 1
    finally:
        if youtube_tmp_dir:
            shutil.rmtree(youtube_tmp_dir, ignore_errors=True)

    print(f"\n{len(results)} clip(s) genere(s) dans '{settings.output}/' :")
    for r in results:
        print(f"  {r.file_name}  (score {r.score:.0f}/100, {r.duration:.1f}s)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
