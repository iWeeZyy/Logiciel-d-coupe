"""Moteurs de synthese vocale : systeme, Piper, catalogue, telechargement.

Ce qui est REELLEMENT execute ici : la detection des moteurs, le chargement
d'un modele ONNX par le vrai moteur Piper (phonemisation espeak comprise), la
generation d'un WAV, la conversion MP3, le cache, et les telechargements --
contre un serveur HTTP local, pas contre un double.

Ce qui ne peut pas l'etre : la qualite d'une vraie voix. Aucun modele officiel
ne peut etre telecharge depuis l'environnement de developpement, la voix
utilisee est donc un modele synthetique de meme interface (voir
tests/helpers/fake_piper_voice.py).
"""
import http.server
import json
import threading
import wave
from pathlib import Path

import pytest

from core.cancellation import CancelToken
from utils.errors import CancelledError
from voice_studio import downloads, piper_models, tts

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

onnx = pytest.importorskip("onnx", reason="onnx n'est pas installé (dépendance de test)")
piper = pytest.importorskip("piper", reason="piper-tts n'est pas installé")

from tests.helpers.fake_piper_voice import build as build_fake_voice  # noqa: E402


@pytest.fixture
def piper_dir(tmp_path, monkeypatch):
    """Un dossier de voix isole, avec une voix synthetique installee."""
    directory = tmp_path / "piper"
    monkeypatch.setattr(piper_models, "models_dir", lambda: directory)
    monkeypatch.setattr(tts, "cache_dir", lambda: tmp_path / "cache")
    (tmp_path / "cache").mkdir(exist_ok=True)
    build_fake_voice(directory)
    return directory


@pytest.fixture
def piper_voice(piper_dir):
    voices = tts.available_voices("piper")
    assert voices, "la voix synthétique doit être vue comme installée"
    return voices[0]


# ------------------------------------------------------------- detection

def test_the_system_engine_is_always_offered_when_the_machine_has_voices():
    # Hortense et les autres voix Windows restent la solution disponible sans
    # rien telecharger : elles ne doivent jamais disparaitre de la liste.
    engine = tts.SystemVoiceEngine()

    assert engine.name == "system"
    if engine.available():
        assert any(v.engine == "system" for v in tts.available_voices())


def test_piper_says_which_runtime_it_would_use():
    metadata = tts.PiperEngine().metadata()

    assert metadata["engine"] == "piper"
    assert metadata["runtime"] in ("library", "binary", "")


def test_piper_without_any_model_is_not_available(tmp_path, monkeypatch):
    # Un moteur present mais sans voix ne doit pas se declarer disponible :
    # sinon l'interface proposerait un choix qui echouerait a la generation.
    monkeypatch.setattr(piper_models, "models_dir", lambda: tmp_path / "vide")

    assert tts.PiperEngine().voices() == []
    assert tts.PiperEngine().available() is False


def test_an_installed_model_appears_as_a_voice(piper_dir):
    voices = tts.available_voices("piper")

    assert [v.engine for v in voices] == ["piper"]
    assert voices[0].is_french
    assert voices[0].display_label.startswith("🇫🇷")


def test_a_half_installed_voice_is_not_offered(piper_dir):
    # Telechargement interrompu : le .onnx est la, la configuration non.
    piper_models.config_path("fr_FR-test-medium").unlink()

    assert tts.available_voices("piper") == []


def test_a_damaged_model_gives_a_readable_message(piper_dir):
    piper_models.model_path("fr_FR-test-medium").write_bytes(b"ceci n'est pas un modele" * 100)
    voice = tts.available_voices("piper")[0]

    with pytest.raises(tts.TtsError) as excinfo:
        tts.synthesize("Bonjour", "/tmp/inutile.wav", voice=voice, use_cache=False)

    assert "réinstalle" in str(excinfo.value).lower()


# ------------------------------------------------------- generation reelle

def test_piper_really_produces_audio(piper_voice, tmp_path):
    out = tts.synthesize("Bonjour, ceci est un essai de voix locale.",
                         str(tmp_path / "voix.wav"), voice=piper_voice, use_cache=False)

    with wave.open(out) as handle:
        assert handle.getframerate() == 22050
        assert handle.getsampwidth() == 2
        assert handle.getnframes() > 1000          # du son, pas un fichier vide


