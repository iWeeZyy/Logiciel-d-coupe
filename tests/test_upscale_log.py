"""La ligne de journal qui dit de combien l'image est agrandie.

POURQUOI ELLE EXISTE. La definition de la source decide de tout le reste : la
fenetre 9:16 d'un clip 1280x720 ne fait que 404 px de large et doit etre
agrandie 2,67 fois, celle d'un 1920x1080 en fait 608 et n'est agrandie que
1,78 fois. La compensation de l'agrandissement suit cet ecart, et sur une
source encore plus grande elle ne s'applique plus du tout.

Sans cette ligne, il faut SUPPOSER une definition -- et une supposition fausse
est exactement ce qui a motive ce message : un clip Twitch pris en « Source »
n'est pas en 720p.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import pipeline


@pytest.fixture
def journal(monkeypatch):
    lignes: list[str] = []
    monkeypatch.setattr(pipeline.logger, "info", lambda message: lignes.append(str(message)))
    return lignes


def _dire(src_w, src_h, aspect="9:16", fit="recadrer"):
    return SimpleNamespace(aspect_ratio=aspect, fit_mode=fit), src_w, src_h


class TestCeQuiEstDit:
    def test_une_source_1080p_est_agrandie_1_78_fois(self, journal):
        """Le cas reel : un clip Twitch pris en « Source »."""
        settings, w, h = _dire(1920, 1080)
        pipeline._log_upscale(w, h, settings)
        assert "fenetre 608x1080" in journal[0]
        assert "x1.78" in journal[0]

    def test_une_source_720p_est_agrandie_2_67_fois(self, journal):
        settings, w, h = _dire(1280, 720)
        pipeline._log_upscale(w, h, settings)
        assert "fenetre 404x720" in journal[0]
        assert "x2.67" in journal[0]

    def test_la_compensation_appliquee_est_nommee(self, journal):
        """La valeur doit etre celle que la chaine de filtres utilise
        vraiment, pas un ordre de grandeur."""
        from video.filter_graph import base_crop_size, sharpen_amount

        settings, w, h = _dire(1280, 720)
        pipeline._log_upscale(w, h, settings)
        attendue = sharpen_amount(1080 / base_crop_size(w, h)[0])
        assert f"compensation {attendue:g}" in journal[0]

    def test_sans_compensation_c_est_dit_aussi(self, journal):
        """Une source deja verticale n'est pas agrandie : le silence serait
        ambigu, on ecrit que rien n'est applique."""
        settings, w, h = _dire(1080, 1920)
        pipeline._log_upscale(w, h, settings)
        assert "aucune compensation" in journal[0]
        assert "x1.00" in journal[0]

    def test_l_image_entiere_est_signalee_comme_telle(self, journal):
        """En « image entiere » ce n'est plus une fenetre qui est agrandie mais
        toute l'image, et le facteur n'a pas le meme sens."""
        settings, w, h = _dire(1920, 1080, fit="entier")
        pipeline._log_upscale(w, h, settings)
        assert "image entiere" in journal[0]

    def test_le_16_9_ne_parle_pas_d_une_fenetre_9_16(self, journal):
        settings, w, h = _dire(1920, 1080, aspect="16:9")
        pipeline._log_upscale(w, h, settings)
        assert "1920x1080" in journal[0]
        assert "608" not in journal[0]

    def test_la_cadence_est_journalisee_avec_la_definition(self):
        """Une source « Source » est souvent en 60 images/s, et la cadence est
        conservee : autant qu'elle soit verifiable."""
        import inspect

        source = inspect.getsource(pipeline.run)
        assert "images/s" in source


class TestCetteLigneNeCassePasUnTraitement:
    def test_une_definition_absurde_ne_leve_pas(self, journal):
        pipeline._log_upscale(0, 0, SimpleNamespace())
        pipeline._log_upscale(-5, 10, SimpleNamespace())
        # aucune exception : une ligne de journal n'a pas le droit de faire
        # echouer une production de plusieurs minutes

    def test_des_reglages_incomplets_ne_levent_pas(self, journal):
        pipeline._log_upscale(1920, 1080, SimpleNamespace())
        assert journal, "des reglages par defaut doivent quand meme etre decrits"
