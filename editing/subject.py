"""Qui cadrer : la personne au premier plan, pas l'incrustation.

DEFAUT REEL QUE CE MODULE CORRIGE. Sur un clip Twitch, la camera du streamer est
souvent affichee en petit dans un coin, en plus de la personne filmee au premier
plan. Le detecteur voit deux visages et le cadrage les traitait a egalite :

- en cadrage fixe, il MOYENNAIT leurs positions -- le point obtenu ne designait
  personne, et le clip 9:16 coupait le sujet en deux ;
- en cadrage suivi, deux visages eloignes declenchaient le cadrage "groupe",
  qui cadrait le milieu du vide entre l'incrustation et le sujet.

La regle qui manquait tient en une phrase : UN VISAGE BEAUCOUP PLUS PETIT QUE LE
PLUS GROS N'EST PAS UN PARTICIPANT, C'EST UNE INCRUSTATION. Un visage plus grand
est plus proche de la camera, donc au premier plan -- c'est de la perspective,
pas une heuristique sur la position, et cela ne depend ni du coin choisi pour
l'incrustation ni de la mise en page de la chaine.

Le cas de deux personnes cote a cote reste traite comme avant : leurs visages
sont de taille comparable, les deux sont donc gardes et le cadrage groupe
s'applique.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

# Deux observations plus proches que ce rayon (en fraction de largeur d'image)
# sont considerees comme la meme personne d'un echantillon a l'autre.
CLUSTER_RADIUS = 0.14

# Un visage dont l'aire typique tombe sous cette part de celle du plus grand est
# ecarte. 0.55 laisse passer deux personnes a des distances differentes de la
# camera -- courant sur un canape -- et ecarte une incrustation, qui est
# generalement deux fois plus petite ou davantage.
MIN_AREA_RATIO = 0.55

# En dessous, la piste est trop rare pour etre le sujet : un faux positif
# apparu deux fois ne doit pas voler le cadrage a une personne presente partout.
MIN_PRESENCE = 0.10


@dataclass(frozen=True)
class FaceTrack:
    """Une personne suivie a travers les echantillons."""

    cx: float
    cy: float
    area: float          # aire typique (mediane des observations)
    peak_area: float
    count: int
    presence: float      # part des echantillons ou elle apparait

    @property
    def is_reliable(self) -> bool:
        return self.presence >= MIN_PRESENCE


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if not ordered:
        return 0.0
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def build_tracks(samples, radius: float = CLUSTER_RADIUS) -> list[FaceTrack]:
    """Regroupe les visages detectes en personnes.

    Regroupement par proximite et non par identite : reconnaitre un visage
    demanderait un second modele et beaucoup de temps de calcul, alors qu'une
    incrustation et un sujet ne bougent pratiquement jamais l'un vers l'autre.
    """
    total = sum(1 for _ in samples) or 1
    clusters: list[dict] = []

    for sample in samples:
        for box in getattr(sample, "faces", ()) or ():
            best = None
            best_distance = radius
            for cluster in clusters:
                distance = ((cluster["cx"] - box.cx) ** 2 + (cluster["cy"] - box.cy) ** 2) ** 0.5
                if distance < best_distance:
                    best, best_distance = cluster, distance
            if best is None:
                clusters.append({"cx": box.cx, "cy": box.cy, "xs": [box.cx],
                                 "ys": [box.cy], "areas": [box.area]})
            else:
                best["xs"].append(box.cx)
                best["ys"].append(box.cy)
                best["areas"].append(box.area)
                # Le centre suit la moyenne : une personne qui se deplace
                # lentement reste une seule piste.
                best["cx"] = sum(best["xs"]) / len(best["xs"])
                best["cy"] = sum(best["ys"]) / len(best["ys"])

    tracks = [
        FaceTrack(
            cx=cluster["cx"], cy=cluster["cy"],
            area=_median(cluster["areas"]),
            peak_area=max(cluster["areas"]),
            count=len(cluster["areas"]),
            presence=min(1.0, len(cluster["areas"]) / total),
        )
        for cluster in clusters
    ]
    tracks.sort(key=lambda t: t.area, reverse=True)
    return tracks


def subject_tracks(tracks: list[FaceTrack], min_area_ratio: float = MIN_AREA_RATIO) -> list[FaceTrack]:
    """Les personnes a cadrer : celle du premier plan, et celles de taille
    comparable.

    Le premier plan est designe par la TAILLE et non par la frequence
    d'apparition. Une incrustation est detectee a presque toutes les images --
    elle est fixe, bien eclairee, de face -- alors que le sujet se retourne, se
    cache le visage ou sort du champ. Trancher a la frequence choisirait donc
    systematiquement l'incrustation, ce qui est exactement le defaut constate.
    """
    usable = [t for t in tracks if t.is_reliable] or list(tracks)
    if not usable:
        return []
    reference = max(t.area for t in usable)
    if reference <= 0:
        return usable
    return [t for t in usable if t.area >= reference * min_area_ratio]


def keep_subject_faces(samples, radius: float = CLUSTER_RADIUS,
                       min_area_ratio: float = MIN_AREA_RATIO):
    """Renvoie les memes echantillons, prives des visages a ecarter.

    Un echantillon dont tous les visages sont ecartes garde une liste vide : le
    cadrage sait deja quoi faire quand il ne voit personne, alors qu'un visage
    faux le ferait partir au mauvais endroit.
    """
    samples = list(samples)
    tracks = build_tracks(samples, radius=radius)
    kept = subject_tracks(tracks, min_area_ratio=min_area_ratio)
    if not kept or len(kept) == len(tracks):
        return samples

    def belongs(box) -> bool:
        return any(((t.cx - box.cx) ** 2 + (t.cy - box.cy) ** 2) ** 0.5 < radius for t in kept)

    filtered = []
    for sample in samples:
        faces = tuple(b for b in (getattr(sample, "faces", ()) or ()) if belongs(b))
        if len(faces) == len(sample.faces):
            filtered.append(sample)
            continue
        activity = tuple(
            a for b, a in zip(sample.faces, sample.mouth_activity or ()) if belongs(b)
        )
        filtered.append(replace(sample, faces=faces, mouth_activity=activity))
    return filtered