def test_the_espeak_language_code_of_real_voices_is_repaired(piper_dir):
    """Les vraies voix declarent "fr-fr", que la donnee espeak livree avec
    piper REFUSE ; elle attend "fr". Sans ce rattrapage, aucune voix francaise
    officielle ne fonctionnerait. Verifie sur machine."""
    config = json.loads(piper_models.config_path("fr_FR-test-medium").read_text(encoding="utf-8"))

    assert config["espeak"]["voice"] == "fr-fr"       # comme les voix officielles
    assert tts.PiperEngine()._espeak_voice("fr-fr") == "fr"


def test_the_requested_speed_reaches_the_engine(piper_voice, tmp_path, monkeypatch):
    # Le modele synthetique ne sait pas etirer le temps : on verifie donc la
    # valeur transmise. length_scale est l'INVERSE de la vitesse.
    import piper as piper_module

    seen = {}
    real = piper_module.SynthesisConfig

    def _spy(**kwargs):
        seen.update(kwargs)
        return real(**kwargs)

    monkeypatch.setattr(piper_module, "SynthesisConfig", _spy)
    tts.synthesize("Bonjour", str(tmp_path / "a.wav"), voice=piper_voice, rate=1.25,
                   use_cache=False)

    assert seen["length_scale"] == pytest.approx(1 / 1.25)
    assert seen["normalize_audio"] is True            # evite la saturation


def test_an_absurd_speed_is_clamped(piper_voice, tmp_path, monkeypatch):
    import piper as piper_module

    seen = {}
    real = piper_module.SynthesisConfig
    monkeypatch.setattr(piper_module, "SynthesisConfig",
                        lambda **kw: (seen.update(kw), real(**kw))[1])
    tts.synthesize("Bonjour", str(tmp_path / "a.wav"), voice=piper_voice, rate=99.0,
                   use_cache=False)

    assert seen["length_scale"] == pytest.approx(1 / tts.MAX_RATE)


def test_sentence_pauses_lengthen_the_result(piper_voice, tmp_path):
    def _duration(path):
        with wave.open(path) as handle:
            return handle.getnframes() / handle.getframerate()

    plain = tts.synthesize("Une. Deux. Trois.", str(tmp_path / "sans.wav"),
                           voice=piper_voice, use_cache=False)
    spaced = tts.synthesize("Une. Deux. Trois.", str(tmp_path / "avec.wav"),
                            voice=piper_voice, sentence_pause_s=0.8, use_cache=False)

    assert _duration(spaced) > _duration(plain) + 1.0
    assert not list(tmp_path.glob("*.part*.wav"))     # aucun fichier temporaire oublie


def test_the_wav_can_be_converted_to_mp3(piper_voice, tmp_path):
    wav = tts.synthesize("Bonjour", str(tmp_path / "voix.wav"), voice=piper_voice,
                         use_cache=False)
    mp3 = tts.to_mp3(wav, str(tmp_path / "voix.mp3"))

    assert Path(mp3).stat().st_size > 500


# -------------------------------------------------------------- cache

def test_the_same_request_is_not_synthesized_twice(piper_voice, tmp_path, monkeypatch):
    first = tts.synthesize("Texte identique", str(tmp_path / "un.wav"), voice=piper_voice)

    def _never(*args, **kwargs):
        raise AssertionError("le cache aurait dû répondre")

    monkeypatch.setattr(tts.PiperEngine, "synthesize", _never)
    second = tts.synthesize("Texte identique", str(tmp_path / "deux.wav"), voice=piper_voice)

    assert Path(first).read_bytes() == Path(second).read_bytes()


def test_changing_any_setting_changes_the_cache_key(piper_voice):
    base = tts.cache_key("texte", piper_voice, 1.0, 1.0, 0.0)

    assert tts.cache_key("autre texte", piper_voice, 1.0, 1.0, 0.0) != base
    assert tts.cache_key("texte", piper_voice, 1.25, 1.0, 0.0) != base
    assert tts.cache_key("texte", piper_voice, 1.0, 0.5, 0.0) != base
    assert tts.cache_key("texte", piper_voice, 1.0, 1.0, 0.5) != base


def test_the_engine_is_part_of_the_cache_key(piper_voice):
    other = tts.Voice(id=piper_voice.id, label="idem", language="fr", engine="system")

    assert tts.cache_key("texte", piper_voice, 1.0, 1.0, 0.0) != \
        tts.cache_key("texte", other, 1.0, 1.0, 0.0)


