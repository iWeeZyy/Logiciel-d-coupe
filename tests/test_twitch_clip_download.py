"""Telechargement d'un clip Twitch.

Tests purs : yt-dlp est remplace par un faux, aucun appel reseau. Ils portent
sur ce que le module garantit -- l'adresse construite, la qualite demandee,
l'annulation, la progression, et des messages d'erreur comprehensibles.
"""
from __future__ import annotations

import sys
import types

import pytest

from core.cancellation import CancelToken
from radar import clip_download
from utils.errors import CancelledError, MediaNotAvailableError


class FakeYoutubeDL:
    """Faux yt-dlp : ecrit un fichier et rejoue une progression."""

    last_options: dict = {}
    error: Exception | None = None
    steps = ((512, 2048), (2048, 2048))

    def __init__(self, options):
        FakeYoutubeDL.last_options = dict(options)
        self.options = options

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def extract_info(self, url, download=True):
        if FakeYoutubeDL.error is not None:
            raise FakeYoutubeDL.error
        for hook in self.options.get("progress_hooks", []):
            for done, total in FakeYoutubeDL.steps:
                hook({"status": "downloading", "downloaded_bytes": done,
                      "total_bytes": total})
        template = self.options["outtmpl"]
        path = template.replace("%(id)s", "AbcClip").replace("%(ext)s", "mp4")
        from pathlib import Path

        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"x" * 4096)
        return {"id": "AbcClip", "height": 1080}


@pytest.fixture
def fake_ytdlp(monkeypatch):
    FakeYoutubeDL.error = None
    FakeYoutubeDL.steps = ((512, 2048), (2048, 2048))
    module = types.ModuleType("yt_dlp")
    module.YoutubeDL = FakeYoutubeDL
    monkeypatch.setitem(sys.modules, "yt_dlp", module)
    return FakeYoutubeDL


class TestAdresse:
    def test_un_identifiant_devient_une_url_de_clip(self):
        assert clip_download.clip_url("AmazonianKindBaboon") == \
            "https://clips.twitch.tv/AmazonianKindBaboon"

    def test_une_url_complete_est_gardee_telle_quelle(self):
        url = "https://www.twitch.tv/ponce/clip/AbcDef"
        assert clip_download.clip_url(url) == url

    def test_une_adresse_vide_est_refusee(self):
        with pytest.raises(MediaNotAvailableError):
            clip_download.clip_url("   ")

    def test_une_adresse_incomprehensible_est_refusee(self):
        with pytest.raises(MediaNotAvailableError):
            clip_download.clip_url("pas une adresse !")


class TestTelechargement:
    def test_le_fichier_est_produit(self, tmp_path, fake_ytdlp):
        path = clip_download.download_clip("AbcClip", str(tmp_path))
        assert path.endswith("AbcClip.mp4")

    def test_la_meilleure_qualite_est_demandee(self, tmp_path, fake_ytdlp):
        clip_download.download_clip("AbcClip", str(tmp_path))
        selector = fake_ytdlp.last_options["format"]
        assert "[ext=mp4]" not in selector
        assert "[height<=" not in selector

    def test_un_plafond_de_definition_est_transmis(self, tmp_path, fake_ytdlp):
        clip_download.download_clip("AbcClip", str(tmp_path), max_height=720)
        assert "[height<=720]" in fake_ytdlp.last_options["format"]

    def test_la_progression_est_rapportee(self, tmp_path, fake_ytdlp):
        seen = []
        clip_download.download_clip("AbcClip", str(tmp_path), on_progress=seen.append)
        assert seen == [0.25, 1.0]

    def test_une_taille_inconnue_ne_produit_pas_une_fausse_progression(self, tmp_path,
                                                                       fake_ytdlp):
        fake_ytdlp.steps = ((512, None),)
        seen = []
        clip_download.download_clip("AbcClip", str(tmp_path), on_progress=seen.append)
        assert seen == [None], "une barre qui avance au hasard vaut moins qu'un aveu"

    def test_l_annulation_interrompt_le_transfert(self, tmp_path, fake_ytdlp):
        token = CancelToken()
        token.cancel()
        with pytest.raises(CancelledError):
            clip_download.download_clip("AbcClip", str(tmp_path), cancel_token=token)


class TestErreurs:
    def test_un_clip_supprime_est_explique(self, tmp_path, fake_ytdlp):
        fake_ytdlp.error = RuntimeError("ERROR: Clip does not exist")
        with pytest.raises(MediaNotAvailableError) as error:
            clip_download.download_clip("AbcClip", str(tmp_path))
        assert "n'existe plus" in str(error.value)

    def test_une_vod_est_expliquee_et_non_tentee(self, tmp_path, fake_ytdlp):
        fake_ytdlp.error = RuntimeError("ERROR: Unsupported URL: https://twitch.tv/videos/1")
        with pytest.raises(MediaNotAvailableError) as error:
            clip_download.download_clip("https://twitch.tv/videos/1", str(tmp_path))
        assert "que pour les clips" in str(error.value).lower() or \
               "Seuls les clips" in str(error.value)

    def test_une_panne_reseau_est_expliquee(self, tmp_path, fake_ytdlp):
        fake_ytdlp.error = RuntimeError("Connection timed out")
        with pytest.raises(MediaNotAvailableError) as error:
            clip_download.download_clip("AbcClip", str(tmp_path))
        assert "injoignable" in str(error.value)

    def test_aucune_trace_python_dans_le_message(self, tmp_path, fake_ytdlp):
        fake_ytdlp.error = RuntimeError("boom inattendu")
        with pytest.raises(MediaNotAvailableError) as error:
            clip_download.download_clip("AbcClip", str(tmp_path))
        assert "Traceback" not in str(error.value)


class TestDroits:
    def test_l_avertissement_ne_confond_pas_fichier_et_licence(self):
        notice = clip_download.RIGHTS_NOTICE
        assert "n'est pas céder des droits" in notice
        assert "libre de droit" not in notice.lower()
