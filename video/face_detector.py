"""Detection locale de visages (OpenCV DNN, Res10 SSD) pour guider le recadrage 9:16.

Modele telecharge une seule fois (~28 Ko de description + ~10,7 Mo de poids)
puis mis en cache dans .cache/models/ -- reutilisable hors ligne ensuite,
exactement comme les modeles Whisper. Si le telechargement echoue et qu'aucune
copie locale n'existe, on ne fait pas planter le pipeline : le recadrage
retombe sur un crop centre (comportement explicitement demande par le cahier
des charges).

Deux niveaux de sortie, produits par UNE SEULE passe de detection :

- `detect_face_track()` : la position de chaque visage a chaque instant
  echantillonne, plus une mesure d'activite de la bouche. C'est ce que
  consomment le suivi du sujet (editing/framing.py) et la detection du
  locuteur actif (editing/speaker.py).
- `detect_crop_hint()` : la moyenne ponderee historique, pour un cadrage fixe.
  Reconstruite a partir de la meme passe -- pas de seconde boucle OpenCV.
"""
from __future__ import annotations

import urllib.request
from dataclasses import dataclass
from pathlib import Path

from core.logging_setup import get_logger
from core.paths import app_base_dir

logger = get_logger()

MODELS_DIR = app_base_dir() / ".cache" / "models"
PROTOTXT_URL = "https://raw.githubusercontent.com/opencv/opencv/4.x/samples/dnn/face_detector/deploy.prototxt"
CAFFEMODEL_URL = (
    "https://raw.githubusercontent.com/opencv/opencv_3rdparty/"
    "dnn_samples_face_detector_20170830/res10_300x300_ssd_iter_140000.caffemodel"
)
PROTOTXT_PATH = MODELS_DIR / "deploy.prototxt"
CAFFEMODEL_PATH = MODELS_DIR / "res10_300x300_ssd_iter_140000.caffemodel"

# Taille a laquelle la zone de bouche est ramenee avant comparaison : la boite
# du visage change de taille d'un echantillon a l'autre, une difference pixel a
# pixel n'aurait aucun sens sans cette normalisation.
_MOUTH_PATCH = (32, 24)


def _download(url: str, dest: Path) -> bool:
    try:
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        urllib.request.urlretrieve(url, tmp)
        tmp.rename(dest)
        return True
    except Exception as e:
        logger.warning(f"Telechargement du modele de detection de visage echoue ({url}) : {e}")
        return False


def ensure_face_model() -> tuple[str, str] | None:
    if not PROTOTXT_PATH.exists():
        if not _download(PROTOTXT_URL, PROTOTXT_PATH):
            return None
    if not CAFFEMODEL_PATH.exists():
        if not _download(CAFFEMODEL_URL, CAFFEMODEL_PATH):
            return None
    return str(PROTOTXT_PATH), str(CAFFEMODEL_PATH)


@dataclass(frozen=True)
class FaceBox:
    """Un visage, en fractions de l'image (0..1), independant de la resolution."""

    x: float
    y: float
    w: float
    h: float
    confidence: float

    @property
    def cx(self) -> float:
        return self.x + self.w / 2.0

    @property
    def cy(self) -> float:
        return self.y + self.h / 2.0

    @property
    def area(self) -> float:
        return max(0.0, self.w) * max(0.0, self.h)


@dataclass(frozen=True)
class FaceSample:
    """Les visages vus a un instant donne, tries du plus grand au plus petit.

    `mouth_activity` a la meme longueur que `faces` : mouvement de la zone de
    bouche depuis l'echantillon precedent (0 pour le premier echantillon)."""

    t: float
    faces: tuple[FaceBox, ...]
    mouth_activity: tuple[float, ...] = ()


class FaceCropHint:
    def __init__(self, x_center_frac: float, y_center_frac: float, samples_used: int):
        self.x_center_frac = x_center_frac
        self.y_center_frac = y_center_frac
        self.samples_used = samples_used


def _sample_times(start: float, end: float, interval: float, max_samples: int) -> list[float]:
    times: list[float] = []
    t = start
    interval = max(0.05, interval)
    while t < end and len(times) < max_samples:
        times.append(t)
        t += interval
    return times


def _mouth_patch(frame, box: FaceBox):
    """Zone de bouche (tiers inferieur du visage), en niveaux de gris et a
    taille fixe, prete a etre comparee d'un echantillon a l'autre."""
    import cv2

    h, w = frame.shape[:2]
    x0 = int(max(0, (box.x + 0.20 * box.w) * w))
    x1 = int(min(w, (box.x + 0.80 * box.w) * w))
    y0 = int(max(0, (box.y + 0.60 * box.h) * h))
    y1 = int(min(h, (box.y + 1.00 * box.h) * h))
    if x1 - x0 < 4 or y1 - y0 < 4:
        return None
    patch = frame[y0:y1, x0:x1]
    gray = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
    return cv2.resize(gray, _MOUTH_PATCH).astype("float32") / 255.0


