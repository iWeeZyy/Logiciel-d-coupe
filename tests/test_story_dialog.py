"""Dialogue "Créer une Story" : recuperation d'image hors du fil de
l'interface, apercu recompose en tache de fond, gabarits, editions
manuelles, export, et le cas "aucune image trouvee".

Le vrai reseau (candidates_for_article, requests.get de image_cache) est
systematiquement monkeypatche -- ces deux fonctions sont deja testees pour
elles-memes ailleurs (test_news_story_image_fetcher.py,
test_news_story_image_cache.py) ; ici on verifie seulement que le dialogue
les orchestre correctement.
"""
import os
import time
from io import BytesIO
from unittest.mock import patch

import pytest


@pytest.fixture
def app():
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _isolated_image_cache(tmp_path, monkeypatch):
    # news_story.image_cache met en cache par URL sur le VRAI disque
    # utilisateur (user_data_dir()) : plusieurs tests de ce fichier reutilisent
    # la meme URL factice ("https://example.test/img.jpg") avec des contenus
    # differents (resolution normale vs faible) -- sans cette isolation, un
    # test recupererait silencieusement le fichier mis en cache par un test
    # precedent au lieu du contenu qu'il vient de simuler.
    import news_story.image_cache as image_cache

    monkeypatch.setattr(image_cache, "CACHE_DIR", tmp_path / "news_images")


@pytest.fixture(autouse=True)
def _no_real_message_boxes(app, monkeypatch):
    # QMessageBox.information()/warning() sont modales et bloquent en
    # attendant un clic reel -- sans affichage (offscreen), rien ne peut
    # jamais cliquer, ce qui fige le test pour de bon. Un risque reel meme
    # pour un test qui ne les appelle pas lui-meme : un signal `ready`/`failed`
    # emis par un QThread d'un test PRECEDENT peut rester en file d'attente
    # (connexion inter-fils) et n'etre livre qu'au prochain appel a
    # app.processEvents() -- potentiellement dans le test SUIVANT, une fois le
    # patch local de ce test-la retombe. Patcher les deux au niveau du
    # fichier entier, plutot qu'au cas par cas, ferme ce risque une fois pour
    # toutes.
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))


def _process_until(app, condition, timeout_s=4.0, step_s=0.01):
    # time.sleep(), pas QThread.msleep() sur l'instance du thread principal :
    # cette derniere a bloque la livraison des signaux en file d'attente dans
    # cet environnement (deadlock constate), le sommeil simple n'a pas ce
    # probleme puisqu'il rend la main a app.processEvents() normalement.
    deadline = time.time() + timeout_s
    while not condition() and time.time() < deadline:
        app.processEvents()
        time.sleep(step_s)
    return condition()


def _fake_article(title="Un nouveau jeu de plateforme annonce", summary="Un résumé"):
    from gaming_news.models import Article

    return Article(source_key="vgc", source_label="VGC", title=title, summary=summary,
                   url="https://example.test/article", published_at="")


def _jpeg_bytes(size=(1200, 800), color=(50, 80, 120)):
    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", size, color).save(buf, format="JPEG")
    return buf.getvalue()


class _FakeResponse:
    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        pass


def _patched_single_candidate(image_bytes=None):
    """Contexte qui simule une seule candidate d'image reussie, sans reseau.

    Patche "gui.radar.story_dialog.candidates_for_article", PAS
    "news_story.image_fetcher.candidates_for_article" : story_dialog.py fait
    `from news_story.image_fetcher import candidates_for_article`, un nom lie
    une seule fois AU PREMIER IMPORT du module (mis en cache dans
    sys.modules). Patcher le module source n'a alors plus aucun effet des le
    second test de ce fichier -- seul le nom tel qu'il est REELEMENT UTILISE
    peut etre remplace de facon fiable a chaque test."""
    import news_story.image_fetcher as image_fetcher

    image_bytes = image_bytes or _jpeg_bytes()

    def fake_candidates(url, feed_image_url="", timeout_s=10):
        return [image_fetcher.ImageCandidate(url="https://example.test/img.jpg", source="og:image", priority=0)]

    return (
        patch("gui.radar.story_dialog.candidates_for_article", fake_candidates),
        patch("requests.get", lambda *a, **k: _FakeResponse(image_bytes)),
    )


