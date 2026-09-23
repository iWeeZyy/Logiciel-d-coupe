#!/usr/bin/env python3
"""Genere le masque alpha de l'incrustation intro (assets/branding/
intro_follow_mask.mp4).

RUN A LA MAIN, PAS AU BUILD -- meme convention que les autres scripts de
tools/ : le fichier produit est COMMITE, pas regenere a chaque build.

POURQUOI UN MASQUE ET NON UN colorkey AU RENDU. La video source
(intro_follow.mp4) a un fond NOIR PLEIN CADRE, mais le logo lui-meme contient
AUSSI du noir -- le disque a l'interieur de l'anneau neon, sous le texte
"ClipsOfStreams". Un colorkey supprime tout le noir sans distinction : le
disque du logo devenait transparent en meme temps que le fond, laissant voir
le clip a travers le logo au lieu de son propre fond noir.

Un simple REMPLISSAGE PAR PROPAGATION (flood-fill) depuis les bords de
l'image resout ca correctement : les pixels noirs ATTEIGNABLES depuis un bord
en ne traversant que du noir sont le vrai fond (on les rend transparents) ;
le disque interieur, lui, est ENTOURE par l'anneau neon (jamais noir), donc
jamais atteint par la propagation -- il reste opaque, exactement comme il
apparait dans la video source.

BUG CORRIGE ICI -- POURQUOI UN MASQUE PAR IMAGE ET NON UNE SEULE IMAGE DE
REFERENCE (comme la premiere version de ce script le faisait). L'anneau
neon N'EST PAS statique du debut a la fin : il apparait en grossissant
depuis rien pendant environ la premiere seconde de l'animation (la version
precedente prenait sa reference a t=3.5s, EXPRES apres cette phase, pour
avoir un contour "pleinement etabli"). Un masque UNIQUE construit sur cette
image tardive reste correct pour tout le reste de l'animation, mais pas pour
son debut : applique aux tout premiers instants, ou l'anneau est encore
petit, le masque continue de marquer comme OPAQUE toute la zone qu'il occupe
a taille finale -- y compris la partie de cette zone qui, a cet instant
precis, est encore du vrai fond noir non dessine. Resultat : un aplat noir
plein cadre, bien plus grand que l'anneau reellement visible, pendant la
demi-seconde a une seconde ou l'incrustation apparait -- signale par
l'utilisateur comme "mal fait" sur les clips 9:16 (le defaut existe en
realite dans tous les formats de sortie, 9:16 le rend seulement plus visible
: l'incrustation y occupe une plus grande part du cadre).

Le flood-fill etant une operation PAR IMAGE (les pixels noirs atteignables
depuis le bord ne dependent que de CETTE image), le repeter sur CHAQUE image
de l'animation resout le probleme a la racine, sans perdre l'effet
d'apparition : tant que l'anneau n'est pas encore ferme, le flood-fill
atteint tout ce qui l'entoure (rien n'est encore "enferme" a l'interieur) et
rend tout transparent -- exactement ce qu'il faut montrer, puisque rien n'a
ete dessine la. Des que l'anneau se referme suffisamment pour entourer le
disque, celui-ci cesse d'etre atteint et redevient opaque -- au bon moment,
celui ou la video source le dessine vraiment.

Le fichier produit est donc desormais une VIDEO (meme duree, meme cadence,
meme nombre d'images que intro_follow.mp4, alignee image pour image avec
elle), pas une image fixe -- video/intro_overlay.py::prepare_filter() n'a
pas eu a changer : `alphamerge` prend deja son second flux comme une source
video ordinaire, qu'elle ait une image ou 210.

Usage :
    .venv/bin/python tools/generate_intro_mask.py
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter
from scipy import ndimage

ROOT = Path(__file__).resolve().parent.parent
SOURCE_VIDEO = ROOT / "assets" / "branding" / "intro_follow.mp4"
OUT_PATH = ROOT / "assets" / "branding" / "intro_follow_mask.mp4"

# Meme cadre que video/intro_overlay.py (CROP_X/Y/W/H) -- les deux DOIVENT
# rester en phase, puisque le masque n'a de sens qu'aligne sur ce recadrage.
CROP_X, CROP_Y, CROP_W, CROP_H = 40, 330, 1000, 1140

# Seuil de "noir" : identique dans l'esprit a l'ancien colorkey (similarity
# 0.15 sur une echelle 0..255 ~ 38). Une marge un peu plus etroite ici : le
# flood-fill n'a pas besoin d'etre genereux, un pixel legerement grisatre du
# halo neon doit rester du cote "logo", pas "fond".
BLACK_THRESHOLD = 35

# Adoucit le contour du masque (pixelise sinon) -- meme raisonnement que le
# blend du colorkey qu'il remplace.
FEATHER_RADIUS = 2

# Connectivite 4 (haut/bas/gauche/droite uniquement, jamais en diagonale) --
# la meme que le flood-fill par file d'attente de la version precedente.
_STRUCTURE = ndimage.generate_binary_structure(2, 1)


def _extract_frames(tmp_dir: Path) -> tuple[Path, float]:
    """Toutes les images source, deja rognees sur le cadre utile -- une
    passe ffmpeg, sans reechantillonnage de cadence (chaque image source
    devient exactement une image de la sequence)."""
    frames_dir = tmp_dir / "src"
    frames_dir.mkdir()
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=r_frame_rate",
         "-of", "default=noprint_wrappers=1:nokey=1", str(SOURCE_VIDEO)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if probe.returncode != 0:
        sys.exit(f"ffprobe a echoue : {probe.stderr.decode(errors='replace')}")
    num, _, den = probe.stdout.decode().strip().partition("/")
    fps = float(num) / float(den or 1)

    cmd = [
        "ffmpeg", "-y", "-i", str(SOURCE_VIDEO),
        "-vf", f"crop={CROP_W}:{CROP_H}:{CROP_X}:{CROP_Y}",
        "-vsync", "0", str(frames_dir / "frame_%04d.png"),
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        sys.exit(f"ffmpeg a echoue : {result.stderr.decode(errors='replace')}")
    return frames_dir, fps


def _mask_for_frame(arr: np.ndarray) -> np.ndarray:
    """255 = garder (opaque), 0 = fond retire (transparent) -- une seule
    image, aucune memoire de l'image precedente ou suivante necessaire."""
    is_black = arr.max(axis=2) < BLACK_THRESHOLD
    labels, _ = ndimage.label(is_black, structure=_STRUCTURE)

    border_labels = np.unique(np.concatenate(
        [labels[0, :], labels[-1, :], labels[:, 0], labels[:, -1]]
    ))
    border_labels = border_labels[border_labels != 0]  # 0 = pixel non noir

    reachable = np.isin(labels, border_labels)
    return np.where(reachable, 0, 255).astype(np.uint8)


