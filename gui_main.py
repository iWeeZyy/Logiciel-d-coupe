#!/usr/bin/env python3
"""Point d'entree de l'interface graphique ClipFarming.

main.py (CLI) reste inchange et pleinement fonctionnel -- ceci est une
DEUXIEME facon de lancer le meme moteur, pas un remplacement.

Toute exception (y compris a l'import, ex: DLL Qt manquante) est capturee ici
et ecrite dans un fichier a cote de l'executable, PLUS affichee avec une
pause avant de rendre la main -- lance en .exe par un double-clic, la
fenetre de console se ferme normalement des que le process se termine, ce
qui rend un plantage invisible (juste "une fenetre s'ouvre puis se ferme")
si on ne force pas explicitement une pause ici.
"""
from __future__ import annotations

import sys
import traceback


def _write_crash_log(exc: BaseException) -> str:
    from core.paths import user_data_dir

    log_path = user_data_dir() / "crash_log.txt"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(log_path, "w", encoding="utf-8") as f:
            f.write("ClipFarming a rencontre une erreur au demarrage.\n\n")
            traceback.print_exception(type(exc), exc, exc.__traceback__, file=f)
        return str(log_path)
    except OSError:
        return ""


def main() -> int:
    try:
        from gui.app import main as run_gui

        return run_gui()
    except BaseException as exc:  # noqa: BLE001 -- dernier filet avant que la fenetre ne se ferme
        print("\nClipFarming a rencontre une erreur au demarrage :\n", file=sys.stderr)
        traceback.print_exc()
        log_path = _write_crash_log(exc)
        if log_path:
            print(f"\nDetail enregistre dans : {log_path}", file=sys.stderr)
        print("\nAppuie sur Entree pour fermer cette fenetre...", file=sys.stderr)
        try:
            input()
        except (EOFError, KeyboardInterrupt):
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
