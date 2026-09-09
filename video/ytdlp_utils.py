"""Helpers yt-dlp partages par les telechargements YouTube et Twitch.

Ces deux fonctions etaient dans youtube/downloader.py, ou elles ont ete ecrites.
Elles n'ont pourtant rien de specifique a YouTube : la chaine de format et la
recherche du fichier produit valent pour n'importe quelle source geree par
yt-dlp. Les laisser la aurait oblige le telechargement Twitch soit a les
importer depuis un paquet qui ne le concerne pas, soit a en ecrire une seconde
version -- deux mauvaises reponses a une question de rangement.
"""
from __future__ import annotations

from pathlib import Path

# Extensions produites par yt-dlp selon ce qu'il a pu fusionner.
OUTPUT_EXTENSIONS = (".mp4", ".mkv", ".webm", ".m4a")


def build_format(max_height: int = 0) -> str:
    """Selecteur de format yt-dlp : la meilleure qualite reellement disponible.

    Pourquoi ne pas exiger du MP4 : YouTube ne sert en MP4 que ses flux H.264.
    Le 1440p, le 2160p et souvent le 1080p60 n'existent qu'en VP9 ou AV1, dans
    un conteneur WebM. Exiger du MP4 revient a refuser silencieusement les
    meilleures pistes et a se contenter, au mieux, d'un 1080p H.264.

    La contrainte de conteneur est donc levee sur la VIDEO, gardee sur l'AUDIO :
    l'AAC (m4a) se remuxe proprement en MP4, alors que l'Opus de la piste WebM y
    est mal supporte et fait echouer la fusion.

    `max_height` plafonne la definition quand elle est renseignee ; 0 signifie
    aucune limite.
    """
    ceiling = f"[height<={int(max_height)}]" if max_height and max_height > 0 else ""
    return (
        f"bestvideo*{ceiling}+bestaudio[ext=m4a]/"
        f"bestvideo*{ceiling}+bestaudio/"
        f"best{ceiling}/best"
    )


def find_downloaded_file(out_dir: str, video_id: str) -> Path | None:
    """Fichier reellement produit par yt-dlp.

    On ne suppose pas le .mp4 : quand la fusion doit se rabattre sur un autre
    conteneur, le fichier existe bel et bien, et supposer l'extension faisait
    annoncer un echec sur un telechargement reussi.
    """
    directory = Path(out_dir)
    for extension in OUTPUT_EXTENSIONS:
        candidate = directory / f"{video_id}{extension}"
        if candidate.is_file():
            return candidate
    matches = sorted(directory.glob(f"{video_id}.*"))
    return matches[0] if matches else None
