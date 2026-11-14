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


# Visage « durable » : present sur au moins ce quart du clip. En dessous, c'est
# le plus souvent un visage du CONTENU montre par le streamer (une
# retransmission, une video, un plan d'evenement qui passe), pas une personne
# a cadrer -- signale le 2026-10-31 : un gros plan d'une seconde dans la
# retransmission d'un evenement volait le cadrage.
MIN_PERSISTENT_PRESENCE = 0.25

# Incrustation webcam « dans un coin » : centre dans le tiers exterieur en
# largeur ET hors de la bande centrale en hauteur, et petit visage (une webcam
# d'angle fait quelques pour mille de l'image ; une personne filmee de pres,
# plusieurs pour cent).
CORNER_X = 0.33
CORNER_Y = 0.40
MAX_CORNER_FACE_AREA = 0.02
# ... mais pas minuscule : un detail de l'habillage du stream (personnage d'un
# logo) faisait 0,0004 et etait vu en continu dans un coin ; une vraie webcam
# fait 0,002 a 0,005 (mesure sur Kamet0, 720p et 1080p) -- 0,001 pour
# Squeezie assis loin de sa camera, image entiere d'un site commente.
MIN_CORNER_FACE_AREA = 0.0008


# Visage IMMOBILE et INCERTAIN : une affiche, un dessin ou une figurine au mur
# (Kamet0, 2026-11-13 : un visage dessine detecte a 0,62 sur 39 images, qui
# n'a pas bouge de 0,2 % de largeur, a cote du streamer a 0,95). Il faisait
# basculer le clip en plan large « a deux personnes » la moitie du temps.
# Meme defaut avec une alerte d'abonnement animee (Squeezie, 2026-11-14) :
# visage net (0,93) mais parfaitement fixe et bouche immobile (0,009, contre
# 0,03 a 0,07 pour une personne qui parle) -- le cadrage la suivait au lieu
# du streamer, plus petit car assis loin de la camera.
STILL_SPREAD = 0.006
STILL_MAX_CONFIDENCE = 0.75
STILL_MAX_MOUTH = 0.02
REAL_MIN_CONFIDENCE = 0.85


def _still_images(samples, tracks, radius: float) -> list:
    """Pistes qui sont une image fixe plutot qu'une personne : quasi aucun
    mouvement ET (confiance basse OU bouche immobile), quand une AUTRE piste
    est un visage sur (confiance haute) qui bouge. Sans personne de
    reference, rien n'est ecarte : mieux vaut un faux visage qu'aucun
    cadrage."""
    from statistics import median, pstdev

    stats = []
    for track in tracks:
        boxes = []
        for sample in samples:
            faces = tuple(getattr(sample, "faces", ()) or ())
            mouths = tuple(getattr(sample, "mouth_activity", ()) or ())
            for i, b in enumerate(faces):
                if ((track.cx - b.cx) ** 2 + (track.cy - b.cy) ** 2) ** 0.5 < radius:
                    boxes.append((b, mouths[i] if i < len(mouths) else None))
        if len(boxes) < 3:
            stats.append((track, 1.0, 1.0, 1.0))
            continue
        spread = max(pstdev([b.cx for b, _ in boxes]), pstdev([b.cy for b, _ in boxes]))
        mouth = [m for _, m in boxes if m is not None]
        stats.append((track, spread, median(getattr(b, "confidence", 1.0) for b, _ in boxes),
                      median(mouth) if mouth else 1.0))
    real = any(spread >= STILL_SPREAD and conf >= REAL_MIN_CONFIDENCE
               for _, spread, conf, _ in stats)
    if not real:
        return []
    return [t for t, spread, conf, mouth in stats
            if spread < STILL_SPREAD and (conf < STILL_MAX_CONFIDENCE or mouth < STILL_MAX_MOUTH)]


