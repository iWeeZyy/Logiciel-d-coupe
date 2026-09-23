"""`pipeline._analyse_framing()` : une seule passe de detection, trois usages.

Ce module orchestre ce que `editing/subject.py`, `editing/framing.py` et
`video/filter_graph.py` construisent chacun de leur cote pour le mode
portrait webcam+gameplay (FIT_SPLIT_WEBCAM) : forcer le suivi meme quand la
case "Cadrage intelligent" est decochee (le suivi n'est pas optionnel dans ce
mode, c'est le mecanisme lui-meme), demander 3 visages par echantillon au
lieu de 2 (il faut voir la webcam ET le sujet dans le MEME echantillon pour
les distinguer), et renvoyer le triplet (face_hint, framing_plan,
webcam_plan) au lieu du couple historique.

Ces tests appellent `_analyse_framing` directement en substituant
`detect_face_track` (comme `test_pipeline_montage.py` substitue
`AudioAnalyzer`) : aucune video reelle n'est necessaire, seule la logique
d'orchestration est sous test -- le calcul du cadrage lui-meme est deja
couvert par `test_editing_framing.py` et `test_framing_subject.py`.
"""
from __future__ import annotations

import pipeline
from core.config_loader import Settings
from core.models import Candidate, Word
from editing.framing import FramingPlan
from video.face_detector import FaceBox, FaceSample
from video.filter_graph import FIT_SPLIT_WEBCAM


def _mot(text, start, end):
    return Word(text=text, start=start, end=end, probability=0.99)


def _candidat():
    mots = [_mot("Bonjour", 0.0, 0.4), _mot("a", 0.4, 0.5), _mot("tous", 0.5, 0.9)]
    return Candidate(start=0.0, end=20.0, text="Bonjour a tous", words=mots,
                     audio={}, text_features={})


def _face(cx: float, cy: float, area: float, confidence: float = 0.9) -> FaceBox:
    side = area ** 0.5
    return FaceBox(x=cx - side / 2, y=cy - side / 2, w=side, h=side, confidence=confidence)


def _sample(t: float, *boxes: FaceBox) -> FaceSample:
    ordered = tuple(sorted(boxes, key=lambda b: b.area, reverse=True))
    return FaceSample(t=t, faces=ordered, mouth_activity=tuple(0.0 for _ in ordered))


# Vignette (petite, quasi-constante) + sujet (grand, intermittent) -- meme
# forme que le clip reel de test_framing_subject.py, condensee.
def _echantillons_avec_webcam() -> list[FaceSample]:
    vignette = lambda: _face(0.09, 0.56, 0.0129, 0.98)
    sujet = lambda: _face(0.59, 0.45, 0.0291, 0.95)
    return [
        _sample(0.0, vignette()),
        _sample(1.0, vignette(), sujet()),
        _sample(2.0, vignette()),
        _sample(3.0, vignette(), sujet()),
        _sample(4.0, vignette()),
        _sample(5.0, vignette(), sujet()),
        _sample(6.0, vignette()),
        _sample(7.0, vignette(), sujet()),
    ]


# Deux personnes cote a cote, taille comparable : le cas legitime ou
# aucune des deux n'est une "webcam" au sens de ce module.
def _echantillons_duo() -> list[FaceSample]:
    return [_sample(float(i), _face(0.30, 0.5, 0.024), _face(0.70, 0.5, 0.021)) for i in range(8)]


class AudioAnalyzerJamaisAppele:
    """Aucun sous-reglage "active_speaker" n'est active dans ces tests : la
    fonction ne doit donc jamais consulter l'audio."""

    def mean_db(self, start, end):
        raise AssertionError("aucun signal audio ne doit etre consulte ici")


class _AppelsEnregistres:
    """Capture les arguments de chaque appel a detect_face_track sans se
    substituer completement a lui -- on veut voir CE QUI a ete demande."""

    def __init__(self, samples: list[FaceSample]):
        self.samples = samples
        self.calls: list[dict] = []

    def __call__(self, video_path, start, end, sample_interval_s, max_samples,
                confidence_threshold, max_faces):
        self.calls.append({"max_faces": max_faces})
        return self.samples


def _settings(fit_mode: str, framing_enabled: bool) -> Settings:
    return Settings(
        fit_mode=fit_mode,
        editing={"framing": {"enabled": framing_enabled}},
    )


