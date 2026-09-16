"""Coller un lien YouTube -- ou un clip Twitch -- dans l'accueil.

RIEN N'EST REECRIT ICI. Le telechargement passe par youtube/downloader.py,
deja utilise par la page Recherche : meme selecteur de qualite, meme verrou de
consentement, memes messages d'erreur. Le fil d'analyse acceptait DEJA une
source YouTube (`youtube_source`), et c'est lui qui remplit `input` apres
telechargement. L'accueil ne fait donc que proposer une seconde facon de
designer la meme chose.

Le clip Twitch a suivi, dans le MEME champ et par le meme chemin : il passe
par radar/clip_download.py, deja utilise par le Radar. Une difference, et une
seule : Twitch propose lui-meme le telechargement d'un clip (menu Partager),
donc il n'y a pas de case de consentement a cocher avant de telecharger --
exiger une confirmation interdirait ce que la plateforme autorise. Le rappel
sur les droits reste affiche, comme rappel avant publication.

CE QUE CES TESTS DEFENDENT :
  * qu'une adresse qui n'est ni YouTube ni un clip Twitch soit refusee AVANT
    tout appel reseau ;
  * que le verrou de consentement YouTube soit le meme qu'ailleurs, pas un
    oubli -- et qu'il ne s'etende pas a Twitch, ou il n'a pas lieu d'etre ;
  * qu'une VOD ou un direct Twitch soient refuses, puisque rien ne permet de
    les telecharger ;
  * que les deux sources ne puissent jamais etre actives en meme temps.
"""
from __future__ import annotations

import pytest

from radar.clip_download import looks_like_twitch_clip
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


@pytest.fixture
def page():
    pytest.importorskip("PySide6")
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from gui.controller import AppController
    from gui.pages.home_page import HomePage

    QApplication.instance() or QApplication([])
    return HomePage(AppController())


class TestAccueil:
    LIEN = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    def test_rien_n_est_lancable_au_demarrage(self, page):
        assert not page.generate_btn.isEnabled()

    def test_le_consentement_n_apparait_qu_avec_un_lien(self, page):
        """Un fichier deja sur le disque n'a rien a telecharger : encombrer la
        carte d'une case sans objet serait du bruit."""
        assert page.consent_check.isHidden()
        page.link_edit.setText(self.LIEN)
        assert not page.consent_check.isHidden()

    def test_un_lien_seul_ne_suffit_pas_a_lancer(self, page):
        """LE VERROU : c'est le meme qu'a la page Recherche, pas un oubli."""
        page.link_edit.setText(self.LIEN)
        assert not page.generate_btn.isEnabled()
        page.consent_check.setChecked(True)
        assert page.generate_btn.isEnabled()

    def test_un_lien_non_youtube_ne_debloque_rien(self, page):
        page.link_edit.setText("https://vimeo.com/123456")
        assert not page.generate_btn.isEnabled()
        assert page.consent_check.isHidden()
        assert "n'est reconnu ni" in page.link_hint.text()

    def test_le_lien_efface_le_fichier_choisi(self, page, tmp_path):
        """Les deux sources s'excluent : sinon il faudrait deviner laquelle
        l'utilisateur voulait."""
        fichier = tmp_path / "clip.mp4"
        fichier.write_bytes(b"0")
        page._on_file_selected(str(fichier))
        page.link_edit.setText(self.LIEN)
        assert page.selected_video_path is None

    def test_le_fichier_efface_le_lien(self, page, tmp_path):
        fichier = tmp_path / "clip.mp4"
        fichier.write_bytes(b"0")
        page.link_edit.setText(self.LIEN)
        page._on_file_selected(str(fichier))
        assert page.link_edit.text() == ""
        assert page.link_source == ""

    def test_vider_le_champ_remet_tout_a_zero(self, page):
        page.link_edit.setText(self.LIEN)
        page.consent_check.setChecked(True)
        page.link_edit.clear()
        assert page.link_source == ""
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

        page.link_edit.setText(self.LIEN)
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
        page.link_edit.setText(self.LIEN)
        page._on_generate_clicked()
        assert vu == []

    def test_le_nom_du_projet_ne_contient_aucun_caractere_interdit(self, page,
                                                                  monkeypatch):
        """Windows refuse : \\ / : * ? " < > | -- et une URL en contient."""
        vu = {}
        monkeypatch.setattr(page.controller, "start_analysis",
                            lambda cli_args, **kwargs: vu.update(kwargs))
        monkeypatch.setattr(page, "_confirm_heavy_model", lambda: True)

        page.link_edit.setText(self.LIEN)
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


