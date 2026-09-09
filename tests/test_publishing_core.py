"""Preparation d'une publication : legendes, controles, export, historique.

Tests purs -- aucun reseau, aucune API. Ils portent sur ce que le module
garantit avant qu'une plateforme n'entre en jeu.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from publishing import captions, compatibility, export
from publishing.history import PublicationHistory
from publishing.models import (
    PLATFORM_INSTAGRAM,
    PLATFORM_TIKTOK,
    STATUS_FAILED,
    STATUS_PUBLISHED,
    PublicationDraft,
    PublicationRecord,
)
from radar.analysis.models import ClipAnalysis


def analysis(**kwargs) -> ClipAnalysis:
    base = dict(
        content_id="twitch:c1", platform="twitch", clip_title="Le moment",
        creator_label="ZeratoR", duration_s=30.0,
        summary="Le clip s'ouvre sur « Il me reste une carte ».",
        description="Il me reste une carte. Le moment arrive à 00:06.",
        short_description="Attends quoi ?",
        title_punchy="ATTENDS QUOI", title_direct="Il me reste une carte",
        hashtags=["#carte"],
    )
    base.update(kwargs)
    return ClipAnalysis(**base)


def make_video(path: Path, *, width=1080, height=1920, duration=6, audio=True,
               container="mp4") -> Path:
    args = ["ffmpeg", "-y", "-f", "lavfi", "-i",
            f"testsrc=size={width}x{height}:rate=25:duration={duration}"]
    if audio:
        args += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}", "-shortest"]
    args += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
    if audio:
        args += ["-c:a", "aac"]
    target = path.with_suffix("." + container)
    args.append(str(target))
    subprocess.run(args, check=True, capture_output=True)
    return target


class TestLegendes:
    def test_les_deux_plateformes_ont_des_legendes_differentes(self):
        """Forcer le meme texte des deux cotes reviendrait a choisir le plus
        mauvais compromis : un Reel supporte quelques lignes, TikTok recompense
        une accroche courte."""
        a = analysis()
        assert captions.instagram_caption(a) != captions.tiktok_caption(a)
        assert "\n" in captions.instagram_caption(a)
        assert "\n" not in captions.tiktok_caption(a)

    def test_rien_n_est_reanalyse(self):
        """Tout vient de l'analyse deja faite."""
        a = analysis()
        source = " ".join([a.summary, a.description, a.short_description, a.title_punchy])
        for phrase in captions.instagram_caption(a).split("\n\n"):
            assert phrase in source

    def test_une_analyse_vide_ne_produit_pas_de_texte_invente(self):
        vide = ClipAnalysis(content_id="twitch:x", platform="twitch")
        assert captions.instagram_caption(vide) == ""
        assert captions.tiktok_caption(vide) == ""

    def test_les_hashtags_de_provenance_completent_ceux_du_contenu(self):
        tags = captions.hashtags_for(analysis())
        assert "#carte" in tags          # du contenu
        assert "#Twitch" in tags         # de la provenance, verifiable
        assert len(tags) <= captions.MAX_HASHTAGS

    def test_pas_de_hashtag_twitch_hors_twitch(self):
        tags = captions.hashtags_for(analysis(platform="youtube"), is_twitch=False)
        assert "#Twitch" not in tags

    def test_une_mention_est_validee_jamais_inventee(self):
        """Le pseudo Twitch d'un streamer n'est pas son compte Instagram.
        Publier "@pseudo" au hasard mentionne quelqu'un qui n'a rien demande."""
        assert captions.normalize_mention("zerator") == "@zerator"
        assert captions.normalize_mention("@zerator") == "@zerator"
        assert captions.normalize_mention("") == ""
        assert captions.normalize_mention("pas un compte !") == ""
        assert captions.normalize_mention("a" * 40) == ""

    def test_le_brouillon_ne_mentionne_personne_par_defaut(self):
        draft = captions.build_draft(analysis(), "/tmp/clip.mp4")
        assert draft.mention == ""

    def test_le_texte_publie_assemble_legende_mention_et_hashtags(self):
        draft = captions.build_draft(analysis(), "/tmp/clip.mp4", mention="zerator")
        texte = draft.full_text_for(PLATFORM_TIKTOK)
        assert texte.startswith("Attends quoi ?")
        assert "@zerator" in texte
        assert "#Twitch" in texte