def _match_previous(box: FaceBox, previous: list[tuple[FaceBox, object]]) -> object | None:
    """Retrouve le meme visage a l'echantillon precedent, par proximite du
    centre. Suffisant ici : entre deux echantillons rapproches, un visage se
    deplace beaucoup moins que la distance qui separe deux personnes."""
    best, best_distance = None, 0.25
    for prev_box, patch in previous:
        distance = ((box.cx - prev_box.cx) ** 2 + (box.cy - prev_box.cy) ** 2) ** 0.5
        if distance < best_distance:
            best, best_distance = patch, distance
    return best


def detect_face_track(
    video_path: str,
    start: float,
    end: float,
    sample_interval_s: float = 0.5,
    max_samples: int = 160,
    confidence_threshold: float = 0.6,
    max_faces: int = 2,
) -> list[FaceSample]:
    """Position des visages au fil du clip. Liste vide si le modele, OpenCV ou
    la video ne sont pas exploitables -- l'appelant retombe alors sur un
    cadrage fixe, jamais sur une erreur."""
    model = ensure_face_model()
    if model is None:
        logger.info("Detecteur de visage indisponible -- cadrage fixe utilise.")
        return []

    try:
        import cv2
    except ImportError:
        logger.warning("opencv-python non installe -- cadrage fixe utilise.")
        return []

    prototxt_path, model_path = model
    net = cv2.dnn.readNetFromCaffe(prototxt_path, model_path)

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        logger.warning(f"Impossible d'ouvrir '{video_path}' avec OpenCV -- cadrage fixe utilise.")
        return []

    samples: list[FaceSample] = []
    previous: list[tuple[FaceBox, object]] = []

    try:
        for t in _sample_times(start, end, sample_interval_s, max_samples):
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
            ret, frame = cap.read()
            if not ret or frame is None:
                continue

            blob = cv2.dnn.blobFromImage(cv2.resize(frame, (300, 300)), 1.0, (300, 300),
                                         (104.0, 177.0, 123.0))
            net.setInput(blob)
            detections = net.forward()

            boxes: list[FaceBox] = []
            for i in range(detections.shape[2]):
                confidence = float(detections[0, 0, i, 2])
                if confidence < confidence_threshold:
                    continue
                x1, y1, x2, y2 = (float(v) for v in detections[0, 0, i, 3:7])
                x1, y1 = max(0.0, x1), max(0.0, y1)
                x2, y2 = min(1.0, x2), min(1.0, y2)
                if x2 - x1 <= 0.01 or y2 - y1 <= 0.01:
                    continue
                boxes.append(FaceBox(x=x1, y=y1, w=x2 - x1, h=y2 - y1, confidence=confidence))

            boxes.sort(key=lambda b: b.area, reverse=True)
            boxes = boxes[:max_faces]

            activity: list[float] = []
            current: list[tuple[FaceBox, object]] = []
            for box in boxes:
                patch = _mouth_patch(frame, box)
                if patch is None:
                    activity.append(0.0)
                    continue
                previous_patch = _match_previous(box, previous)
                if previous_patch is None:
                    activity.append(0.0)
                else:
                    activity.append(float(abs(patch - previous_patch).mean()))
                current.append((box, patch))

            previous = current
            samples.append(FaceSample(t=t, faces=tuple(boxes), mouth_activity=tuple(activity)))
    finally:
        cap.release()

    return samples


def detect_crop_hint(
    video_path: str,
    start: float,
    end: float,
    sample_interval_s: float = 1.0,
    max_samples: int = 20,
    confidence_threshold: float = 0.6,
) -> FaceCropHint | None:
    """Position moyenne du visage principal sur le clip (cadrage fixe).

    Construite a partir de la meme passe de detection que le suivi : il n'y a
    qu'une implementation de la detection, pas deux."""
    samples = detect_face_track(
        video_path, start, end,
        sample_interval_s=sample_interval_s,
        max_samples=max_samples,
        confidence_threshold=confidence_threshold,
        max_faces=1,
    )
    return crop_hint_from_track(samples)


def crop_hint_from_track(samples: list[FaceSample]) -> FaceCropHint | None:
    weighted_x = weighted_y = weight_sum = 0.0
    used = 0
    for sample in samples:
        if not sample.faces:
            continue
        box = sample.faces[0]
        weighted_x += box.cx * box.confidence
        weighted_y += box.cy * box.confidence
        weight_sum += box.confidence
        used += 1

    if used < 2 or weight_sum <= 0:
        logger.info("Pas assez de visages detectes de facon fiable -- crop centre utilise pour ce clip.")
        return None

    return FaceCropHint(
        x_center_frac=weighted_x / weight_sum,
        y_center_frac=weighted_y / weight_sum,
        samples_used=used,
    )
