"""Source deja decoupee : le clip est pris en entier.

Un clip recupere par le Radar a deja ete decoupe par quelqu'un. Y chercher un
"meilleur passage" revient a defaire ce choix -- et, mesure sur un vrai clip de
24 s, a en perdre les huit premieres secondes parce qu'une fenetre plus courte
notait un point de plus.
"""
import pytest

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
    # Le Radar met TOUJOURS whole_source : sa source est deja un clip. Sans
    # rien demander, une source reste decoupee en clips -- c'est le
    # comportement historique de l'accueil et de la ligne de commande.
    from types import SimpleNamespace

    from core.config_loader import load_settings

    radar = load_settings(SimpleNamespace(input="x.mp4", whole_source=True))
    defaut = load_settings(SimpleNamespace(input="x.mp4"))

    assert radar.whole_source is True
    assert defaut.whole_source is False


def test_the_context_detection_never_recuts_a_clip_taken_whole():
    # Verifie sur le vrai enchainement du pipeline : whole_source coupe la
    # detection du contexte, quelle que soit la configuration.
    from types import SimpleNamespace

    from core.config_loader import load_settings

    s = load_settings(SimpleNamespace(input="x.mp4", whole_source=True))
    s.editing = {"context_detection": {"enabled": True}}

    context_enabled = s.editing_module_enabled("context_detection") and not s.whole_source
    assert context_enabled is False


class TestChoixDeLAccueil:
    """« Garder toute la video » sur la page d'accueil.

    Le chemin est celui du Radar, deja ecrit et deja teste plus haut : la case
    ne fait que le demander pour une source locale. Ce qu'elle doit garantir,
    c'est qu'aucune des deux decisions devenues sans objet -- duree d'un clip,
    nombre de clips -- ne reste active ni ne soit transmise.
    """

    @pytest.fixture
    def page(self):
        import os

        pytest.importorskip("PySide6")
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        from gui.controller import AppController
        from gui.pages.home_page import HomePage

        app = QApplication.instance() or QApplication([])
        assert app is not None
        page = HomePage(AppController())
        page.selected_video_path = "x.mp4"
        return page

    def _args(self, page):
        captured = {}
        page.controller.start_analysis = lambda cli_args, **kw: captured.update(a=cli_args)
        page._on_generate_clicked()
        return captured["a"]

    def test_le_decoupage_en_clips_reste_le_defaut(self, page):
        args = self._args(page)
        assert args.whole_source is False
        assert args.nb_clips >= 1

    def test_la_case_demande_une_seule_sortie(self, page):
        page.whole_video_check.setChecked(True)
        args = self._args(page)
        assert args.whole_source is True
        assert args.nb_clips == 1

    def test_la_duree_annoncee_est_celle_de_la_source(self, page):
        page.video_duration_s = 754.0
        page.whole_video_check.setChecked(True)
        assert self._args(page).clip_duration == 754

    def test_une_duree_non_mesurable_ne_bloque_pas(self, page):
        """ffprobe peut echouer sur un fichier abime : la case doit rester
        utilisable, le pipeline prend de toute facon la fenetre entiere."""
        page.video_duration_s = None
        page.whole_video_check.setChecked(True)
        assert self._args(page).clip_duration > 0

    def test_les_champs_devenus_sans_objet_sont_grises(self, page):
        page.whole_video_check.setChecked(True)
        assert not page.duration_combo.isEnabled()
        assert not page.nb_clips_auto.isEnabled()
        assert not page.nb_clips_spin.isEnabled()

    def test_decocher_rend_les_champs(self, page):
        page.whole_video_check.setChecked(True)
        page.whole_video_check.setChecked(False)
        assert page.duration_combo.isEnabled()
        assert page.nb_clips_auto.isEnabled()

    def test_le_nombre_manuel_reste_grise_tant_qu_on_garde_tout(self, page):
        """Piege : decocher « Automatique » pendant que la case est cochee ne
        doit pas ranimer le champ."""
        page.whole_video_check.setChecked(True)
        page.nb_clips_auto.setChecked(False)
        assert not page.nb_clips_spin.isEnabled()

    def test_le_bouton_dit_ce_qu_il_va_faire(self, page):
        page.whole_video_check.setChecked(True)
        assert "TOUTE LA VIDÉO" in page.generate_btn.text()

    def test_les_autres_reglages_sont_transmis_comme_avant(self, page):
        page.whole_video_check.setChecked(True)
        args = self._args(page)
        assert args.aspect in ("9:16", "16:9")
        assert args.fill_mode in ("flou", "noir")
        assert args.fit_mode in ("recadrer", "entier")