def test_the_cache_can_be_emptied(piper_voice, tmp_path):
    tts.synthesize("Pour le cache", str(tmp_path / "a.wav"), voice=piper_voice)

    assert tts.clear_cache() >= 1
    assert tts.clear_cache() == 0


# -------------------------------------------------------------- apercu

def test_the_preview_only_takes_an_extract():
    long_text = "Une phrase de test. " * 100

    extract = tts.preview_text(long_text)

    assert len(extract) <= tts.PREVIEW_MAX_CHARS
    assert extract.endswith(".")                      # coupe a une fin de phrase


def test_a_short_text_is_previewed_whole():
    assert tts.preview_text("Court.") == "Court."


# ------------------------------------------------------------ catalogue

def test_the_catalogue_only_lists_french_voices_for_now():
    entries = piper_models.catalogue()

    assert entries, "le catalogue livré doit contenir des voix"
    assert all(v.language.startswith("fr") for v in entries)
    assert all(v.path and v.key for v in entries)


def test_an_unknown_voice_is_refused_rather_than_downloaded(tmp_path, monkeypatch):
    monkeypatch.setattr(piper_models, "models_dir", lambda: tmp_path)

    with pytest.raises(piper_models.PiperModelError):
        piper_models.install("nexiste-pas")


def test_a_voice_dropped_in_by_hand_is_recognised(piper_dir):
    # Le catalogue propose ; il ne decide pas de ce qui existe.
    assert "fr_FR-test-medium" in piper_models.installed_keys()
    assert piper_models.describe("fr_FR-test-medium").key == "fr_FR-test-medium"


def test_removing_a_voice_takes_both_files(piper_dir):
    assert piper_models.remove("fr_FR-test-medium") is True

    assert piper_models.installed_keys() == []
    assert not piper_models.model_path("fr_FR-test-medium").exists()
    assert not piper_models.config_path("fr_FR-test-medium").exists()


# --------------------------------------------- telechargement (serveur local)

class _Server:
    """Serveur HTTP local : le telechargement est reellement exerce."""

    def __init__(self, directory):
        handler = http.server.SimpleHTTPRequestHandler
        self.directory = str(directory)

        class Handler(handler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=str(directory), **kwargs)

            def log_message(self, *args):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        host, port = self.httpd.server_address[:2]
        return f"http://{host}:{port}"

    def stop(self):
        self.httpd.shutdown()


@pytest.fixture
def served(tmp_path, monkeypatch):
    """Un faux depot de voix, servi en HTTP, et un catalogue qui pointe dessus."""
    repository = tmp_path / "depot" / "fr" / "fr_FR" / "essai" / "medium"
    repository.mkdir(parents=True)
    build_fake_voice(repository, key="fr_FR-essai-medium")
    server = _Server(tmp_path / "depot")

    monkeypatch.setattr(piper_models, "models_dir", lambda: tmp_path / "voix")
    monkeypatch.setattr(piper_models, "base_url", lambda: server.url)
    monkeypatch.setattr(piper_models, "catalogue", lambda: [piper_models.CatalogueVoice(
        key="fr_FR-essai-medium", label="Français — Essai", language="fr_FR",
        quality="medium", gender="female",
        path="fr/fr_FR/essai/medium/fr_FR-essai-medium")])
    yield server
    server.stop()


def test_a_voice_is_really_downloaded_and_becomes_usable(served, tmp_path):
    seen = []

    piper_models.install("fr_FR-essai-medium",
                         on_progress=lambda f, d, t: seen.append((f, d, t)))

    assert piper_models.is_installed("fr_FR-essai-medium")
    assert seen, "la progression doit être rapportée"
    voices = tts.available_voices("piper")
    assert [v.label for v in voices] == ["Français — Essai"]


def test_a_download_leaves_nothing_behind_when_it_fails(served, tmp_path, monkeypatch):
    # Adresse valide pour la configuration, cassee pour le modele : rien ne doit
    # rester, sinon la voix apparaitrait comme a moitie installee.
    real = piper_models._download_file

    def _fail_on_model(url, target, **kwargs):
        if url.endswith(".onnx"):
            raise piper_models.PiperModelError("échec simulé")
        return real(url, target, **kwargs)

    monkeypatch.setattr(piper_models, "_download_file", _fail_on_model)

    with pytest.raises(piper_models.PiperModelError):
        piper_models.install("fr_FR-essai-medium")

    assert piper_models.installed_keys() == []
    assert not piper_models.config_path("fr_FR-essai-medium").exists()


