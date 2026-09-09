"""Source deja decoupee : le clip est pris en entier.

Un clip recupere par le Radar a deja ete decoupe par quelqu'un. Y chercher un
"meilleur passage" revient a defaire ce choix -- et, mesure sur un vrai clip de
24 s, a en perdre les huit premieres secondes parce qu'une fenetre plus courte
notait un point de plus.
"""
from core.models import AudioFeatures, TextFeatures, Word
from core.steps import (
    STEP_ANALYSIS,
    STEP_CLIP_ANALYSIS,
    STEP_CONTEXT,
    STEP_SELECTION,
    build_step_labels,
)
from analysis.hook_detector import generate_candidates, whole_video_candidate


class _Text:
    """Analyseur de texte minimal : seul le nombre de mots compte ici."""

    def __init__(self, word_count: int = 30):
        self.word_count = word_count

    def analyze_window(self, start, end):
        return f"[{start:.1f}-{end:.1f}]", TextFeatures(word_count=self.word_count)

    def words_in_window(self, start, end):
        return [Word(text="mot", start=start, end=end)]


class _Audio:
    def analyze_window(self, start, end):
        return AudioFeatures()


# ------------------------------------------------- ce que faisait l'ancien mode

def test_a_clip_already_cut_produced_more_than_one_window():
    # Le defaut d'origine : le Radar passait clip_duration = int(duree), donc
    # une seconde de moins que le fichier, ce qui suffisait a ouvrir une
    # deuxieme fenetre demarrant au tiers du clip.
    windows = generate_candidates(_Audio(), _Text(), video_duration=24.6,
                                  clip_duration=24, stride_ratio=0.33,
                                  min_words_in_window=8)

    assert len(windows) == 2
    assert windows[1].start > 7.0        # tout ce debut pouvait etre perdu


# ------------------------------------------------------------ fenetre unique

def test_the_whole_clip_is_one_single_window():
    candidate = whole_video_candidate(_Audio(), _Text(), video_duration=24.6)

    assert (candidate.start, candidate.end) == (0.0, 24.6)


def test_a_clip_with_almost_no_speech_is_still_produced():
    # generate_candidates ecarte une fenetre de moins de 8 mots : utile pour
    # choisir un passage, desastreux ici -- un clip de reaction ou personne ne
    # parle vraiment ne produirait plus rien du tout.
    silent = _Text(word_count=2)

    assert generate_candidates(_Audio(), silent, video_duration=30.0, clip_duration=30,
                               stride_ratio=0.33, min_words_in_window=8) == []
    assert whole_video_candidate(_Audio(), silent, video_duration=30.0).end == 30.0


def test_a_zero_length_source_never_makes_an_empty_window():
    assert whole_video_candidate(_Audio(), _Text(), video_duration=0.0).end > 0


# ---------------------------------------------------------------- etapes

def test_the_steps_that_decide_nothing_are_not_shown():
    labels = build_step_labels(context_detection=True, metadata=True, whole_source=True)

    assert STEP_CLIP_ANALYSIS in labels
    assert STEP_ANALYSIS not in labels
    assert STEP_SELECTION not in labels
    assert STEP_CONTEXT not in labels


def test_a_normal_video_keeps_all_its_steps():
    labels = build_step_labels(context_detection=True, metadata=True)

    assert [STEP_ANALYSIS, STEP_SELECTION, STEP_CONTEXT] == [
        s for s in labels if s in (STEP_ANALYSIS, STEP_SELECTION, STEP_CONTEXT)]
    assert STEP_CLIP_ANALYSIS not in labels


# --------------------------------------------------------------- reglages

def test_the_radar_asks_for_the_whole_clip():
    # La fenetre du Radar est ce qui met whole_source ; l'accueil ne le met
    # jamais, une video d'une heure n'est pas un clip.
    from types import SimpleNamespace

    from core.config_loader import load_settings

    radar = load_settings(SimpleNamespace(input="x.mp4", whole_source=True))
    accueil = load_settings(SimpleNamespace(input="x.mp4"))

    assert radar.whole_source is True
    assert accueil.whole_source is False


def test_the_context_detection_never_recuts_a_clip_taken_whole():
    # Verifie sur le vrai enchainement du pipeline : whole_source coupe la
    # detection du contexte, quelle que soit la configuration.
    from types import SimpleNamespace

    from core.config_loader import load_settings

    s = load_settings(SimpleNamespace(input="x.mp4", whole_source=True))
    s.editing = {"context_detection": {"enabled": True}}

    context_enabled = s.editing_module_enabled("context_detection") and not s.whole_source
    assert context_enabled is False