class TestModeEntier:
    def test_rien_n_est_detecte_quand_l_image_est_gardee_entiere(self, monkeypatch):
        stub = _AppelsEnregistres(_echantillons_avec_webcam())
        monkeypatch.setattr(pipeline, "detect_face_track", stub)
        resultat = pipeline._analyse_framing(
            _settings("entier", framing_enabled=True), "video.mp4", _candidat(),
            AudioAnalyzerJamaisAppele(), face_cfg={},
        )
        assert resultat == (None, None, None)
        assert stub.calls == [], "aucune detection ne doit avoir lieu en mode entier"


class TestModeClassique:
    def test_cadrage_intelligent_desactive_ne_suit_rien(self, monkeypatch):
        """Sans le mode split, le suivi reste optionnel : la case decochee
        doit continuer a produire un framing_plan absent."""
        stub = _AppelsEnregistres(_echantillons_avec_webcam())
        monkeypatch.setattr(pipeline, "detect_face_track", stub)
        face_hint, framing_plan, webcam_plan = pipeline._analyse_framing(
            _settings("recadrer", framing_enabled=False), "video.mp4", _candidat(),
            AudioAnalyzerJamaisAppele(), face_cfg={"enabled": True},
        )
        assert framing_plan is None
        assert webcam_plan is None

    def test_cadrage_intelligent_active_suit_deux_visages_au_plus(self, monkeypatch):
        stub = _AppelsEnregistres(_echantillons_avec_webcam())
        monkeypatch.setattr(pipeline, "detect_face_track", stub)
        face_hint, framing_plan, webcam_plan = pipeline._analyse_framing(
            _settings("recadrer", framing_enabled=True), "video.mp4", _candidat(),
            AudioAnalyzerJamaisAppele(), face_cfg={},
        )
        assert stub.calls == [{"max_faces": 2}]
        assert isinstance(framing_plan, FramingPlan)
        assert webcam_plan is None, "webcam_plan doit rester None hors mode split"


class TestModeSplitWebcam:
    def test_le_suivi_est_force_meme_case_decochee(self, monkeypatch):
        """Le coeur du bug potentiel : en mode split, le suivi n'est pas un
        confort optionnel derriere une case a cocher, c'est le mecanisme."""
        stub = _AppelsEnregistres(_echantillons_avec_webcam())
        monkeypatch.setattr(pipeline, "detect_face_track", stub)
        face_hint, framing_plan, webcam_plan = pipeline._analyse_framing(
            _settings(FIT_SPLIT_WEBCAM, framing_enabled=False), "video.mp4", _candidat(),
            AudioAnalyzerJamaisAppele(), face_cfg={},
        )
        assert stub.calls, "detect_face_track doit avoir ete appele malgre la case decochee"
        assert isinstance(framing_plan, FramingPlan)
        assert isinstance(webcam_plan, FramingPlan)

    def test_trois_visages_sont_demandes_par_echantillon(self, monkeypatch):
        stub = _AppelsEnregistres(_echantillons_avec_webcam())
        monkeypatch.setattr(pipeline, "detect_face_track", stub)
        pipeline._analyse_framing(
            _settings(FIT_SPLIT_WEBCAM, framing_enabled=True), "video.mp4", _candidat(),
            AudioAnalyzerJamaisAppele(), face_cfg={},
        )
        assert stub.calls == [{"max_faces": 3}]

    def test_le_triplet_complet_est_renvoye_quand_la_webcam_est_identifiable(self, monkeypatch):
        stub = _AppelsEnregistres(_echantillons_avec_webcam())
        monkeypatch.setattr(pipeline, "detect_face_track", stub)
        face_hint, framing_plan, webcam_plan = pipeline._analyse_framing(
            _settings(FIT_SPLIT_WEBCAM, framing_enabled=True), "video.mp4", _candidat(),
            AudioAnalyzerJamaisAppele(), face_cfg={},
        )
        assert face_hint is not None
        assert isinstance(framing_plan, FramingPlan)
        assert isinstance(webcam_plan, FramingPlan)

    def test_webcam_plan_reste_none_sans_webcam_identifiable(self, monkeypatch):
        """Deux personnes de taille comparable : aucune n'est une incrustation
        -- c'est le signal que le pipeline doit utiliser pour retomber sur le
        cadrage classique (choix confirme par l'utilisateur)."""
        stub = _AppelsEnregistres(_echantillons_duo())
        monkeypatch.setattr(pipeline, "detect_face_track", stub)
        face_hint, framing_plan, webcam_plan = pipeline._analyse_framing(
            _settings(FIT_SPLIT_WEBCAM, framing_enabled=True), "video.mp4", _candidat(),
            AudioAnalyzerJamaisAppele(), face_cfg={},
        )
        assert webcam_plan is None
        assert isinstance(framing_plan, FramingPlan), (
            "le cadrage classique du duo, lui, doit rester disponible pour le repli"
        )