def test_cancelling_a_download_keeps_the_partial_file_for_later(served, tmp_path):
    token = CancelToken()
    calls = {"n": 0}

    def _cancel_after_first_chunk(fraction, done, total):
        calls["n"] += 1
        if calls["n"] >= 1:
            token.cancel()

    with pytest.raises(CancelledError):
        piper_models.install("fr_FR-essai-medium", on_progress=_cancel_after_first_chunk,
                             cancel_token=token)

    assert not piper_models.is_installed("fr_FR-essai-medium")


def test_a_missing_voice_on_the_server_is_explained(served, monkeypatch):
    monkeypatch.setattr(piper_models, "catalogue", lambda: [piper_models.CatalogueVoice(
        key="fr_FR-absente-medium", label="Absente", language="fr_FR",
        path="fr/fr_FR/absente/medium/fr_FR-absente-medium")])

    with pytest.raises(piper_models.PiperModelError) as excinfo:
        piper_models.install("fr_FR-absente-medium")

    assert "catalogue" in str(excinfo.value).lower()


def test_a_full_disk_is_announced_before_writing(served, monkeypatch):
    # Le controle vit dans voice_studio/downloads.py, partage avec le
    # gestionnaire des modeles de langue.
    monkeypatch.setattr(downloads, "free_space", lambda directory: 1024)

    with pytest.raises(piper_models.PiperModelError) as excinfo:
        piper_models.install("fr_FR-essai-medium")

    assert "espace disque" in str(excinfo.value).lower()


def test_an_unreachable_server_is_explained(tmp_path, monkeypatch):
    monkeypatch.setattr(piper_models, "models_dir", lambda: tmp_path / "voix")
    monkeypatch.setattr(piper_models, "base_url", lambda: "http://127.0.0.1:1")
    monkeypatch.setattr(piper_models, "catalogue", lambda: [piper_models.CatalogueVoice(
        key="fr_FR-essai-medium", label="Essai", language="fr_FR",
        path="fr/fr_FR/essai/medium/fr_FR-essai-medium")])

    with pytest.raises(piper_models.PiperModelError) as excinfo:
        piper_models.install("fr_FR-essai-medium")

    assert "connexion" in str(excinfo.value).lower()


# ------------------------------------------------- coexistence des moteurs

def test_switching_engines_changes_the_voice_list(piper_dir):
    system_voices = tts.available_voices("system")
    piper_voices = tts.available_voices("piper")

    assert all(v.engine == "piper" for v in piper_voices)
    assert all(v.engine == "system" for v in system_voices)
    assert not set(v.id for v in piper_voices) & set(v.id for v in system_voices)


def test_the_system_engine_still_works_beside_piper(piper_dir, tmp_path):
    system = tts.available_voices("system")
    if not system:
        pytest.skip("aucune voix système sur cette machine")

    out = tts.synthesize("Bonjour", str(tmp_path / "systeme.wav"), voice=system[0],
                         use_cache=False)

    with wave.open(out) as handle:
        assert handle.getnframes() > 0


# ------------------------------------------------- installation du moteur

@pytest.fixture
def served_engine(tmp_path, monkeypatch):
    """Une archive de moteur servie en HTTP local, comme le ferait GitHub."""
    import zipfile

    depot = tmp_path / "depot"
    depot.mkdir()
    archive = depot / "piper_test.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("piper/piper", "#!/bin/sh\necho piper\n")
        bundle.writestr("piper/libpiper.so", "binaire factice")
    server = _Server(depot)

    monkeypatch.setattr(piper_models, "models_dir", lambda: tmp_path / "voix")
    monkeypatch.setattr(piper_models, "engine_url",
                        lambda platform_name=None: f"{server.url}/piper_test.zip")
    yield server
    server.stop()


def test_the_engine_archive_is_downloaded_and_extracted(served_engine):
    binary = piper_models.install_engine()

    assert binary.is_file() and binary.name == "piper"
    assert piper_models.engine_binary() == binary