def main() -> None:
    if not SOURCE_VIDEO.is_file():
        sys.exit(f"Introuvable : {SOURCE_VIDEO}")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        frames_dir, fps = _extract_frames(tmp_dir)
        src_frames = sorted(frames_dir.glob("frame_*.png"))
        if not src_frames:
            sys.exit("Aucune image extraite de la video source.")

        mask_dir = tmp_dir / "mask"
        mask_dir.mkdir()
        for i, frame_path in enumerate(src_frames, start=1):
            arr = np.array(Image.open(frame_path).convert("RGB"))
            if arr.shape[:2] != (CROP_H, CROP_W):
                sys.exit(f"Image {frame_path.name} {arr.shape[:2]} != cadre attendu {(CROP_H, CROP_W)}")
            mask = _mask_for_frame(arr)
            mask_img = Image.fromarray(mask, mode="L").filter(ImageFilter.GaussianBlur(FEATHER_RADIUS))
            mask_img.save(mask_dir / f"mask_{i:04d}.png")
            if i % 30 == 0 or i == len(src_frames):
                print(f"  masque {i}/{len(src_frames)}")

        OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            "ffmpeg", "-y", "-framerate", f"{fps}",
            "-i", str(mask_dir / "mask_%04d.png"),
            # crf 0 = x264 sans perte : un masque degrade introduirait un
            # liseré visible (bruit de compression) exactement sur le
            # contour qu'il sert a dessiner.
            "-c:v", "libx264", "-preset", "veryslow", "-crf", "0",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            str(OUT_PATH),
        ]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.returncode != 0:
            sys.exit(f"ffmpeg (encodage) a echoue : {result.stderr.decode(errors='replace')}")

    old_png = OUT_PATH.with_suffix(".png")
    if old_png.is_file():
        old_png.unlink()

    print(f"Ecrit {OUT_PATH} ({len(src_frames)} images, {fps:.3f} fps)")


if __name__ == "__main__":
    main()
