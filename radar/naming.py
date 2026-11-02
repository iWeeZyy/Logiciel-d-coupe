"""Le nom d'un streamer tel que les gens le TAPENT dans la recherche TikTok
(demande du 2026-11-02) : « kameto » et non « kamet0 », « anyme » plutot que
« anyme023 ». Il sert dans la legende (titre et hashtag en plus de celui du
login) ; la liste vit dans la configuration privee, cle « search_names » :
{"kamet0": "Kameto", "anyme023": "Anyme"}. Sans entree : le nom Twitch.

Et le commentaire a epingler sous chaque video, pour lancer la discussion
(les commentaires font monter une video). Fonctions pures, aucun import de Qt.
"""
from __future__ import annotations

import zlib

VOWELS = "aeiouyhéèêàâîôûAEIOUYHÉÈÊÀÂÎÔÛ"

CLIP_COMMENTS = (
    "Note ce moment sur 10 👇",
    "Tu aurais réagi comment à sa place ? 👇",
    "Tu veux voir quel streamer dans le prochain clip ? 👇",
    "Tag quelqu'un qui doit voir ça 👇",
)


def search_name(config: dict, login: str, label: str = "") -> str:
    """Le nom a afficher et a chercher ; a defaut le nom Twitch."""
    names = {k.lower(): v for k, v in ((config or {}).get("search_names") or {}).items()}
    return " ".join(str(names.get((login or "").lower()) or label or login or "").split())


def of(name: str) -> str:
    """« de Squeezie », « d'Anyme »."""
    name = (name or "").strip()
    return f"d'{name}" if name and name[0] in VOWELS else f"de {name}"


def clip_comment(clip_id: str) -> str:
    """Commentaire a epingler sous un clip seul : une question, toujours la
    meme pour un meme clip, variee d'un clip a l'autre."""
    return CLIP_COMMENTS[zlib.crc32((clip_id or "").encode()) % len(CLIP_COMMENTS)]


def top_comment(count: int) -> str:
    """Commentaire a epingler sous un top : voter pour son prefere."""
    numbers = [f"#{n}" for n in range(1, max(1, count) + 1)]
    listed = ", ".join(numbers[:-1]) + f" ou {numbers[-1]}" if len(numbers) > 1 else numbers[0]
    return f"Ton préféré : {listed} ? 👇"