def test_a_corrupt_engine_archive_leaves_nothing_installed(served_engine, tmp_path, monkeypatch):
    corrupt = tmp_path / "depot" / "piper_test.zip"
    corrupt.write_bytes(b"ceci n'est pas une archive")

    with pytest.raises(piper_models.PiperModelError) as excinfo:
        piper_models.install_engine()

    assert "illisible" in str(excinfo.value).lower()
    assert piper_models.engine_binary() is None


def test_a_system_without_a_declared_engine_address_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(piper_models, "models_dir", lambda: tmp_path / "voix")
    monkeypatch.setattr(piper_models, "engine_url", lambda platform_name=None: "")

    with pytest.raises(piper_models.PiperModelError):
        piper_models.install_engine()


def test_the_windows_address_is_the_one_used_on_windows():
    # Le catalogue livre doit couvrir Windows : c'est la cible de l'application.
    assert piper_models.engine_url("win32").endswith(".zip")
    assert "piper" in piper_models.engine_url("win32")


# ------------------------------------------- detection du programme piper

def _install_fake_engine(directory, nested: bool = True, name: str = "piper"):
    """Pose un faux programme piper, comme le fait l'archive officielle."""
    target = directory / "piper" if nested else directory
    target.mkdir(parents=True, exist_ok=True)
    binary = target / name
    binary.write_text("#!/bin/sh\necho piper\n", encoding="utf-8")
    binary.chmod(0o755)
    return binary


def test_the_engine_is_found_inside_the_folder_of_the_archive(tmp_path, monkeypatch):
    """DEFAUT REEL : l'archive officielle range le programme dans un
    sous-dossier « piper/ ». La recherche ne regardait que la racine, donc
    l'application proposait d'installer un moteur DEJA installe et les voix
    telechargees restaient inutilisables."""
    monkeypatch.setattr(piper_models, "models_dir", lambda: tmp_path / "voix")
    monkeypatch.delenv("PIPER_BIN", raising=False)
    monkeypatch.setattr(tts.shutil, "which", lambda name: None)
    binary = _install_fake_engine(tmp_path / "piper-bin", nested=True)

    engine = tts.PiperEngine()

    assert engine.runtime() in ("library", "binary")
    assert str(binary) == str(piper_models.engine_binary())
    assert engine._binary == str(binary)


def test_the_engine_is_also_found_at_the_root(tmp_path, monkeypatch):
    monkeypatch.setattr(piper_models, "models_dir", lambda: tmp_path / "voix")
    monkeypatch.delenv("PIPER_BIN", raising=False)
    monkeypatch.setattr(tts.shutil, "which", lambda name: None)
    binary = _install_fake_engine(tmp_path / "piper-bin", nested=False)

    assert tts.PiperEngine()._binary == str(binary)


def test_a_windows_executable_is_recognised_too(tmp_path, monkeypatch):
    monkeypatch.setattr(piper_models, "models_dir", lambda: tmp_path / "voix")
    monkeypatch.delenv("PIPER_BIN", raising=False)
    monkeypatch.setattr(tts.shutil, "which", lambda name: None)
    binary = _install_fake_engine(tmp_path / "piper-bin", nested=True, name="piper.exe")

    assert tts.PiperEngine()._binary == str(binary)


def test_the_environment_variable_wins(tmp_path, monkeypatch):
    monkeypatch.setattr(piper_models, "models_dir", lambda: tmp_path / "voix")
    chosen = _install_fake_engine(tmp_path / "ailleurs", nested=False)
    monkeypatch.setenv("PIPER_BIN", str(chosen))

    assert tts.PiperEngine()._binary == str(chosen)


def test_the_dialog_and_the_engine_agree_on_what_is_installed(tmp_path, monkeypatch):
    # Le desaccord entre les deux recherches est ce qui a produit le bug :
    # la fenetre voyait le moteur, le moteur ne se voyait pas lui-meme.
    monkeypatch.setattr(piper_models, "models_dir", lambda: tmp_path / "voix")
    monkeypatch.delenv("PIPER_BIN", raising=False)
    monkeypatch.setattr(tts.shutil, "which", lambda name: None)
    _install_fake_engine(tmp_path / "piper-bin", nested=True)

    seen_by_dialog = piper_models.engine_binary() is not None
    seen_by_engine = bool(tts.PiperEngine()._binary)

    assert seen_by_dialog == seen_by_engine is True
