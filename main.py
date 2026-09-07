#!/usr/bin/env python3
"""CLI de clip_farming -- transforme une video longue en clips courts 9:16
optimises pour Instagram Reels, 100% local (voir README.md).

Exemple :
    python main.py --input video.mp4 --clip-duration 45 --nb-clips 5
"""
from __future__ import annotations

import argparse
import sys
import traceback

import pipeline
from core.config_loader import load_settings
from core.logging_setup import get_logger
from utils.errors import ClipFarmingError

logger = get_logger()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description=(
            "Transforme une video longue en plusieurs clips courts 9:16 "
            "optimises pour Instagram Reels -- 100%% local, sans API externe."
        ),
        epilog=(
            "Exemples :\n"
            "  python main.py --input video.mp4\n"
            "  python main.py --input video.mp4 --clip-duration 45 --nb-clips 5\n"
            "  python main.py --input video.mp4 --model medium --device cuda\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument("--input", required=True, help="Chemin de la video source.")
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

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    try:
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

    print(f"\n{len(results)} clip(s) genere(s) dans '{settings.output}/' :")
    for r in results:
        print(f"  {r.file_name}  (score {r.score:.0f}/100, {r.duration:.1f}s)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
