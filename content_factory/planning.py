"""Mode automatique du Content Factory (section 2) : combien de clips, et de
quelle duree, pour une video donnee.

Ce n'est pas une intelligence : c'est une regle de trois assumee, ecrite ici
plutot que devinee dans l'interface, pour qu'elle soit lisible et testable.

Le raisonnement : une video contient un nombre limite de moments reellement
distincts. En viser trop revient a produire des variantes du meme passage --
exactement ce que la selection diversifiee cherche a eviter. En viser trop peu
laisse du contenu de cote. Le rapport retenu (un clip par tranche de quelques
minutes) est un point de depart honnete, borne des deux cotes, et modifiable :
l'utilisateur garde toujours la main sur le nombre.

Module pur.
"""
from __future__ import annotations

# Une tranche de video par clip vise. Quatre minutes : sur un podcast, c'est
# l'ordre de grandeur d'un sujet ; en dessous, on decoupe le meme sujet en
# morceaux, au-dessus on laisse passer des moments.
SECONDS_PER_CLIP = 240.0

MIN_CLIPS = 3
MAX_CLIPS = 20

# Duree de clip proposee en mode automatique. La detection de contexte ajuste
# ensuite les bornes reelles : cette valeur est un point de depart, pas une
# promesse -- l'etiquette de l'interface le dit deja.
AUTO_CLIP_DURATION_S = 45


def suggested_clip_count(video_duration_s: float) -> int:
    """Nombre de clips propose pour une video de cette duree.

    Une video plus courte que la premiere tranche donne quand meme le minimum :
    trois clips valent mieux qu'un seul quand on cherche a produire en serie, et
    la selection en produira moins d'elle-meme si le contenu ne suit pas.
    """
    if video_duration_s <= 0:
        return MIN_CLIPS
    raw = round(video_duration_s / SECONDS_PER_CLIP)
    return max(MIN_CLIPS, min(MAX_CLIPS, int(raw)))


def describe(video_duration_s: float) -> str:
    """Phrase affichee sous le champ, pour que le chiffre ne tombe pas du ciel."""
    minutes = max(1, round(video_duration_s / 60))
    count = suggested_clip_count(video_duration_s)
    return f"{count} clips proposés pour {minutes} min de vidéo"
