"""Detection des visages : YuNet livre avec l'application, Res10 en repli.

Mesure sur un vrai clip Twitch (streamer filme de loin, perruque, lunettes
de soleil) : Res10 trouvait le visage sur 6 images sur 60, YuNet sur 52."""
import numpy as np
import pytest

from video import face_detector as fd

cv2 = pytest.importorskip("cv2")


def test_le_modele_est_livre_avec_l_application():
    path = fd.yunet_model_path()
    assert path.is_file() and path.stat().st_size > 100_000
    assert (path.parent / "face_detection_yunet-LICENSE.txt").is_file()


@pytest.mark.skipif(not hasattr(cv2, "FaceDetectorYN"), reason="OpenCV sans YuNet")
def test_yunet_choisi_en_premier():
    assert isinstance(fd.load_face_detector(), fd.YuNetDetector)


@pytest.mark.skipif(not hasattr(cv2, "FaceDetectorYN"), reason="OpenCV sans YuNet")
def test_image_sans_visage_et_tailles_variees():
    detector = fd.load_face_detector()
    for shape in ((1080, 1920, 3), (1920, 1080, 3), (300, 400, 3)):
        assert fd.detect_faces_in_frame(detector, np.zeros(shape, dtype=np.uint8)) == []


def test_repli_res10_si_yunet_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(fd, "yunet_model_path", lambda: tmp_path / "absent.onnx")
    monkeypatch.setattr(fd, "ensure_face_model", lambda: None)
    assert fd.load_face_detector() is None          # ni YuNet ni Res10 : cadrage centre