class TestFetchFlow:
    def test_a_successful_fetch_selects_the_first_candidate_and_enables_export(self, app):
        p1, p2 = _patched_single_candidate()
        with p1, p2:
            from gui.radar.story_dialog import StoryDialog

            dialog = StoryDialog(_fake_article())
            assert _process_until(app, lambda: dialog.export_btn.isEnabled())
            assert dialog._selected_index == 0
            assert len(dialog._candidates) == 1
            dialog.cleanup()

    def test_no_candidates_shows_a_clear_message_and_never_enables_export(self, app):
        with patch("gui.radar.story_dialog.candidates_for_article", lambda *a, **k: []):
            from gui.radar.story_dialog import StoryDialog

            dialog = StoryDialog(_fake_article())
            assert _process_until(app, lambda: "Aucune image" in dialog.status_label.text())
            assert not dialog.export_btn.isEnabled()
            dialog.cleanup()

    def test_a_download_failure_on_one_candidate_does_not_block_the_others(self, app):
        import news_story.image_fetcher as image_fetcher

        def fake_candidates(url, feed_image_url="", timeout_s=10):
            return [
                image_fetcher.ImageCandidate(url="https://example.test/broken.jpg", source="og:image", priority=0),
                image_fetcher.ImageCandidate(url="https://example.test/good.jpg", source="twitter:image", priority=1),
            ]

        def fake_get(url, *a, **k):
            if "broken" in url:
                return _FakeResponse(b"pas une image")
            return _FakeResponse(_jpeg_bytes())

        with patch("gui.radar.story_dialog.candidates_for_article", fake_candidates), \
             patch("requests.get", fake_get):
            from gui.radar.story_dialog import StoryDialog

            dialog = StoryDialog(_fake_article())
            assert _process_until(app, lambda: dialog.export_btn.isEnabled())
            assert len(dialog._candidates) == 1  # seule la bonne a survecu
            dialog.cleanup()


class TestRightsNotice:
    def test_the_rights_notice_is_shown_in_the_dialog(self, app):
        p1, p2 = _patched_single_candidate()
        with p1, p2:
            from gui.radar.story_dialog import RIGHTS_NOTICE, StoryDialog

            dialog = StoryDialog(_fake_article())
            assert dialog.rights_label.text() == RIGHTS_NOTICE
            dialog.cleanup()

    def test_the_rights_notice_never_reaches_the_composed_image(self, app, tmp_path):
        # Le compositeur (news_story.story_composer) ne connait meme pas
        # cette mention -- verifie que le fichier d'apercu compose ne la
        # contient jamais, meme sous forme d'octets bruts.
        p1, p2 = _patched_single_candidate()
        with p1, p2:
            from gui.radar.story_dialog import RIGHTS_NOTICE, StoryDialog

            dialog = StoryDialog(_fake_article())
            assert _process_until(app, lambda: dialog.export_btn.isEnabled())
            with open(dialog._preview_path, "rb") as f:
                raw = f.read()
            assert RIGHTS_NOTICE.encode("utf-8") not in raw
            dialog.cleanup()


class TestTemplateAndTitleEditing:
    def test_the_title_field_is_prefilled_with_the_title_and_summary_combined(self, app):
        """Le champ prerempli montre desormais ce qui sera reellement
        compose sur l'image -- titre PUIS resume (voir
        title_shortener.build_display_title) -- pas seulement le titre,
        souvent un teaser sans l'information elle-meme."""
        p1, p2 = _patched_single_candidate()
        with p1, p2:
            from gui.radar.story_dialog import StoryDialog

            article = _fake_article(title="Un titre tout a fait normal", summary="Un résumé")
            dialog = StoryDialog(article)
            assert dialog.title_edit.text() == "Un titre tout a fait normal. Un résumé"
            dialog.cleanup()

    def test_sans_resume_le_champ_ne_montre_que_le_titre(self, app):
        p1, p2 = _patched_single_candidate()
        with p1, p2:
            from gui.radar.story_dialog import StoryDialog

            article = _fake_article(title="Un titre tout a fait normal", summary="")
            dialog = StoryDialog(article)
            assert dialog.title_edit.text() == "Un titre tout a fait normal"
            dialog.cleanup()

    def test_switching_template_does_not_erase_a_manual_title_edit(self, app):
        from news_story.story_templates import TEMPLATE_BREAKING

        p1, p2 = _patched_single_candidate()
        with p1, p2:
            from gui.radar.story_dialog import StoryDialog

            dialog = StoryDialog(_fake_article())
            dialog.title_edit.setText("Titre choisi a la main")
            dialog.title_edit.textEdited.emit("Titre choisi a la main")

            index = [dialog.template_combo.itemData(i) for i in range(dialog.template_combo.count())].index(
                TEMPLATE_BREAKING)
            dialog.template_combo.setCurrentIndex(index)

            assert dialog.title_edit.text() == "Titre choisi a la main"
            dialog.cleanup()

    def test_the_image_template_disables_the_title_field(self, app):
        from news_story.story_templates import TEMPLATE_IMAGE

        p1, p2 = _patched_single_candidate()
        with p1, p2:
            from gui.radar.story_dialog import StoryDialog

            dialog = StoryDialog(_fake_article())
            index = [dialog.template_combo.itemData(i) for i in range(dialog.template_combo.count())].index(
                TEMPLATE_IMAGE)
            dialog.template_combo.setCurrentIndex(index)

            assert not dialog.title_edit.isEnabled()
            dialog.cleanup()


