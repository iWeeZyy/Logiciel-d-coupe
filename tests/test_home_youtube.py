"""Coller un lien YouTube dans l'accueil.

RIEN N'EST REECRIT ICI. Le telechargement passe par youtube/downloader.py,
deja utilise par la page Recherche : meme selecteur de qualite, meme verrou de
consentement, memes messages d'erreur. Le fil d'analyse acceptait DEJA une
source YouTube (`youtube_source`), et c'est lui qui remplit `input` apres
telechargement. L'accueil ne fait donc que proposer une seconde facon de
designer la meme chose.

CE QUE CES TESTS DEFENDENT :
  * qu'une adresse qui n'est pas YouTube soit refusee AVANT tout appel reseau ;
  * que le verrou de consentement soit le meme qu'ailleurs, pas un oubli ;
  * que les deux sources ne puissent jamais etre actives en meme temps.
"""
from __future__ import annotations

import pytest

from youtube.downloader import looks_like_youtube


class TestReconnaissanceDuLien:
    """Fonction PURE : elle sert a griser un bouton, pas a garantir que la
    video existe -- ce qui demanderait un appel reseau a chaque frappe."""

    @pytest.mark.parametrize("valeur", [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "http://youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ",
        "https://m.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://music.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://www.youtube.com/shorts/dQw4w9WgXcQ",
        "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ",
        "www.youtube.com/watch?v=dQw4w9WgXcQ",
        "youtu.be/dQw4w9WgXcQ",
        "dQw4w9WgXcQ",
    ])
    def test_les_formes_reelles_sont_acceptees(self, valeur):
        assert looks_like_youtube(valeur)

    @pytest.mark.parametrize("valeur", [
        "", "   ", "pas un lien",
        "https://vimeo.com/123456",
        "https://dailymotion.com/video/x123",
        r"C:\\videos\\clip.mp4",
        "/home/user/clip.mp4",
    ])
    def test_le_reste_est_refuse(self, valeur):
        assert not looks_like_youtube(valeur)

    def test_un_hote_qui_contient_youtube_sans_etre_youtube_est_refuse(self):
        """« youtube.evil.example.com » contient « youtube » : une simple
        recherche de sous-chaine l'aurait accepte."""
        assert not looks_like_youtube("https://youtube.evil.example.com/watch?v=x")
        assert not looks_like_youtube("https://notyoutube.com/watch?v=x")

    def test_un_protocole_exotique_est_refuse(self):
        assert not looks_like_youtube("file:///etc/passwd")
        assert not looks_like_youtube("javascript:alert(1)")

    def test_les_espaces_autour_ne_genent_pas(self):
        """Un lien colle depuis un navigateur traine souvent un espace."""
        assert looks_like_youtube("  https://youtu.be/dQw4w9WgXcQ  ")


