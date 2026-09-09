# -*- mode: python ; coding: utf-8 -*-
"""Spec PyInstaller pour la version graphique (.github/workflows/build-windows-gui.yml).

Meme raisonnement --onedir que build/clip_farming.spec (numba/ctranslate2 se
comportent mal en --onefile). Le resultat est un dossier dist/ClipFarming/
contenant ClipFarming.exe + toutes les dependances + config/ + assets/
(ffmpeg.exe/ffprobe.exe copies par le workflow apres coup, comme pour la CLI).

PySide6 n'est PAS dans la boucle collect_all ci-dessous : PyInstaller a un
hook dedie pour PySide6 (paquet pyinstaller-hooks-contrib, deja tire par
pyinstaller lui-meme) qui gere correctement les plugins Qt necessaires
(platforms/, multimedia/, styles/) des que l'import est detecte par analyse
statique -- lui rajouter collect_all() par-dessus duplique/alourdit pour
rien, voir la doc du hook PySide6 de PyInstaller.
"""
import os

from PyInstaller.utils.hooks import collect_all

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

datas = [
    (os.path.join(ROOT, "config"), "config"),
    (os.path.join(ROOT, "assets"), "assets"),
]
binaries = []

# radar.analysis.handoff n'est encore appele par personne : c'est l'interface
# preparee pour le Content Factory et l'apprentissage, que la demande dit
# explicitement de ne pas brancher tout de suite. L'analyse statique de
# PyInstaller ne peut donc pas le trouver, et il serait absent de l'exe -- le
# jour ou on le branche, l'application compilee planterait sur un import
# manquant alors que tout marche depuis les sources. Le declarer ici coute un
# fichier de 3 ko et supprime ce piege.
hiddenimports = [
    "radar.analysis.handoff",
    # keyring choisit son coffre a l'execution : l'analyse statique ne voit donc
    # pas le backend Windows, et un exe compile sans lui refuserait toute
    # connexion de compte en annoncant, a tort, que le coffre est indisponible.
    "keyring.backends.Windows",
    "keyring.backends.SecretService",
    "keyring.backends.fail",
]

for pkg in [
    "faster_whisper", "ctranslate2", "av", "librosa", "numba",
    "cv2", "onnxruntime", "soundfile", "PIL",
    "requests", "yt_dlp", "tzdata",
]:
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    [os.path.join(ROOT, "gui_main.py")],
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
    name="ClipFarming",
    # Console gardee visible (pas de version windowed-only) : le cahier des
    # charges demande explicitement que la console reste disponible en
    # arriere-plan pour les logs techniques/le debogage, meme si l'utilisateur
    # final n'en a normalement jamais besoin.
    console=True,
    disable_windowed_traceback=False,
    icon=os.path.join(ROOT, "assets", "icons", "clipfarming.ico")
    if os.path.exists(os.path.join(ROOT, "assets", "icons", "clipfarming.ico")) else None,
    # PyInstaller >= 6.0 range par defaut tout ce qui est collecte (dependances
    # ET datas, donc config/ et assets/) dans un sous-dossier "_internal/"
    # plutot qu'a plat a cote de l'exe. C'est ICI, sur EXE(), que ce
    # comportement se decide reellement (COLLECT() plus bas se contente de
    # recopier la valeur depuis cet objet EXE -- lui passer contents_directory
    # separement n'a aucun effet, verifie dans le code source de PyInstaller,
    # PyInstaller/building/api.py). "." restaure l'ancienne disposition a
    # plat (comportement pre-6.0). Sans ca, le programme plante au demarrage
    # avec un FileNotFoundError sur config/settings.json (bug reel constate
    # par un utilisateur en test).
    contents_directory=".",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    name="ClipFarming",
)