class TestLowResolutionWarning:
    def test_a_low_resolution_image_shows_the_warning(self, app):
        p1, p2 = _patched_single_candidate(image_bytes=_jpeg_bytes(size=(100, 80)))
        with p1, p2:
            from gui.radar.story_dialog import StoryDialog

            dialog = StoryDialog(_fake_article())
            assert _process_until(app, lambda: dialog.export_btn.isEnabled())
            # isHidden() reflete le drapeau explicite pose par le code, pas la
            # visibilite reelle a l'ecran (toujours fausse pour une QDialog
            # jamais montree via show()/exec() dans ce test hors-ecran).
            assert not dialog.low_res_label.isHidden()
            dialog.cleanup()

    def test_a_normal_resolution_image_hides_the_warning(self, app):
        p1, p2 = _patched_single_candidate()
        with p1, p2:
            from gui.radar.story_dialog import StoryDialog

            dialog = StoryDialog(_fake_article())
            assert _process_until(app, lambda: dialog.export_btn.isEnabled())
            assert dialog.low_res_label.isHidden()
            dialog.cleanup()


class TestExport:
    def test_export_writes_a_valid_story_file(self, app, tmp_path):
        from PIL import Image

        p1, p2 = _patched_single_candidate()
        with p1, p2:
            from gui.radar.story_dialog import StoryDialog

            dialog = StoryDialog(_fake_article())
            assert _process_until(app, lambda: dialog.export_btn.isEnabled())

            out_path = str(tmp_path / "export.png")
            with patch("PySide6.QtWidgets.QFileDialog.getSaveFileName", return_value=(out_path, "")), \
                 patch("PySide6.QtWidgets.QMessageBox.information", lambda *a, **k: None):
                dialog._export()
                assert _process_until(app, lambda: os.path.exists(out_path) and os.path.getsize(out_path) > 0)

            with Image.open(out_path) as im:
                assert im.size == (1080, 1920)
            dialog.cleanup()

    def test_cancelling_the_save_dialog_does_not_export_anything(self, app):
        p1, p2 = _patched_single_candidate()
        with p1, p2:
            from gui.radar.story_dialog import StoryDialog

            dialog = StoryDialog(_fake_article())
            assert _process_until(app, lambda: dialog.export_btn.isEnabled())

            with patch("PySide6.QtWidgets.QFileDialog.getSaveFileName", return_value=("", "")):
                dialog._export()
            assert dialog.export_btn.isEnabled()  # jamais desactive pour un export qui n'a pas eu lieu
            dialog.cleanup()