def keep_persistent_faces(samples, min_presence: float = MIN_PERSISTENT_PRESENCE,
                          radius: float = CLUSTER_RADIUS):
    """Les memes echantillons, prives des visages passagers (voir
    MIN_PERSISTENT_PRESENCE) et des images fixes (affiche au mur, voir
    STILL_SPREAD). Un echantillon peut se retrouver sans visage : le cadrage
    montre alors l'image entiere."""
    samples = list(samples)
    tracks = [t for t in build_tracks(samples, radius=radius) if t.presence >= min_presence]
    still = _still_images(samples, tracks, radius)
    tracks = [t for t in tracks if t not in still]

    def lasting(box) -> bool:
        return any(((t.cx - box.cx) ** 2 + (t.cy - box.cy) ** 2) ** 0.5 < radius for t in tracks)

    out = []
    for sample in samples:
        faces = tuple(getattr(sample, "faces", ()) or ())
        kept = tuple(b for b in faces if lasting(b))
        if len(kept) == len(faces):
            out.append(sample)
            continue
        activity = tuple(a for b, a in zip(faces, sample.mouth_activity or ()) if lasting(b))
        out.append(replace(sample, faces=kept, mouth_activity=activity))
    return out


def corner_webcam(samples, radius: float = CLUSTER_RADIUS,
                  min_presence: float = 0.5) -> FaceTrack | None:
    """L'incrustation webcam d'un stream de jeu ou d'evenement : un petit
    visage dans un coin, present au moins la moitie du clip. Les AUTRES
    visages n'y changent rien : sur une retransmission (camera des joueurs
    dans l'interface du match, gros plan d'un joueur), ils font partie du
    contenu montre (verifie sur trois clips de Kamet0 le 2026-10-31 : sa
    webcam en bas a gauche, presente sur 59 a 100 % des images, a cote de
    cameras de joueurs presentes sur la moitie du clip). S'il y en a
    plusieurs, la plus presente. None sinon -- le cadrage habituel
    s'applique."""
    candidates = []
    for track in build_tracks(samples, radius=radius):
        in_corner = ((track.cx < CORNER_X or track.cx > 1 - CORNER_X)
                     and (track.cy < CORNER_Y or track.cy > 1 - CORNER_Y))
        if (in_corner and MIN_CORNER_FACE_AREA <= track.area <= MAX_CORNER_FACE_AREA
                and track.presence >= min_presence):
            candidates.append(track)
    return max(candidates, key=lambda t: t.presence, default=None)


def floating_webcam(samples, radius: float = CLUSTER_RADIUS,
                    min_presence: float = 0.8) -> FaceTrack | None:
    """Une incrustation webcam HORS d'un coin (milieu d'un bord, par exemple) :
    un petit visage present presque tout le clip. Seulement un candidat --
    retenu par le pipeline uniquement si le reste de l'image est fixe, que la
    webcam bouge (en direct) et que ses bords sont trouves (video/motion.py).
    Le plus present, None sinon."""
    candidates = [t for t in build_tracks(samples, radius=radius)
                  if MIN_CORNER_FACE_AREA <= t.area <= MAX_CORNER_FACE_AREA
                  and t.presence >= min_presence]
    return max(candidates, key=lambda t: t.presence, default=None)


def webcam_box(webcam: FaceTrack, src_w: int, src_h: int, face_h: float,
               box_faces: float = 3.5, box_ratio: float = 1.6,
               margin: float = 0.01) -> tuple[float, float, float, float]:
    """Boite estimee de l'incrustation webcam autour du visage (memes
    proportions que game_window) : (x0, y0, x1, y1) en fractions. Pure."""
    box_h = min(1.0, box_faces * face_h) + margin
    box_w = min(1.0, box_h * src_h * box_ratio / src_w) + margin
    return (max(0.0, webcam.cx - box_w / 2), max(0.0, webcam.cy - box_h / 2),
            min(1.0, webcam.cx + box_w / 2), min(1.0, webcam.cy + box_h / 2))


def corner_box(webcam: FaceTrack, box: tuple[float, float, float, float]):
    """La boite estimee prolongee jusqu'au coin de l'ecran ou est la webcam
    (une incrustation est collee a son coin). Pure."""
    x0, y0, x1, y1 = box
    if webcam.cx < 0.5:
        x0 = 0.0
    else:
        x1 = 1.0
    if webcam.cy < 0.5:
        y0 = 0.0
    else:
        y1 = 1.0
    return x0, y0, x1, y1