class TestReconnaissanceDuClipTwitch:
    """Meme role que looks_like_youtube, meme forme : pure, sans reseau."""

    @pytest.mark.parametrize("valeur", [
        "https://clips.twitch.tv/SardocheLeKingAmazingClip",
        "http://clips.twitch.tv/AbcDef",
        "clips.twitch.tv/AbcDef",
        "https://www.twitch.tv/sardoche/clip/SpicyBadgerHeyGuys-abc_123",
        "https://twitch.tv/sardoche/clip/SpicyBadger",
        "https://m.twitch.tv/sardoche/clip/SpicyBadger",
        "https://www.twitch.tv/clip/SpicyBadger",
        "https://clips.twitch.tv/AbcDef?filter=clips&range=7d",
        "  https://clips.twitch.tv/AbcDef  ",
    ])
    def test_les_formes_reelles_sont_acceptees(self, valeur):
        assert looks_like_twitch_clip(valeur)

    @pytest.mark.parametrize("valeur", [
        "", "   ", "pas un lien",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://vimeo.com/123456",
        r"C:\\videos\\clip.mp4",
        "/home/user/clip.mp4",
    ])
    def test_le_reste_est_refuse(self, valeur):
        assert not looks_like_twitch_clip(valeur)

    @pytest.mark.parametrize("valeur", [
        "https://www.twitch.tv/sardoche",
        "https://www.twitch.tv/videos/123456789",
        "https://www.twitch.tv/sardoche/videos",
    ])
    def test_une_chaine_une_vod_un_direct_sont_refuses(self, valeur):
        """Twitch ne propose AUCUN telechargement pour ceux-la : les accepter
        ferait echouer yt-dlp trente secondes plus tard sur un message
        technique, au lieu de le dire tout de suite."""
        assert not looks_like_twitch_clip(valeur)

    def test_un_hote_qui_contient_twitch_sans_etre_twitch_est_refuse(self):
        assert not looks_like_twitch_clip("https://twitch.tv.evil.example.com/a/clip/b")
        assert not looks_like_twitch_clip("https://nottwitch.tv/a/clip/b")

    def test_un_identifiant_nu_est_refuse(self):
        """clip_url() sait completer un slug, mais rien ne distingue
        « SardocheLeKing » d'un titre ou d'un pseudo : sur un champ ou on peut
        aussi coller un lien YouTube, deviner serait pire que demander une
        adresse complete."""
        assert not looks_like_twitch_clip("SardocheLeKing")

    def test_un_protocole_exotique_est_refuse(self):
        assert not looks_like_twitch_clip("file:///etc/passwd")
        assert not looks_like_twitch_clip("javascript:alert(1)")

    def test_aucune_adresse_ne_satisfait_les_deux_plateformes(self):
        """Les deux listes d'hotes sont disjointes : l'ordre des deux tests
        dans l'accueil ne peut donc pas changer le resultat."""
        for valeur in ("https://clips.twitch.tv/AbcDef",
                       "https://www.twitch.tv/a/clip/b",
                       "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                       "https://youtu.be/dQw4w9WgXcQ"):
            assert not (looks_like_youtube(valeur) and looks_like_twitch_clip(valeur))


class TestAccueilTwitch:
    """Le MEME champ, le meme bouton, l'autre plateforme."""

    CLIP = "https://clips.twitch.tv/SardocheLeKingAmazingClip"

    def test_un_clip_suffit_a_lancer_sans_consentement(self, page):
        """LA DIFFERENCE AVEC YOUTUBE, et la seule : Twitch propose lui-meme ce
        telechargement. Exiger une confirmation avant de telecharger
        interdirait ce que la plateforme autorise."""
        page.link_edit.setText(self.CLIP)
        assert page.link_kind == "twitch"
        assert page.consent_check.isHidden()
        assert page.generate_btn.isEnabled()

    def test_le_rappel_sur_les_droits_reste_affiche(self, page):
        """Pouvoir telecharger n'est pas detenir des droits : le rappel ne
        disparait pas avec la case."""
        page.link_edit.setText(self.CLIP)
        assert "licence" in page.link_hint.text().lower()

    def test_une_vod_ne_debloque_rien(self, page):
        page.link_edit.setText("https://www.twitch.tv/videos/123456789")
        assert page.link_kind == ""
        assert not page.generate_btn.isEnabled()

    def test_l_analyse_part_avec_la_source_twitch(self, page, monkeypatch):
        vu = {}
        monkeypatch.setattr(page.controller, "start_analysis",
                            lambda cli_args, **kwargs: vu.update(
                                {"args": cli_args, **kwargs}))
        monkeypatch.setattr(page, "_confirm_heavy_model", lambda: True)

        page.link_edit.setText(self.CLIP)
        page._on_generate_clicked()

        assert vu["twitch_source"] == self.CLIP
        assert vu["source_kind"] == "twitch"
        assert "youtube_source" not in vu
        assert vu["args"].input == "", "le chemin est rempli après téléchargement"

    def test_le_nom_du_projet_ne_contient_aucun_caractere_interdit(self, page,
                                                                   monkeypatch):
        vu = {}
        monkeypatch.setattr(page.controller, "start_analysis",
                            lambda cli_args, **kwargs: vu.update(kwargs))
        monkeypatch.setattr(page, "_confirm_heavy_model", lambda: True)

        page.link_edit.setText(self.CLIP)
        page._on_generate_clicked()

        for interdit in '\\/:*?"<>|':
            assert interdit not in vu["name"], vu["name"]

    def test_passer_d_un_clip_a_un_lien_youtube_remet_le_consentement(self, page):
        """Le champ est unique : changer de plateforme doit changer le verrou,
        pas garder celui de la precedente."""
        page.link_edit.setText(self.CLIP)
        assert page.generate_btn.isEnabled()
        page.link_edit.setText("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        assert page.link_kind == "youtube"
        assert not page.consent_check.isHidden()
        assert not page.generate_btn.isEnabled()


class TestFilDAnalyseTwitch:
    """Le fil d'analyse telecharge le clip AVANT le pipeline, comme il le fait
    deja pour YouTube -- c'est lui qui remplit `input`."""

    CLIP = "https://clips.twitch.tv/SardocheLeKingAmazingClip"

    @pytest.fixture
    def thread(self):
        pytest.importorskip("PySide6")
        from core.config_loader import Settings
        from gui.controller import AnalysisThread
        from utils.errors import CancelledError  # noqa: F401  (import verifie)

        class Jeton:
            def check(self):
                return None

        settings = Settings.__new__(Settings)
        settings.input = ""
        return AnalysisThread(settings, Jeton(), twitch_source=self.CLIP)

    def test_le_clip_est_telecharge_dans_la_meilleure_qualite(self, thread, monkeypatch):
        """AUCUN PLAFOND DE DEFINITION. `download_clip` a max_height=0 par
        defaut et le fil ne le surcharge pas : un clip disponible en Source ne
        doit jamais redescendre en 720p au passage."""
        vu = {}

        def faux_download_clip(url, out_dir, **kwargs):
            vu.update({"url": url, "out_dir": out_dir, **kwargs})
            return "/tmp/clip.mp4"

        import radar.clip_download as cd

        monkeypatch.setattr(cd, "download_clip", faux_download_clip)
        monkeypatch.setattr("pipeline.run", lambda *a, **k: [])

        thread.run()

        assert vu["url"] == self.CLIP
        assert vu.get("max_height", 0) == 0, "aucun plafond ne doit etre impose"
        assert thread.settings.input == "/tmp/clip.mp4"

    def test_le_clip_va_dans_le_dossier_du_radar(self, thread, monkeypatch):
        """Pas un temporaire : le Radar reutilise un clip deja telecharge, et
        rien ne justifie de retelecharger le meme fichier selon la page par
        laquelle on est passe."""
        vu = {}
        import radar.clip_download as cd
        from radar.analysis.media import clips_dir

        monkeypatch.setattr(cd, "download_clip",
                            lambda url, out_dir, **k: vu.update(out_dir=out_dir) or "/tmp/c.mp4")
        monkeypatch.setattr("pipeline.run", lambda *a, **k: [])

        thread.run()
        assert vu["out_dir"] == str(clips_dir())

    def test_le_jeton_d_annulation_est_transmis(self, thread, monkeypatch):
        """Sans lui, le bouton Annuler n'a aucun effet pendant le transfert."""
        vu = {}
        import radar.clip_download as cd

        monkeypatch.setattr(cd, "download_clip",
                            lambda url, out_dir, **k: vu.update(k) or "/tmp/c.mp4")
        monkeypatch.setattr("pipeline.run", lambda *a, **k: [])

        thread.run()
        assert vu.get("cancel_token") is thread.cancel_token