class TestCompatibilite:
    def test_un_clip_vertical_avec_son_est_pret(self, tmp_path):
        video = make_video(tmp_path / "ok")
        result = compatibility.check(video, PLATFORM_INSTAGRAM)
        assert result.ready, result.warnings
        assert any("verticale" in p for p in result.passed)

    def test_un_clip_horizontal_est_signale_sans_etre_refuse(self, tmp_path):
        video = make_video(tmp_path / "paysage", width=1920, height=1080)
        result = compatibility.check(video, PLATFORM_TIKTOK)
        assert not result.ready
        assert any("horizontale" in w for w in result.warnings)
        assert compatibility.needs_reencoding(result)

    def test_un_clip_sans_audio_est_signale(self, tmp_path):
        video = make_video(tmp_path / "muet", audio=False)
        result = compatibility.check(video, PLATFORM_TIKTOK)
        assert any("audio" in w.lower() for w in result.warnings)

    def test_un_clip_trop_court_est_signale(self, tmp_path):
        video = make_video(tmp_path / "court", duration=1)
        result = compatibility.check(video, PLATFORM_INSTAGRAM)
        assert any("minimum" in w for w in result.warnings)

    def test_un_fichier_absent_ne_fait_pas_planter(self, tmp_path):
        result = compatibility.check(tmp_path / "rien.mp4", PLATFORM_TIKTOK)
        assert not result.ready
        assert "introuvable" in result.warnings[0]

    def test_un_probleme_non_reparable_ne_propose_pas_de_reencodage(self, tmp_path):
        """Une video trop longue ne se corrige pas en changeant de codec :
        proposer un bouton qui ne resout rien fait perdre du temps."""
        video = make_video(tmp_path / "court", duration=1)
        result = compatibility.check(video, PLATFORM_INSTAGRAM)
        assert not compatibility.needs_reencoding(result)


class TestExport:
    def _draft(self, video) -> PublicationDraft:
        return captions.build_draft(analysis(), str(video))

    def test_l_export_contient_tout_ce_qu_il_faut(self, tmp_path):
        video = make_video(tmp_path / "clip")
        result = export.export(self._draft(video), PLATFORM_INSTAGRAM, tmp_path / "projets")

        directory = Path(result.directory)
        assert directory.is_dir()
        assert Path(result.video).is_file()
        assert "Attends quoi ?" in Path(result.caption_file).read_text(encoding="utf-8")
        assert "#Twitch" in Path(result.hashtags_file).read_text(encoding="utf-8")

    def test_chaque_plateforme_a_son_dossier(self, tmp_path):
        video = make_video(tmp_path / "clip")
        draft = self._draft(video)
        a = export.export(draft, PLATFORM_INSTAGRAM, tmp_path / "p")
        b = export.export(draft, PLATFORM_TIKTOK, tmp_path / "p")
        assert a.directory != b.directory
        assert "Instagram" in a.directory and "TikTok" in b.directory

    def test_la_video_n_est_pas_recopiee_deux_fois(self, tmp_path):
        video = make_video(tmp_path / "clip")
        draft = self._draft(video)
        first = export.export(draft, PLATFORM_TIKTOK, tmp_path / "p")
        second = export.export(draft, PLATFORM_TIKTOK, tmp_path / "p")
        assert not first.reused
        assert second.reused, "un clip pèse des dizaines de Mo, on ne le recopie pas pour rien"

    def test_la_couverture_est_reprise_quand_elle_existe(self, tmp_path):
        video = make_video(tmp_path / "clip")
        cover = tmp_path / "vignette.jpg"
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=100x100:duration=1",
                        "-frames:v", "1", str(cover)], check=True, capture_output=True)
        draft = self._draft(video)
        draft.cover_path = str(cover)
        result = export.export(draft, PLATFORM_TIKTOK, tmp_path / "p")
        assert Path(result.cover).is_file()

    def test_sans_couverture_aucun_fichier_n_est_invente(self, tmp_path):
        video = make_video(tmp_path / "clip")
        result = export.export(self._draft(video), PLATFORM_TIKTOK, tmp_path / "p")
        assert result.cover == ""


class TestHistorique:
    @pytest.fixture
    def history(self, tmp_path) -> PublicationHistory:
        return PublicationHistory(tmp_path / "radar.sqlite3")

    def test_un_echec_n_efface_pas_une_reussite(self, history):
        """Les plateformes reussissent et echouent independamment."""
        ok = history.record(PublicationRecord(content_id="twitch:c1",
                                              platform=PLATFORM_INSTAGRAM))
        history.finish(ok, STATUS_PUBLISHED, url="https://instagram.com/p/abc")
        ko = history.record(PublicationRecord(content_id="twitch:c1",
                                              platform=PLATFORM_TIKTOK))
        history.finish(ko, STATUS_FAILED, error="refusé")

        assert history.published_platforms("twitch:c1") == {PLATFORM_INSTAGRAM}
        assert len(history.for_content("twitch:c1")) == 2

    def test_seules_les_reussites_font_un_badge(self, history):
        """Afficher "Instagram ✓" apres un echec ferait republier deux fois."""
        ko = history.record(PublicationRecord(content_id="twitch:c2",
                                              platform=PLATFORM_INSTAGRAM))
        history.finish(ko, STATUS_FAILED, error="token expiré")
        assert history.published_platforms("twitch:c2") == set()
        assert history.published_map() == {}

    def test_une_nouvelle_tentative_ne_remplace_pas_l_ancienne(self, history):
        first = history.record(PublicationRecord(content_id="twitch:c3",
                                                 platform=PLATFORM_TIKTOK))
        history.finish(first, STATUS_FAILED, error="réseau")
        second = history.record(PublicationRecord(content_id="twitch:c3",
                                                  platform=PLATFORM_TIKTOK))
        history.finish(second, STATUS_PUBLISHED, url="https://tiktok.com/@x/video/1")

        records = history.for_content("twitch:c3")
        assert len(records) == 2
        assert history.published_platforms("twitch:c3") == {PLATFORM_TIKTOK}

    def test_l_url_et_les_hashtags_sont_relus(self, history):
        record = history.record(PublicationRecord(
            content_id="twitch:c4", platform=PLATFORM_INSTAGRAM,
            caption="Attends quoi ?", hashtags=["#Twitch", "#Clips"]))
        history.finish(record, STATUS_PUBLISHED, url="https://instagram.com/p/xyz")
        relu = history.for_content("twitch:c4")[0]
        assert relu.url.endswith("/xyz")
        assert relu.hashtags == ["#Twitch", "#Clips"]
        assert relu.status_label == "✓ Publié"

    def test_la_carte_du_radar_lit_tout_en_une_requete(self, history):
        for content, platform in (("twitch:a", PLATFORM_INSTAGRAM),
                                  ("twitch:a", PLATFORM_TIKTOK),
                                  ("twitch:b", PLATFORM_TIKTOK)):
            record = history.record(PublicationRecord(content_id=content, platform=platform))
            history.finish(record, STATUS_PUBLISHED, url="https://exemple/1")
        carte = history.published_map()
        assert carte["twitch:a"] == {PLATFORM_INSTAGRAM, PLATFORM_TIKTOK}
        assert carte["twitch:b"] == {PLATFORM_TIKTOK}
