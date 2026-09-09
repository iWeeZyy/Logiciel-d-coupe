"""Options de production : sous-titres, format, cadrage intelligent, montage.

Ces quatre choix se font AVANT un traitement. Ils ne remplacent pas
config/editing.json : ils ne peuvent que desactiver un module deja actif.
"""
from __future__ import annotations

import pytest

from core.config_loader import Settings
from editing.timeline import EditList
from video.cropper import (
    ASPECT_LANDSCAPE,
    ASPECT_PORTRAIT,
    LANDSCAPE_SIZE,
    PORTRAIT_SIZE,
    target_size,
)
from video.filter_graph import build_video_chain

TOUS_ACTIFS = {"framing": {"enabled": True}, "montage": {"enabled": True},
               "captions": {"enabled": True}}


def chain(**kwargs) -> str:
    params = dict(edit_list=EditList.identity(0.0, 10.0), framing_plan=None,
                  zoom_track=None, src_w=1920, src_h=1080, fps=25.0)
    params.update(kwargs)
    return build_video_chain(**params)


class TestFormat:
    def test_le_portrait_reste_le_defaut(self):
        assert target_size() == PORTRAIT_SIZE == (1080, 1920)
        assert "crop=" in chain(), "le 9:16 recadre, c'est sa raison d'etre"
        assert "scale=1080:1920" in chain()

    def test_le_paysage_ne_recadre_pas(self):
        """Recadrer une source horizontale vers un cadre horizontal ne ferait
        que rogner l'image pour rien."""
        produced = chain(target_size=target_size(ASPECT_LANDSCAPE))
        assert "crop=" not in produced
        assert "scale=1920:1080" in produced

    def test_le_paysage_complete_par_des_bandes_au_lieu_de_deformer(self):
        produced = chain(src_w=1080, src_h=1080, target_size=LANDSCAPE_SIZE)
        assert "force_original_aspect_ratio=decrease" in produced
        assert "pad=1920:1080" in produced

    def test_un_format_inconnu_retombe_sur_le_portrait(self):
        assert target_size("carre") == PORTRAIT_SIZE
        assert target_size("") == PORTRAIT_SIZE

    def test_les_sous_titres_sont_incrustes_dans_les_deux_formats(self):
        for size in (PORTRAIT_SIZE, LANDSCAPE_SIZE):
            produced = chain(ass_path="/tmp/x.ass", target_size=size)
            assert "subtitles=" in produced

    def test_le_zoom_travaille_a_la_taille_du_format_demande(self):
        from editing.zoom import ZoomKeyframe, ZoomTrack

        track = ZoomTrack(keyframes=[ZoomKeyframe(t=0.0, zoom=1.0),
                                     ZoomKeyframe(t=2.0, zoom=1.2)])
        produced = chain(zoom_track=track)
        assert "s=1080x1920" in produced


class TestInterrupteurs:
    def _settings(self, **kwargs) -> Settings:
        return Settings(editing=TOUS_ACTIFS, **kwargs)

    def test_tout_est_actif_par_defaut(self):
        settings = self._settings()
        assert settings.subtitles_enabled and settings.smart_framing and settings.auto_montage
        assert settings.aspect_ratio == ASPECT_PORTRAIT
        for module in ("framing", "montage", "captions"):
            assert settings.editing_module_enabled(module)

    def test_decocher_desactive_le_module_correspondant(self):
        assert not self._settings(smart_framing=False).editing_module_enabled("framing")
        assert not self._settings(auto_montage=False).editing_module_enabled("montage")
        assert not self._settings(subtitles_enabled=False).editing_module_enabled("captions")

    def test_decocher_un_module_n_en_desactive_pas_un_autre(self):
        settings = self._settings(smart_framing=False)
        assert settings.editing_module_enabled("montage")
        assert settings.editing_module_enabled("captions")

    def test_une_case_cochee_ne_rallume_pas_un_module_coupe_en_configuration(self):
        """La configuration reste la source ; la case est un interrupteur
        par-dessus, pas un contournement."""
        settings = Settings(editing={"framing": {"enabled": False}}, smart_framing=True)
        assert not settings.editing_module_enabled("framing")

    def test_un_module_sans_interrupteur_suit_la_seule_configuration(self):
        settings = self._settings(smart_framing=False, auto_montage=False)
        settings.editing["metadata"] = {"enabled": True}
        assert settings.editing_module_enabled("metadata")

    def test_le_format_choisi_donne_la_definition_de_sortie(self):
        assert self._settings().target_size() == PORTRAIT_SIZE
        assert self._settings(aspect_ratio=ASPECT_LANDSCAPE).target_size() == LANDSCAPE_SIZE


