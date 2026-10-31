"""Reconnaitre UNE personne donnee parmi les visages d'un clip.

Demande de l'utilisateur (2026-10-31, compilation de Squeezie a cinq sur un
plateau) : que le cadrage suive le streamer. Il fournit une photo de
reference ; quand ce visage est a l'ecran, le cadrage le suit, sinon il suit
la personne qui parle (editing/speaker.py), comme avant.

Modele : SFace (OpenCV Zoo, licence Apache 2.0), via cv2.FaceRecognizerSF,
le pendant de YuNet pour la reconnaissance. Il n'est PAS livre avec
l'application (39 Mo) : telecharge une fois dans le dossier des modeles. Sans
lui, rien ne change -- le cadrage suit la personne qui parle.

Le visage est aligne sur l'image d'ORIGINE (reperes de YuNet ramenes a sa
resolution), pas sur l'image reduite a 960 px de la detection : sur un
plateau large, un visage n'y fait qu'une trentaine de pixels, trop peu pour
le reconnaitre.
"""
from __future__ import annotations

from pathlib import Path

from core.logging_setup import get_logger

logger = get_logger()

SFACE_FILE = "face_recognition_sface_2021dec.onnx"
SFACE_URL = ("https://huggingface.co/opencv/face_recognition_sface/resolve/main/"
             + SFACE_FILE)
# Seuil de similarite cosinus d'OpenCV pour SFace (« meme personne » au-dessus
# de 0,363 sur LFW) ; un peu plus bas pour des images de stream compressees.
MATCH_THRESHOLD = 0.30


def model_path() -> Path:
    from video.face_detector import MODELS_DIR

    return MODELS_DIR / SFACE_FILE


def ensure_model() -> Path | None:
    path = model_path()
    if path.is_file():
        return path
    from video.face_detector import _download

    logger.info("Téléchargement du modèle de reconnaissance des visages (SFace, 39 Mo)…")
    return path if _download(SFACE_URL, path) else None


def cosine(a, b) -> float:
    """Similarite cosinus de deux vecteurs (fonction pure)."""
    import numpy as np

    a, b = np.asarray(a, dtype="float32").ravel(), np.asarray(b, dtype="float32").ravel()
    norm = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(np.dot(a, b) / norm) if norm else 0.0


class IdentityMatcher:
    """Les empreintes de la personne a suivre, et la mesure d'un visage."""

    def __init__(self, references: list, recognizer):
        self.references = references
        self.recognizer = recognizer

    def score(self, frame, face_row) -> float:
        """Similarite du visage `face_row` (ligne YuNet, en pixels de `frame`)."""
        try:
            aligned = self.recognizer.alignCrop(frame, face_row)
            feature = self.recognizer.feature(aligned)
        except Exception:  # noqa: BLE001 -- visage coupe au bord, etc.
            return -1.0
        return max(cosine(feature, ref) for ref in self.references)


def load_matcher(photo_paths) -> IdentityMatcher | None:
    """Matcher pour les photos de reference, ou None (pas de photo lisible,
    pas de visage dedans, modele indisponible) -- jamais d'exception."""
    paths = [Path(p) for p in (photo_paths or []) if Path(p).is_file()]
    if not paths:
        return None
    model = ensure_model()
    if model is None:
        return None
    try:
        import cv2

        from video.face_detector import yunet_model_path

        recognizer = cv2.FaceRecognizerSF.create(str(model), "")
        references = []
        for path in paths:
            image = cv2.imread(str(path))
            if image is None:
                continue
            h, w = image.shape[:2]
            detector = cv2.FaceDetectorYN.create(str(yunet_model_path()), "", (w, h), 0.6, 0.3, 50)
            _, faces = detector.detect(image)
            if faces is None or not len(faces):
                logger.warning(f"Aucun visage sur la photo de référence {path.name}.")
                continue
            face = max(faces, key=lambda f: f[2] * f[3])
            references.append(recognizer.feature(recognizer.alignCrop(image, face)))
    except Exception as e:  # noqa: BLE001 -- reconnaissance indisponible : suivi habituel
        logger.warning(f"Reconnaissance du visage indisponible : {e}")
        return None
    if not references:
        return None
    logger.info(f"Visage à suivre : {len(references)} photo(s) de référence.")
    return IdentityMatcher(references, recognizer)


def keep_target(samples, threshold: float = MATCH_THRESHOLD, min_presence: float = 0.15):
    """Echantillons ou la personne reconnue est seule gardee ; les autres
    restent intacts (le cadrage y suit la personne qui parle). Si elle n'est
    reconnue que tres rarement (< `min_presence`), rien n'est change : un
    faux positif isole ne doit pas tirer le cadrage."""
    from dataclasses import replace

    samples = list(samples)
    hits = 0
    out = []
    for sample in samples:
        faces = list(getattr(sample, "faces", ()) or ())
        scored = [(f, getattr(f, "identity", -1.0)) for f in faces]
        best = max(scored, key=lambda p: p[1], default=(None, -1.0))
        if best[0] is not None and best[1] >= threshold:
            hits += 1
            index = faces.index(best[0])
            activity = sample.mouth_activity[index:index + 1] if sample.mouth_activity else ()
            out.append(replace(sample, faces=(best[0],), mouth_activity=activity))
        else:
            out.append(sample)
    with_faces = sum(1 for s in samples if getattr(s, "faces", ()))
    if not with_faces or hits / with_faces < min_presence:
        return samples, 0.0
    return out, hits / with_faces
