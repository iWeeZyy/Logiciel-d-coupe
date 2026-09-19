"""Cache disque de l'image originale d'un article : ne jamais retelecharger
la meme image deux fois pour la meme selection d'actualite, et signaler
(sans jamais etirer silencieusement) une resolution insuffisante.

Le cache est redirige vers tmp_path via CACHE_DIR monkeypatche -- jamais
d'ecriture dans le vrai dossier utilisateur pendant les tests.
"""
from io import BytesIO

import pytest
from PIL import Image

from news_story import image_cache
from news_story.image_cache import CachedImage, ImageFetchError

_URL = "https://example.test/img/cover.jpg"


def _png_bytes(width: int, height: int) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (width, height), color=(200, 100, 50)).save(buf, format="PNG")
    return buf.getvalue()


class _FakeResponse:
    def __init__(self, content: bytes, status_error: Exception | None = None):
        self.content = content
        self._status_error = status_error

    def raise_for_status(self):
        if self._status_error:
            raise self._status_error


@pytest.fixture(autouse=True)
def _isolated_cache_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(image_cache, "CACHE_DIR", tmp_path / "news_images")


class TestGetCached:
    def test_returns_none_when_nothing_was_ever_downloaded(self):
        assert image_cache.get_cached(_URL) is None

    def test_returns_none_when_the_meta_json_is_corrupted(self):
        img_path, meta_path = image_cache._paths_for(_URL)
        img_path.parent.mkdir(parents=True, exist_ok=True)
        img_path.write_bytes(b"donnees")
        meta_path.write_text("{ceci n'est pas du json valide", encoding="utf-8")

        assert image_cache.get_cached(_URL) is None


class TestDownload:
    def test_downloads_and_reports_measured_dimensions(self, monkeypatch):
        monkeypatch.setattr("requests.get", lambda *a, **k: _FakeResponse(_png_bytes(800, 600)))

        result = image_cache.download(_URL)

        assert isinstance(result, CachedImage)
        assert (result.width, result.height) == (800, 600)
        assert result.path.is_file()

    def test_a_url_already_in_cache_is_never_downloaded_again(self, monkeypatch):
        calls = []

        def _fake_get(*args, **kwargs):
            calls.append(1)
            return _FakeResponse(_png_bytes(800, 600))

        monkeypatch.setattr("requests.get", _fake_get)

        image_cache.download(_URL)
        image_cache.download(_URL)

        assert len(calls) == 1

    def test_a_second_download_returns_the_same_cached_file(self, monkeypatch):
        monkeypatch.setattr("requests.get", lambda *a, **k: _FakeResponse(_png_bytes(800, 600)))

        first = image_cache.download(_URL)
        second = image_cache.download(_URL)

        assert first.path == second.path

    def test_a_network_error_raises_image_fetch_error(self, monkeypatch):
        import requests

        def _raise(*a, **k):
            raise requests.exceptions.ConnectionError("hors ligne")

        monkeypatch.setattr("requests.get", _raise)

        with pytest.raises(ImageFetchError):
            image_cache.download(_URL)

    def test_a_timeout_raises_image_fetch_error(self, monkeypatch):
        import requests

        def _raise(*a, **k):
            raise requests.exceptions.Timeout("delai depasse")

        monkeypatch.setattr("requests.get", _raise)

        with pytest.raises(ImageFetchError):
            image_cache.download(_URL)

    def test_an_http_error_status_raises_image_fetch_error(self, monkeypatch):
        import requests

        monkeypatch.setattr(
            "requests.get",
            lambda *a, **k: _FakeResponse(b"", status_error=requests.exceptions.HTTPError("404")),
        )

        with pytest.raises(ImageFetchError):
            image_cache.download(_URL)

    def test_an_unsupported_or_corrupted_format_raises_image_fetch_error(self, monkeypatch):
        monkeypatch.setattr("requests.get", lambda *a, **k: _FakeResponse(b"ceci n'est pas une image"))

        with pytest.raises(ImageFetchError):
            image_cache.download(_URL)

    def test_an_empty_response_body_raises_image_fetch_error(self, monkeypatch):
        monkeypatch.setattr("requests.get", lambda *a, **k: _FakeResponse(b""))

        with pytest.raises(ImageFetchError):
            image_cache.download(_URL)

    def test_a_failed_download_writes_nothing_to_the_cache(self, monkeypatch):
        monkeypatch.setattr("requests.get", lambda *a, **k: _FakeResponse(b"pas une image"))

        with pytest.raises(ImageFetchError):
            image_cache.download(_URL)

        assert image_cache.get_cached(_URL) is None


class TestLowResolutionFlag:
    def test_an_image_below_the_threshold_is_flagged_low_resolution(self):
        img = CachedImage(path=None, width=200, height=150)
        assert img.is_low_resolution is True

    def test_an_image_at_or_above_the_threshold_is_not_flagged(self):
        img = CachedImage(path=None, width=1080, height=1920)
        assert img.is_low_resolution is False

    def test_only_one_undersized_dimension_is_enough_to_flag_it(self):
        # Une banniere tres large mais peu haute ne remplirait pas
        # honnetement une Story 9:16 -- ne jamais l'accepter comme "assez
        # grande" simplement parce que la largeur est confortable.
        img = CachedImage(path=None, width=2000, height=100)
        assert img.is_low_resolution is True