class TestPostDuFil:
    """Gabarit POST 4:5 : propose d'emblee pour le fil cinema, titre seul,
    legende a copier."""

    def test_le_fil_cinema_propose_le_post_avec_le_titre_seul(self, app):
        from news_story.story_templates import TEMPLATE_POST_VERTICAL as TEMPLATE_POST

        p1, p2 = _patched_single_candidate()
        with p1, p2:
            from gui.radar.story_dialog import StoryDialog

            dialog = StoryDialog(_fake_article(title="Un film annonce", summary="Le détail."), theme="cinema")
            assert dialog.template_combo.currentData() == TEMPLATE_POST
            assert dialog.title_edit.text() == "Un film annonce"
            assert dialog.label_combo.isVisibleTo(dialog)
            dialog.cleanup()

    def test_le_fil_bandes_annonces_propose_le_post_etiquete(self, app):
        from news_story.story_templates import TEMPLATE_POST_VERTICAL as TEMPLATE_POST

        p1, p2 = _patched_single_candidate()
        with p1, p2:
            from gui.radar.story_dialog import StoryDialog

            dialog = StoryDialog(_fake_article(title="DUNE 3 | Bande-annonce VF"), theme="trailers")
            assert dialog.template_combo.currentData() == TEMPLATE_POST
            assert dialog.label_combo.currentText() == "BANDE-ANNONCE"
            assert dialog._logo_theme == "cinema"  # meme compte, meme logo
            dialog.cleanup()

    def test_le_fil_gaming_garde_la_story_par_defaut(self, app):
        from news_story.story_templates import TEMPLATE_NEWS

        p1, p2 = _patched_single_candidate()
        with p1, p2:
            from gui.radar.story_dialog import StoryDialog

            dialog = StoryDialog(_fake_article())
            assert dialog.template_combo.currentData() == TEMPLATE_NEWS
            assert not dialog.label_combo.isVisibleTo(dialog)
            dialog.cleanup()

    def test_la_legende_est_preremplie_et_copiable(self, app):
        p1, p2 = _patched_single_candidate()
        with p1, p2:
            from PySide6.QtGui import QGuiApplication

            from gui.radar.story_dialog import StoryDialog

            dialog = StoryDialog(_fake_article(title="Un film annonce", summary="Le détail."), theme="cinema")
            caption = dialog.caption_edit.toPlainText()
            assert caption.startswith("🎬 Un film annonce") and "Le détail." in caption
            dialog._copy_caption()
            assert QGuiApplication.clipboard().text() == caption
            dialog.cleanup()

    def test_une_rumeur_preselectionne_l_etiquette_rumeur(self, app):
        p1, p2 = _patched_single_candidate()
        with p1, p2:
            from gui.radar.story_dialog import StoryDialog

            dialog = StoryDialog(_fake_article(title="Rumeur : un reboot en préparation", summary=""),
                                 theme="cinema")
            assert dialog.label_combo.currentText() == "RUMEUR"
            dialog.cleanup()

    def test_l_export_du_post_est_en_9_16(self, app, tmp_path):
        from PIL import Image

        p1, p2 = _patched_single_candidate()
        with p1, p2:
            from gui.radar.story_dialog import StoryDialog

            dialog = StoryDialog(_fake_article(), theme="cinema")
            assert _process_until(app, lambda: dialog.export_btn.isEnabled())
            out_path = str(tmp_path / "post.png")
            with patch("PySide6.QtWidgets.QFileDialog.getSaveFileName", return_value=(out_path, "")), \
                 patch("PySide6.QtWidgets.QMessageBox.information", lambda *a, **k: None):
                dialog._export()
                assert _process_until(app, lambda: os.path.exists(out_path) and os.path.getsize(out_path) > 0)
                # Attendre aussi la confirmation (_on_export_ready) tant que la
                # boite de message est neutralisee : sinon elle s'ouvrirait
                # pour de vrai dans un test suivant et le bloquerait.
                assert _process_until(app, lambda: dialog.export_btn.isEnabled())
            with Image.open(out_path) as im:
                assert im.size == (1080, 1920)
            dialog.cleanup()

    def test_le_4_5_n_est_plus_propose(self, app):
        from news_story.story_templates import TEMPLATE_POST

        p1, p2 = _patched_single_candidate()
        with p1, p2:
            from gui.radar.story_dialog import StoryDialog

            dialog = StoryDialog(_fake_article(), theme="cinema")
            assert dialog.template_combo.findData(TEMPLATE_POST) < 0
            dialog.cleanup()


