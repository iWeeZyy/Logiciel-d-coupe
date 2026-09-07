# -*- mode: python ; coding: utf-8 -*-
"""Spec PyInstaller pour le build Windows (.github/workflows/build-windows-exe.yml).

Mode --onedir (pas --onefile) delibere : numba (JIT, utilise par librosa) et
ctranslate2 se comportent mal empaquetes en un seul fichier qui se
desarchive dans un dossier temporaire a chaque lancement -- --onedir est le
mode recommande pour ce genre de dependances. Le resultat est un dossier
dist/clip_farming/ contenant clip_farming.exe + toutes ses dependances +
config/ (ffmpeg.exe/ffprobe.exe y sont copies par le workflow apres coup,
pas ici -- voir le job GitHub Actions).
"""
import os

from PyInstaller.utils.hooks import collect_all

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

datas = [(os.path.join(ROOT, "config"), "config")]
binaries = []
hiddenimports = []

# Ces paquets font tous des choses que l'analyse statique de PyInstaller rate
# (chargement dynamique de .dll/.pyd, donnees embarquees, imports conditionnels) :
# faster_whisper/ctranslate2 (moteur Whisper), av (decodeur audio pour Whisper),
# librosa/numba (analyse audio, JIT), cv2 (detection de visage), onnxruntime
# (VAD interne a faster-whisper), soundfile (lecture wav).
for pkg in [
    "faster_whisper", "ctranslate2", "av", "librosa", "numba",
    "cv2", "onnxruntime", "soundfile",
    "requests", "yt_dlp", "tzdata",
]:
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    [os.path.join(ROOT, "main.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="clip_farming",
    console=True,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    name="clip_farming",
)
