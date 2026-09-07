"""Generation des miniatures verticales 1080x1920 (fonctionnalite 6).

Extrait des images candidates du clip, les mesure (nettete, luminosite, visage,
yeux ouverts, transition), laisse editing/thumbnail.py les noter et choisir,
puis compose trois variantes avec un texte court tire des titres extraits.

Le texte est pose dans la bande la plus eloignee du visage, avec un contour
calcule d'apres la luminosite reelle de cette bande -- pas une couleur fixe qui
disparaitrait sur un fond clair. Aucune miniature n'est "reparee" a coups de
filtres : si aucune image n'est vraiment bonne, la moins mauvaise est proposee
telle quelle.

Toute erreur ici (OpenCV absent, police manquante, image illisible) renvoie une
liste vide : une miniature manquante ne doit jamais empecher un clip d'exister.
"""
from __future__ import annotations

from pathlib import Path

from core.logging_setup import get_logger
from core.paths import app_base_dir
from editing.thumbnail import (
    FrameMetrics,
    choose_text_layout,
    choose_variants,
    score_frames,
)
from video.cropper import TARGET_H, TARGET_W, CenterHint, compute_crop_rect
from video.face_detector import FaceBox, ensure_face_model

logger = get_logger()

_FONT_CANDIDATES = (
    app_base_dir() / "assets" / "fonts" / "JetBrainsMono-Bold.ttf",
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    Path("C:/Windows/Fonts/arialbd.ttf"),
)


def _load_font(size: int):
    from PIL import ImageFont

    for path in _FONT_CANDIDATES:
        try:
            if path.exists():
                return ImageFont.truetype(str(path), size)
        except OSError:
            continue
    return ImageFont.load_default()


def _detect_face(net, frame, confidence_threshold: float) -> FaceBox | None:
    import cv2

    blob = cv2.dnn.blobFromImage(cv2.resize(frame, (300, 300)), 1.0, (300, 300),
                                 (104.0, 177.0, 123.0))
    net.setInput(blob)
    detections = net.forward()

    best = None
    for i in range(detections.shape[2]):
        confidence = float(detections[0, 0, i, 2])
        if confidence < confidence_threshold:
            continue
        x1, y1, x2, y2 = (float(v) for v in detections[0, 0, i, 3:7])
        x1, y1, x2, y2 = max(0.0, x1), max(0.0, y1), min(1.0, x2), min(1.0, y2)
        if x2 - x1 <= 0.01 or y2 - y1 <= 0.01:
            continue
        box = FaceBox(x=x1, y=y1, w=x2 - x1, h=y2 - y1, confidence=confidence)
        if best is None or box.area > best.area:
            best = box
    return best


def _eyes_open(cascade, gray, face: FaceBox) -> bool | None:
    """Approximation volontairement modeste : la cascade de Haar reperant des
    yeux ouverts, son absence de detection ne prouve pas des yeux fermes (angle,
    lunettes, resolution). D'ou le None, traite comme "on ne sait pas" et jamais
    comme un critere eliminatoire."""
    if cascade is None:
        return None
    h, w = gray.shape[:2]
    x0, y0 = int(face.x * w), int(face.y * h)
    x1, y1 = int((face.x + face.w) * w), int((face.y + face.h * 0.65) * h)
    if x1 - x0 < 20 or y1 - y0 < 20:
        return None
    roi = gray[y0:y1, x0:x1]
    try:
        eyes = cascade.detectMultiScale(roi, scaleFactor=1.1, minNeighbors=4)
    except Exception:
        return None
    return True if len(eyes) >= 1 else None


def _compose(frame, face: FaceBox | None, text: str, out_path: Path, cfg: dict) -> bool:
    import cv2
    import numpy as np
    from PIL import Image, ImageDraw

    h, w = frame.shape[:2]
    hint = CenterHint(face.cx, max(0.0, min(1.0, face.cy + 0.05))) if face else None
    rect = compute_crop_rect(w, h, hint)
    cropped = frame[rect.y:rect.y + rect.h, rect.x:rect.x + rect.w]
    if cropped.size == 0:
        return False

    resized = cv2.resize(cropped, (TARGET_W, TARGET_H), interpolation=cv2.INTER_AREA)
    image = Image.fromarray(cv2.cvtColor(resized, cv2.COLOR_BGR2RGB))

    if text:
        gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
        band_luminance = {
            "top": float(gray[: int(TARGET_H * 0.32)].mean()),
            "bottom": float(gray[int(TARGET_H * 0.68):].mean()),
        }
        face_y = None
        if face is not None:
            face_y = (face.cy * h - rect.y) / max(1, rect.h)
            face_y = None if not (0.0 <= face_y <= 1.0) else face_y

        layout = choose_text_layout(
            face_y, band_luminance,
            face_avoid_frac=float(cfg.get("face_avoid_frac", 0.18)),
        )
        _draw_text(image, text, layout, cfg)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(out_path, format="JPEG", quality=int(cfg.get("jpeg_quality", 88)))
    return True