class TestSousTitresDesactives:
    def test_aucun_fichier_de_sous_titres_n_est_produit(self, monkeypatch, tmp_path):
        """Produire un .ass pour ne pas s'en servir laisserait croire, en lisant
        le dossier de travail, que les sous-titres ont ete incrustes."""
        from video import clip_builder

        rendus = []
        monkeypatch.setattr(clip_builder, "render_ass_file",
                            lambda *a, **k: rendus.append(k.get("out_ass_path")))
        args = {}
        monkeypatch.setattr(clip_builder, "build_ffmpeg_args",
                            lambda **kwargs: args.update(kwargs) or ["ffmpeg"])
        monkeypatch.setattr(clip_builder, "run_ffmpeg", lambda *a, **k: None)

        clip_builder.build_clip(
            video_path="in.mp4", start=0.0, end=5.0, src_w=1920, src_h=1080,
            face_hint=None, words=[], subtitle_style={},
            out_mp4_path=str(tmp_path / "out.mp4"), ass_path=None,
            export_settings={}, clip_label="clip_01",
        )

        assert rendus == [], "aucun fichier de sous-titres ne doit être écrit"
        assert args["ass_path"] is None

    def test_les_sous_titres_actifs_produisent_bien_un_fichier(self, monkeypatch, tmp_path):
        from video import clip_builder

        rendus = []
        monkeypatch.setattr(clip_builder, "render_ass_file",
                            lambda *a, **k: rendus.append(k.get("out_ass_path")))
        monkeypatch.setattr(clip_builder, "build_ffmpeg_args", lambda **kwargs: ["ffmpeg"])
        monkeypatch.setattr(clip_builder, "run_ffmpeg", lambda *a, **k: None)

        clip_builder.build_clip(
            video_path="in.mp4", start=0.0, end=5.0, src_w=1920, src_h=1080,
            face_hint=None, words=[], subtitle_style={},
            out_mp4_path=str(tmp_path / "out.mp4"), ass_path=str(tmp_path / "c.ass"),
            export_settings={}, clip_label="clip_01",
        )

        assert rendus == [str(tmp_path / "c.ass")]


class TestLigneDeCommande:
    def test_sans_option_le_comportement_est_inchange(self):
        """Les options sont formulees en negatif : une commande deja ecrite
        quelque part continue de produire exactement la meme chose."""
        import argparse

        args = argparse.Namespace(input="v.mp4")
        from core.config_loader import load_settings

        settings = load_settings(args)
        assert settings.subtitles_enabled
        assert settings.smart_framing
        assert settings.auto_montage
        assert settings.aspect_ratio == ASPECT_PORTRAIT

    def test_les_options_negatives_sont_lues(self):
        import argparse

        from core.config_loader import load_settings

        args = argparse.Namespace(input="v.mp4", no_subtitles=True, no_smart_framing=True,
                                  no_auto_montage=True, aspect="16:9")
        settings = load_settings(args)
        assert not settings.subtitles_enabled
        assert not settings.smart_framing
        assert not settings.auto_montage
        assert settings.aspect_ratio == ASPECT_LANDSCAPE


class TestCaseSousTitresDeLAccueil:
    """Defaut reel : decocher "Sous-titres" ne retirait pas les sous-titres.

    La case coupait bien le module captions, mais le fichier .ass etait
    construit et incruste quoi qu'il arrive -- seul l'export du .srt etait
    supprime. Le meme test decide desormais de l'incrustation ET de l'export.
    """

    def test_couper_le_module_captions_coupe_l_incrustation(self):
        settings = Settings(editing={"captions": {"enabled": False}})
        assert not settings.editing_module_enabled("captions")

    def test_la_case_de_l_accueil_passe_par_le_meme_interrupteur(self):
        """L'accueil surcharge editing[captions][enabled] ; la ligne de commande
        passe par subtitles_enabled. Les deux doivent aboutir au meme endroit."""
        depuis_accueil = Settings(editing={"captions": {"enabled": False}})
        depuis_cli = Settings(editing={"captions": {"enabled": True}},
                              subtitles_enabled=False)
        assert not depuis_accueil.editing_module_enabled("captions")
        assert not depuis_cli.editing_module_enabled("captions")

    def test_le_pipeline_decide_l_incrustation_sur_ce_test(self):
        """Verrou de non-regression : si quelqu'un remet une condition
        differente pour le .ass, ce test le voit."""
        from pathlib import Path

        source = Path("pipeline.py").read_text(encoding="utf-8")
        assert 'if settings.editing_module_enabled("captions") else None' in source
