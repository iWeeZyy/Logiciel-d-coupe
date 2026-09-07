"""Detection locale de visage (OpenCV DNN, Res10 SSD) pour guider le recadrage 9:16.

Modele telecharge une seule fois (~28 Ko de description + ~10,7 Mo de poids)
puis mis en cache dans .cache/models/ -- reutilisable hors ligne ensuite,
exactement comme les modeles Whisper. Si le telechargement echoue et qu'aucune
copie locale n'existe, on ne fait pas planter le pipeline : cropper.py retombe
sur un crop centre (comportement explicitement demande par le cahier des charges).
"""
from __future__ import annotations

import urllib.request
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


class FaceCropHint:
    def __init__(self, x_center_frac: float, y_center_frac: float, samples_used: int):
        self.x_center_frac = x_center_frac
        self.y_center_frac = y_center_frac
        self.samples_used = samples_used


def detect_crop_hint(
    video_path: str,
    start: float,
    end: float,
    sample_interval_s: float = 1.0,
    max_samples: int = 20,
    confidence_threshold: float = 0.6,
) -> FaceCropHint | None:
    model = ensure_face_model()
    if model is None:
        logger.info("Detecteur de visage indisponible -- crop centre utilise pour ce clip.")
        return None

    try:
        import cv2
    except ImportError:
        logger.warning("opencv-python non installe -- crop centre utilise.")
        return None

    prototxt_path, model_path = model
    net = cv2.dnn.readNetFromCaffe(prototxt_path, model_path)

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        logger.warning(f"Impossible d'ouvrir '{video_path}' avec OpenCV -- crop centre utilise.")
        return None

    sample_times = []
    t = start
    while t < end and len(sample_times) < max_samples:
        sample_times.append(t)
        t += sample_interval_s

    weighted_x, weighted_y, weight_sum, samples_with_face = 0.0, 0.0, 0.0, 0

    for t in sample_times:
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
        ret, frame = cap.read()
        if not ret or frame is None:
            continue
        h, w = frame.shape[:2]
        blob = cv2.dnn.blobFromImage(cv2.resize(frame, (300, 300)), 1.0, (300, 300), (104.0, 177.0, 123.0))
        net.setInput(blob)
        detections = net.forward()

        best_conf, best_cx, best_cy = 0.0, None, None
        for i in range(detections.shape[2]):
            confidence = float(detections[0, 0, i, 2])
            if confidence < confidence_threshold or confidence <= best_conf:
                continue
            x1, y1, x2, y2 = detections[0, 0, i, 3:7]
            best_conf = confidence
            best_cx = float((x1 + x2) / 2.0)
            best_cy = float((y1 + y2) / 2.0)

        if best_cx is not None:
            weighted_x += best_cx * best_conf
            weighted_y += best_cy * best_conf
            weight_sum += best_conf
            samples_with_face += 1

    cap.release()

    if samples_with_face < 2 or weight_sum <= 0:
        logger.info("Pas assez de visages detectes de facon fiable -- crop centre utilise pour ce clip.")
        return None

    return FaceCropHint(
        x_center_frac=weighted_x / weight_sum,
        y_center_frac=weighted_y / weight_sum,
        samples_used=samples_with_face,
    )
