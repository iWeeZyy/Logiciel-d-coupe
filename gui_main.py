#!/usr/bin/env python3
"""Point d'entree de l'interface graphique ClipFarming.

main.py (CLI) reste inchange et pleinement fonctionnel -- ceci est une
DEUXIEME facon de lancer le meme moteur, pas un remplacement.
"""
from __future__ import annotations

import sys

from gui.app import main

if __name__ == "__main__":
    sys.exit(main())
