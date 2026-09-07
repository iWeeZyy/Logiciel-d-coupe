"""Localise config/, .cache/ et ffmpeg par rapport au binaire quand l'app tourne
packagee en .exe (PyInstaller), et par rapport au depot quand elle tourne en
script Python normal.

En mode normal, __file__ pointe dans le depot -> parent.parent = racine du projet.
En mode fige (PyInstaller --onedir), sys.executable est le .exe genere et tous
les fichiers de donnees (config/, ffmpeg.exe...) sont copies a cote de lui par
le build (voir build/build_windows.py) -> son dossier EST la racine "projet".
"""
from __future__ import annotations

import sys
from pathlib import Path


def app_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent
