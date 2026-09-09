"""Adaptateurs de plateformes et enchainement d'une publication.

Aucun appel reseau : les adaptateurs sont remplaces par des faux. Ces tests
portent sur les GARANTIES -- independance des plateformes, export toujours
possible, messages comprehensibles, jeton jamais ecrit en clair.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from publishing import publisher, tokens
from publishing.history import PublicationHistory
from publishing.models import (
    PLATFORM_INSTAGRAM,
    PLATFORM_TIKTOK,
    STATUS_EXPORTED,
    STATUS_FAILED,
    STATUS_PUBLISHED,
    PublicationDraft,
)
from publishing.platforms.base import PlatformAdapter, PlatformStatus, PublishResult
from publishing.platforms.instagram import InstagramAdapter
from publishing.platforms.tiktok import TikTokAdapter


class FakeAdapter(PlatformAdapter):
    def __init__(self, platform, *, connected=True, result=None, reason="", boom=False):
        self.platform = platform
        self.connected = connected
        self.result = result or PublishResult(ok=True, url=f"https://{platform}/p/1")
        self.reason = reason or "Aucun compte connecté."
        self.boom = boom
        self.calls = 0

    def status(self):
        return PlatformStatus(platform=self.platform, configured=self.connected,
                              connected=self.connected, account="@moi" if self.connected else "",
                              reason="" if self.connected else self.reason)

    def publish(self, draft, *, on_progress=None, cancel_token=None):
        self.calls += 1
        if self.boom:
            raise RuntimeError("plantage interne")
        if on_progress:
            on_progress(1.0)
        return self.result

    def disconnect(self):
        pass


@pytest.fixture
def draft(tmp_path) -> PublicationDraft:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x" * 4096)
    return PublicationDraft(clip_path=str(video), content_id="twitch:c1",
                            clip_title="Un clip", creator_label="ZeratoR",
                            instagram_caption="Légende Reel", tiktok_caption="Légende courte",
                            hashtags=["#Twitch", "#Clips"], duration_s=28.0)


@pytest.fixture
def history(tmp_path) -> PublicationHistory:
    return PublicationHistory(tmp_path / "radar.sqlite3")


class TestIndependanceDesPlateformes:
    def test_un_echec_n_empeche_pas_l_autre_de_reussir(self, draft, history, tmp_path):
        adapters = {
            PLATFORM_INSTAGRAM: FakeAdapter(PLATFORM_INSTAGRAM),
            PLATFORM_TIKTOK: FakeAdapter(PLATFORM_TIKTOK,
                                         result=PublishResult(ok=False, error="refusé")),
        }
        records = publisher.publish(draft, [PLATFORM_INSTAGRAM, PLATFORM_TIKTOK],
                                    history=history, export_root=tmp_path / "p",
                                    adapters=adapters)
        par_plateforme = {r.platform: r.status for r in records}
        assert par_plateforme[PLATFORM_INSTAGRAM] == STATUS_PUBLISHED
        assert par_plateforme[PLATFORM_TIKTOK] == STATUS_FAILED
        assert history.published_platforms("twitch:c1") == {PLATFORM_INSTAGRAM}

    def test_un_plantage_d_adaptateur_ne_tue_pas_la_suite(self, draft, history, tmp_path):
        adapters = {
            PLATFORM_INSTAGRAM: FakeAdapter(PLATFORM_INSTAGRAM, boom=True),
            PLATFORM_TIKTOK: FakeAdapter(PLATFORM_TIKTOK),
        }
        records = publisher.publish(draft, [PLATFORM_INSTAGRAM, PLATFORM_TIKTOK],
                                    history=history, export_root=tmp_path / "p",
                                    adapters=adapters)
        assert len(records) == 2
        assert records[1].status == STATUS_PUBLISHED
        assert "Traceback" not in records[0].error

    def test_l_url_rendue_par_la_plateforme_est_conservee(self, draft, history, tmp_path):
        adapters = {PLATFORM_TIKTOK: FakeAdapter(
            PLATFORM_TIKTOK, result=PublishResult(ok=True, url="https://tiktok/v/9"))}
        records = publisher.publish(draft, [PLATFORM_TIKTOK], history=history,
                                    export_root=tmp_path / "p", adapters=adapters)
        assert records[0].url == "https://tiktok/v/9"

    def test_aucune_url_n_est_inventee(self, draft, history, tmp_path):
        adapters = {PLATFORM_TIKTOK: FakeAdapter(PLATFORM_TIKTOK,
                                                 result=PublishResult(ok=True, url=""))}
        records = publisher.publish(draft, [PLATFORM_TIKTOK], history=history,
                                    export_root=tmp_path / "p", adapters=adapters)
        assert records[0].url == ""


class TestExportToujoursPossible:
    def test_sans_compte_connecte_le_clip_est_exporte_et_non_perdu(self, draft, history,
                                                                   tmp_path):
        """L'export est un resultat, pas un lot de consolation."""
        adapters = {PLATFORM_INSTAGRAM: FakeAdapter(PLATFORM_INSTAGRAM, connected=False)}
        records = publisher.publish(draft, [PLATFORM_INSTAGRAM], history=history,
                                    export_root=tmp_path / "p", adapters=adapters)

        assert records[0].status == STATUS_EXPORTED
        directory = Path(records[0].export_dir)
        assert (directory / "legende.txt").is_file()
        assert (directory / "hashtags.txt").is_file()

    def test_un_export_n_est_pas_compte_comme_une_publication(self, draft, history, tmp_path):
        adapters = {PLATFORM_INSTAGRAM: FakeAdapter(PLATFORM_INSTAGRAM, connected=False)}
        publisher.publish(draft, [PLATFORM_INSTAGRAM], history=history,
                          export_root=tmp_path / "p", adapters=adapters)
        assert history.published_platforms("twitch:c1") == set()

    def test_la_raison_est_conservee_pour_etre_expliquee(self, draft, history, tmp_path):
        adapters = {PLATFORM_TIKTOK: FakeAdapter(PLATFORM_TIKTOK, connected=False,
                                                 reason="Aucune application TikTok configurée.")}
        records = publisher.publish(draft, [PLATFORM_TIKTOK], history=history,
                                    export_root=tmp_path / "p", adapters=adapters)
        assert "application TikTok" in records[0].error

    def test_une_plateforme_inconnue_est_exportee_plutot_qu_ignoree(self, draft, history,
                                                                    tmp_path):
        records = publisher.publish(draft, [PLATFORM_TIKTOK], history=history,
                                    export_root=tmp_path / "p", adapters={})
        assert records[0].status == STATUS_EXPORTED


class TestEtatDesPlateformes:
    def test_instagram_explique_ce_qui_manque(self):
        state = InstagramAdapter().status()
        assert not state.can_publish
        assert "application Meta" in state.reason
        assert "professionnel" in state.setup_hint

    def test_tiktok_previent_de_l_audit(self):
        state = TikTokAdapter().status()
        assert not state.can_publish
        assert "audit" in state.setup_hint.lower()

    def test_instagram_refuse_sans_adresse_publique_et_le_dit(self, draft, monkeypatch):
        """Instagram ne recoit pas de fichier : ses serveurs vont chercher la
        video a une adresse publique, que ClipFarming n'a pas."""
        adapter = InstagramAdapter()
        monkeypatch.setattr(adapter, "status", lambda: PlatformStatus(
            platform=PLATFORM_INSTAGRAM, configured=True, connected=True))
        result = adapter.publish(draft)
        assert not result.ok
        assert "adresse publique" in result.error

    def test_tiktok_signale_un_fichier_absent(self, monkeypatch):
        adapter = TikTokAdapter()
        monkeypatch.setattr(adapter, "status", lambda: PlatformStatus(
            platform=PLATFORM_TIKTOK, configured=True, connected=True))
        result = adapter.publish(PublicationDraft(clip_path="/nulle/part.mp4"))
        assert not result.ok
        assert "introuvable" in result.error

    def test_un_jeton_expire_est_dit_en_clair(self):
        message = TikTokAdapter._explain({"error": {"code": "access_token_invalid"}})
        assert "expiré" in message
        assert "Reconnectez" in message

    def test_aucun_message_ne_montre_de_jeton(self):
        message = TikTokAdapter._explain({"error": {"code": "x", "message": "bad request"}})
        assert "Bearer" not in message and "token" not in message.lower()


class TestJetons:
    def test_sans_coffre_rien_n_est_ecrit(self, monkeypatch):
        """Un refus explique se corrige ; un fichier en clair ne se remarque
        que le jour ou il fuit."""
        monkeypatch.setattr(tokens, "_keyring", lambda: None)
        assert tokens.save_token(PLATFORM_TIKTOK, "secret") is False
        assert tokens.load_token(PLATFORM_TIKTOK) == ""
        assert tokens.has_token(PLATFORM_TIKTOK) is False

    def test_le_message_dit_quoi_faire(self):
        assert "keyring" in tokens.UNAVAILABLE_MESSAGE
        assert "export manuel" in tokens.UNAVAILABLE_MESSAGE

    def test_un_compte_s_affiche_sans_son_jeton(self):
        from publishing.tokens import StoredAccount

        account = StoredAccount(platform=PLATFORM_TIKTOK, username="ClipsOfStreams")
        assert account.label == "@ClipsOfStreams"
        assert "token" not in str(account).lower()


class TestApprentissage:
    def test_les_donnees_preparees_parlent_le_vocabulaire_existant(self, draft, history,
                                                                    tmp_path):
        adapters = {PLATFORM_TIKTOK: FakeAdapter(PLATFORM_TIKTOK)}
        records = publisher.publish(draft, [PLATFORM_TIKTOK], history=history,
                                    export_root=tmp_path / "p", adapters=adapters)
        features = publisher.performance_features(records[0], draft)
        for key in ("clip_id", "platform", "published_at", "title", "duration"):
            assert key in features

    def test_les_statistiques_ne_sont_pas_inventees(self, draft, history, tmp_path):
        """Vues et likes n'existent pas au moment de la publication."""
        adapters = {PLATFORM_TIKTOK: FakeAdapter(PLATFORM_TIKTOK)}
        records = publisher.publish(draft, [PLATFORM_TIKTOK], history=history,
                                    export_root=tmp_path / "p", adapters=adapters)
        features = publisher.performance_features(records[0], draft)
        for absent in ("views", "likes", "comments", "shares"):
            assert absent not in features