class TestAccueil:
    @pytest.fixture
    def page(self):
        pytest.importorskip("PySide6")
        import os

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        from gui.controller import AppController
        from gui.pages.home_page import HomePage

        QApplication.instance() or QApplication([])
        return HomePage(AppController())

    LIEN = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    def test_rien_n_est_lancable_au_demarrage(self, page):
        assert not page.generate_btn.isEnabled()

    def test_le_consentement_n_apparait_qu_avec_un_lien(self, page):
        """Un fichier deja sur le disque n'a rien a telecharger : encombrer la
        carte d'une case sans objet serait du bruit."""
        assert page.consent_check.isHidden()
        page.youtube_edit.setText(self.LIEN)
        assert not page.consent_check.isHidden()

    def test_un_lien_seul_ne_suffit_pas_a_lancer(self, page):
        """LE VERROU : c'est le meme qu'a la page Recherche, pas un oubli."""
        page.youtube_edit.setText(self.LIEN)
        assert not page.generate_btn.isEnabled()
        page.consent_check.setChecked(True)
        assert page.generate_btn.isEnabled()

    def test_un_lien_non_youtube_ne_debloque_rien(self, page):
        page.youtube_edit.setText("https://vimeo.com/123456")
        assert not page.generate_btn.isEnabled()
        assert page.consent_check.isHidden()
        assert "pas reconnu" in page.youtube_hint.text()

    def test_le_lien_efface_le_fichier_choisi(self, page, tmp_path):
        """Les deux sources s'excluent : sinon il faudrait deviner laquelle
        l'utilisateur voulait."""
        fichier = tmp_path / "clip.mp4"
        fichier.write_bytes(b"0")
        page._on_file_selected(str(fichier))
        page.youtube_edit.setText(self.LIEN)
        assert page.selected_video_path is None

    def test_le_fichier_efface_le_lien(self, page, tmp_path):
        fichier = tmp_path / "clip.mp4"
        fichier.write_bytes(b"0")
        page.youtube_edit.setText(self.LIEN)
        page._on_file_selected(str(fichier))
        assert page.youtube_edit.text() == ""
        assert page.youtube_source == ""

    def test_vider_le_champ_remet_tout_a_zero(self, page):
        page.youtube_edit.setText(self.LIEN)
        page.consent_check.setChecked(True)
        page.youtube_edit.clear()
        assert page.youtube_source == ""
        assert not page.generate_btn.isEnabled()
        assert page.consent_check.isHidden()

    def test_l_analyse_part_avec_la_source_youtube(self, page, monkeypatch):
        """Le fil d'analyse acceptait deja `youtube_source` : c'est lui qui
        telecharge puis remplit `input`. L'accueil ne fait que le renseigner."""
        vu = {}
        monkeypatch.setattr(page.controller, "start_analysis",
                            lambda cli_args, **kwargs: vu.update(
                                {"args": cli_args, **kwargs}))
        monkeypatch.setattr(page, "_confirm_heavy_model", lambda: True)

        page.youtube_edit.setText(self.LIEN)
        page.consent_check.setChecked(True)
        page._on_generate_clicked()

        assert vu["youtube_source"] == self.LIEN
        assert vu["source_kind"] == "youtube"
        assert vu["args"].input == "", "le chemin est rempli après téléchargement"

    def test_rien_ne_part_sans_consentement(self, page, monkeypatch):
        """Meme en forcant le clic : le verrou n'est pas qu'un grisage."""
        vu = []
        monkeypatch.setattr(page.controller, "start_analysis",
                            lambda *a, **k: vu.append(a))
        page.youtube_edit.setText(self.LIEN)
        page._on_generate_clicked()
        assert vu == []

    def test_le_nom_du_projet_ne_contient_aucun_caractere_interdit(self, page,
                                                                  monkeypatch):
        """Windows refuse : \\ / : * ? " < > | -- et une URL en contient."""
        vu = {}
        monkeypatch.setattr(page.controller, "start_analysis",
                            lambda cli_args, **kwargs: vu.update(kwargs))
        monkeypatch.setattr(page, "_confirm_heavy_model", lambda: True)

        page.youtube_edit.setText(self.LIEN)
        page.consent_check.setChecked(True)
        page._on_generate_clicked()

        for interdit in '\\/:*?"<>|':
            assert interdit not in vu["name"], vu["name"]

    def test_le_fichier_local_continue_de_marcher_comme_avant(self, page,
                                                              tmp_path, monkeypatch):
        """LA REGRESSION A EVITER : l'accueil ne devait rien perdre."""
        vu = {}
        monkeypatch.setattr(page.controller, "start_analysis",
                            lambda cli_args, **kwargs: vu.update(
                                {"args": cli_args, **kwargs}))
        monkeypatch.setattr(page, "_confirm_heavy_model", lambda: True)

        fichier = tmp_path / "clip.mp4"
        fichier.write_bytes(b"0")
        page._on_file_selected(str(fichier))
        page._on_generate_clicked()

        assert vu["source_kind"] == "local"
        assert vu["args"].input == str(fichier)
        assert "youtube_source" not in vu