class TestPostVideo:
    """Article avec une video : le modele video est ajoute et choisi."""

    def test_le_modele_video_apparait_quand_l_article_a_une_video(self, app):
        from news_story.story_templates import TEMPLATE_VIDEO

        p1, p2 = _patched_single_candidate()
        with p1, p2, patch("gui.radar.story_dialog.fetch_video_url",
                           lambda url: "https://www.dailymotion.com/video/xb5ci0m"):
            from gui.radar.story_dialog import StoryDialog

            dialog = StoryDialog(_fake_article(), theme="cinema")
            assert dialog.template_combo.findData(TEMPLATE_VIDEO) < 0
            assert _process_until(app, lambda: dialog.template_combo.currentData() == TEMPLATE_VIDEO)
            assert dialog.opacity_combo.isVisibleTo(dialog)
            assert dialog.opacity_combo.currentData() == 0.6
            dialog.cleanup()

    def test_sans_video_pas_de_modele_video(self, app):
        from news_story.story_templates import TEMPLATE_VIDEO

        p1, p2 = _patched_single_candidate()
        with p1, p2, patch("gui.radar.story_dialog.fetch_video_url", lambda url: ""):
            from gui.radar.story_dialog import StoryDialog

            dialog = StoryDialog(_fake_article(), theme="cinema")
            assert _process_until(app, lambda: dialog.export_btn.isEnabled())
            app.processEvents()
            assert dialog.template_combo.findData(TEMPLATE_VIDEO) < 0
            assert not dialog.opacity_combo.isVisibleTo(dialog)
            dialog.cleanup()

    def test_un_choix_de_modele_de_l_utilisateur_n_est_pas_ecrase(self, app):
        from news_story.story_templates import TEMPLATE_NEWS, TEMPLATE_VIDEO

        p1, p2 = _patched_single_candidate()
        with p1, p2, patch("gui.radar.story_dialog.fetch_video_url", lambda url: ""):
            from gui.radar.story_dialog import StoryDialog

            dialog = StoryDialog(_fake_article(), theme="cinema")
            dialog.template_combo.setCurrentIndex(dialog.template_combo.findData(TEMPLATE_NEWS))
            dialog._on_template_chosen_by_user(0)
            dialog._on_video_found("https://www.youtube.com/watch?v=AAAAAAAAAAA")
            assert dialog.template_combo.findData(TEMPLATE_VIDEO) >= 0
            assert dialog.template_combo.currentData() == TEMPLATE_NEWS
            dialog.cleanup()


class TestLogoParFil:
    def test_le_fil_cinema_utilise_le_logo_cinema_livre_par_defaut(self, app, monkeypatch):
        from PySide6.QtCore import QSettings

        monkeypatch.setattr(QSettings, "value", lambda self, key, default=None: "")
        p1, p2 = _patched_single_candidate()
        with p1, p2:
            from gui.radar.story_dialog import StoryDialog

            dialog = StoryDialog(_fake_article(), theme="cinema")
            assert dialog._logo_path and dialog._logo_path.endswith("logo-cinema.png")
            dialog.cleanup()

    def test_le_fil_gaming_garde_le_logo_de_l_application(self, app, monkeypatch):
        from PySide6.QtCore import QSettings

        monkeypatch.setattr(QSettings, "value", lambda self, key, default=None: "")
        p1, p2 = _patched_single_candidate()
        with p1, p2:
            from gui.radar.story_dialog import StoryDialog

            dialog = StoryDialog(_fake_article())
            assert dialog._logo_path is None
            dialog.cleanup()


class TestPetitEcran:
    """Retour utilisateur : sur un ecran peu haut, « Copier la légende »
    s'affichait par-dessus la legende. Le panneau d'options defile, et le
    bouton est dans la barre du bas."""

    def test_rien_ne_se_chevauche_a_la_hauteur_minimale(self, app):
        p1, p2 = _patched_single_candidate()
        with p1, p2:
            from gui.radar.story_dialog import StoryDialog

            dialog = StoryDialog(_fake_article(title="Un film", summary="Le détail."), theme="cinema")
            dialog.resize(dialog.minimumWidth(), dialog.minimumHeight())
            dialog.show()
            app.processEvents()
            btn, caption = dialog.copy_caption_btn, dialog.caption_edit
            assert btn.isVisible()
            # Le bouton n'est plus dans le panneau qui defile.
            assert not dialog.options_scroll.widget().isAncestorOf(btn)
            # La legende garde sa hauteur minimale au lieu d'etre ecrasee.
            assert caption.height() >= caption.minimumHeight()
            dialog.cleanup()