def game_window(webcam: FaceTrack, src_w: int, src_h: int, band_aspect: float,
                face_h: float, box_faces: float = 3.5, box_ratio: float = 1.6,
                margin: float = 0.01):
    """Fenetre du JEU (bande du bas du mode webcam+jeu) qui ne contient PAS
    l'incrustation webcam -- deja en haut, on ne la veut pas deux fois
    (demande de l'utilisateur, 2026-10-31). Fonction pure.

    L'incrustation est estimee autour du visage : `box_faces` hauteurs de
    visage de haut, `box_ratio` fois plus large que haute (en pixels). La
    plus grande fenetre a l'aspect de la bande est cherchee de part et
    d'autre de l'incrustation (a cote, au-dessus, en dessous) ; si la fenetre
    centree ne la touche pas, on la garde telle quelle (aucun zoom).

    Renvoie (cx, cy, h) en fractions de la source : centre et hauteur."""
    box_h = min(1.0, box_faces * face_h) + margin
    box_w = min(1.0, box_h * src_h * box_ratio / src_w) + margin
    left, right = webcam.cx - box_w / 2, webcam.cx + box_w / 2
    top, bottom = webcam.cy - box_h / 2, webcam.cy + box_h / 2

    def size(h_frac: float) -> tuple[float, float]:
        """(largeur, hauteur) en fractions, a l'aspect de la bande."""
        w_frac = h_frac * src_h * band_aspect / src_w
        if w_frac > 1.0:
            return 1.0, src_w / (band_aspect * src_h)
        return w_frac, h_frac

    w, h = size(1.0)
    x0 = 0.5 - w / 2
    if x0 + w <= left or x0 >= right or h <= top or 0.0 >= bottom:
        return 0.5, 0.5, h                                   # deja hors de la webcam

    options = []
    # A cote (toute la hauteur si possible) : a droite ou a gauche de l'incrustation.
    for lo, hi in ((max(0.0, right), 1.0), (0.0, min(1.0, left))):
        avail = hi - lo
        if avail > 0:
            hh = min(1.0, avail * src_w / (band_aspect * src_h))
            ww, hh = size(hh)
            options.append((hh, (lo + hi) / 2, 0.5))
    # Au-dessus ou en dessous (la fenetre retrecit en hauteur).
    for lo, hi in ((0.0, max(0.0, top)), (min(1.0, bottom), 1.0)):
        avail = hi - lo
        if avail > 0:
            ww, hh = size(avail)
            options.append((hh, 0.5, (lo + hi) / 2))
    if not options:
        return 0.5, 0.5, h
    hh, cx, cy = max(options)
    return cx, cy, hh


# Liseré de l'incrustation laissé hors du jeu (fraction de la largeur).
SIDE_GAME_INSET = 0.004


def side_game_box(rect, src_w: int, src_h: int, band_aspect: float):
    """La bande du bas du mode webcam+jeu = TOUT le jeu, du bord reel de la
    webcam jusqu'au bord oppose de l'ecran, sur toute la hauteur (demande de
    l'utilisateur, 2026-11-05 : une fenetre a l'aspect de la bande, centree
    a cote de la webcam, coupait l'action d'un clip de Kamet0). Fonction pure.

    `rect` : la webcam mesuree (x0, y0, x1, y1 en fractions), collee au bord
    gauche ou droit. Renvoie la zone (x0, y0, x1, y1) a poser entiere sur
    fond flou, ou None (webcam pas sur un cote, ou zone assez etroite pour
    remplir la bande sans rien couper : le cadrage habituel suffit)."""
    if rect is None or src_w <= 0 or src_h <= 0:
        return None
    x0, _, x1, _ = rect
    if x0 <= 0.02 and x1 < 0.5:
        box = (min(1.0, x1 + SIDE_GAME_INSET), 0.0, 1.0, 1.0)
    elif x1 >= 0.98 and x0 > 0.5:
        box = (0.0, 0.0, max(0.0, x0 - SIDE_GAME_INSET), 1.0)
    else:
        return None
    width_px = (box[2] - box[0]) * src_w
    if width_px <= band_aspect * src_h:
        return None
    return box
