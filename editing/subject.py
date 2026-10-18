"""Qui cadrer : la personne au premier plan, pas l'incrustation -- et, pour le
mode portrait webcam+gameplay, l'inverse exact : l'incrustation ELLE-MEME.

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

LE MODE PORTRAIT (webcam en haut, gameplay en bas -- video/filter_graph.py,
FIT_SPLIT_WEBCAM) a besoin de l'inverse : cadrer PRECISEMENT l'incrustation que
`keep_subject_faces` ecarte deliberement. `webcam_track`/`keep_webcam_faces`
partagent le meme regroupement en pistes (`build_tracks`) mais appliquent la
regle symetrique -- avec un garde-fou supplementaire, `MIN_WEBCAM_PRESENCE` :
une webcam REELLE est visible la quasi-totalite du temps (fixe, bien eclairee,
de face), donc un faux positif ponctuel ne doit jamais etre pris pour elle --
l'erreur serait bien pire ici, ou la piste designee occupe toute une bande du
clip final, que pour le sujet, ou elle ne fait que decaler un peu le cadrage.
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

# Seuil de presence pour la webcam, bien plus strict que MIN_PRESENCE : une
# incrustation reelle est vue presque partout (fixe, bien eclairee, de face),
# contrairement au sujet qui se retourne ou sort du champ. Sous ce seuil, la
# piste la plus petite est plus probablement un faux positif isole (une main,
# un reflet) qu'une vraie webcam -- mieux vaut renoncer au split que cadrer une
# bande entiere du clip sur du bruit.
MIN_WEBCAM_PRESENCE = 0.35


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


def webcam_track(tracks: list[FaceTrack], min_area_ratio: float = MIN_AREA_RATIO,
                 min_presence: float = MIN_WEBCAM_PRESENCE) -> FaceTrack | None:
    """La piste qui EST l'incrustation webcam, ou None si aucune ne se degage
    clairement.

    Deux cas, symetriques a `subject_tracks` :

    - UNE SEULE piste dans tout le clip : c'est forcement elle -- un streamer
      seul visible face camera, jeu sans visage en arriere-plan, est la
      configuration la plus courante sur Twitch. Elle doit quand meme etre
      assez presente (`min_presence`) pour ecarter un unique faux positif.
    - PLUSIEURS pistes : la plus petite est candidate, mais seulement si elle
      est significativement plus petite que la plus grande (meme seuil que
      `subject_tracks`, applique a l'envers) ET assez presente. Deux
      personnes de taille comparable ne produisent aucune webcam identifiable
      -- ce cas doit renvoyer None, pas deviner laquelle est l'incrustation.
    """
    usable = [t for t in tracks if t.presence >= min_presence]
    if not usable:
        return None

    if len(usable) == 1:
        return usable[0]

    smallest = min(usable, key=lambda t: t.area)
    largest_area = max(t.area for t in usable)
    if largest_area <= 0:
        return None
    if smallest.area >= largest_area * min_area_ratio:
        # Toutes les pistes sont de taille comparable : pas d'incrustation a
        # designer, seulement des participants.
        return None
    return smallest


def keep_webcam_faces(samples, radius: float = CLUSTER_RADIUS,
                      min_area_ratio: float = MIN_AREA_RATIO,
                      min_presence: float = MIN_WEBCAM_PRESENCE):
    """Renvoie les memes echantillons, prives de tout visage QUI N'EST PAS la
    webcam -- l'inverse exact de `keep_subject_faces`.

    None si aucune webcam ne se degage : l'appelant (editing/framing.py) sait
    alors qu'il n'y a rien a cadrer pour la bande du haut, et doit renoncer au
    mode portrait plutot que de cadrer une bande sur du bruit."""
    samples = list(samples)
    tracks = build_tracks(samples, radius=radius)
    webcam = webcam_track(tracks, min_area_ratio=min_area_ratio, min_presence=min_presence)
    if webcam is None:
        return None

    def belongs(box) -> bool:
        return ((webcam.cx - box.cx) ** 2 + (webcam.cy - box.cy) ** 2) ** 0.5 < radius

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