def _draw_text(image, text: str, layout, cfg: dict) -> None:
    from PIL import ImageDraw

    draw = ImageDraw.Draw(image)
    max_width = int(TARGET_W * layout.max_width_frac)
    size = int(cfg.get("max_font_size", 96))
    min_size = int(cfg.get("min_font_size", 44))

    # On reduit la taille jusqu'a ce que le texte tienne en deux lignes dans les
    # marges de securite : un texte qui deborde du cadre est illisible sur
    # mobile, ou pire, rogne par l'interface du reseau social.
    while size >= min_size:
        font = _load_font(size)
        lines = _wrap(draw, text, font, max_width)
        if len(lines) <= 2 and all(draw.textlength(line, font=font) <= max_width for line in lines):
            break
        size -= 6
    else:
        font = _load_font(min_size)
        lines = _wrap(draw, text, font, max_width)[:2]

    fill = (255, 255, 255) if layout.light_text else (15, 15, 15)
    stroke = (0, 0, 0) if layout.light_text else (255, 255, 255)
    stroke_width = max(3, size // 12)

    line_height = int(size * 1.18)
    total_height = line_height * len(lines)
    y = int(TARGET_H * layout.y_center_frac - total_height / 2)

    for line in lines:
        width = draw.textlength(line, font=font)
        draw.text(((TARGET_W - width) / 2, y), line, font=font, fill=fill,
                  stroke_width=stroke_width, stroke_fill=stroke)
        y += line_height


def _wrap(draw, text: str, font, max_width: int) -> list[str]:
    words, lines, current = text.split(), [], ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and draw.textlength(candidate, font=font) > max_width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def generate_thumbnails(
    video_path: str,
    start: float,
    end: float,
    out_dir: str,
    clip_stem: str,
    text: str = "",
    cfg: dict | None = None,
) -> list[str]:
    """Ecrit thumbnails/<clip_stem>_a.jpg (et _b, _c). Renvoie les chemins
    ecrits, relatifs au dossier du projet."""
    cfg = cfg or {}
    samples = int(cfg.get("candidate_frames", 24))
    if samples < 3 or end - start <= 0.2:
        return []

    try:
        import cv2
        import numpy as np
    except ImportError:
        logger.warning("opencv-python non installe -- miniatures non generees.")
        return []

    try:
        from PIL import Image  # noqa: F401 -- verifie seulement la disponibilite
    except ImportError:
        logger.warning("Pillow non installe -- miniatures non generees.")
        return []

    model = ensure_face_model()
    net = cv2.dnn.readNetFromCaffe(*model) if model else None
    try:
        cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_eye.xml")
        if cascade.empty():
            cascade = None
    except Exception:
        cascade = None

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        logger.warning(f"Impossible d'ouvrir '{video_path}' -- miniatures non generees.")
        return []

    metrics: list[FrameMetrics] = []
    frames: dict[float, object] = {}
    previous_small = None
    step = (end - start) / samples

    try:
        for i in range(samples):
            t = start + i * step
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
            ret, frame = cap.read()
            if not ret or frame is None:
                continue

            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            small = cv2.resize(gray, (64, 36)).astype("float32") / 255.0
            change = float(abs(small - previous_small).mean()) if previous_small is not None else 0.0
            previous_small = small

            face = _detect_face(net, frame, float(cfg.get("confidence_threshold", 0.6))) if net else None
            metrics.append(FrameMetrics(
                t=t,
                sharpness=float(cv2.Laplacian(gray, cv2.CV_64F).var()),
                brightness=float(gray.mean()),
                face=face,
                eyes_open=_eyes_open(cascade, gray, face) if face is not None else None,
                change_from_previous=change,
            ))
            frames[t] = frame
    finally:
        cap.release()

    variants = choose_variants(
        score_frames(metrics, min_sharpness=float(cfg.get("min_sharpness", 40.0))),
        min_gap_s=float(cfg.get("min_gap_s", 1.0)),
    )
    if not variants:
        logger.info("Aucune image exploitable pour la miniature de ce clip.")
        return []

    written: list[str] = []
    base = Path(out_dir)
    for key, choice in sorted(variants.items()):
        frame = frames.get(choice.t)
        if frame is None:
            continue
        # La variante "contexte" porte un texte plus court : l'image y raconte
        # moins, le texte doit y etre plus lisible, pas plus bavard.
        variant_text = text if key != "b" else " ".join(text.split()[:4])
        path = base / "thumbnails" / f"{clip_stem}_{key}.jpg"
        try:
            if _compose(frame, choice.metrics.face, variant_text, path, cfg):
                written.append(f"thumbnails/{path.name}")
        except Exception as e:  # noqa: BLE001 -- une miniature ratee ne casse pas un clip
            logger.warning(f"Miniature {path.name} non generee : {e}")

    return written
