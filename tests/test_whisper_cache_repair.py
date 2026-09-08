"""Reparation d'un cache Hugging Face incomplet.

Bug reel constate en usage : le telechargement de large-v3 (~3 Go) est
interrompu, le dossier du modele existe mais son fichier de poids non.
huggingface_hub considere alors le modele comme deja present et ne
retelecharge plus rien -- l'application echoue a chaque lancement, sans issue
autre que supprimer un dossier de cache que l'utilisateur n'a jamais cree.

Tests purs : aucun modele n'est charge, seule la logique de decision est
exercee (via un faux WhisperModel injecte dans le module).
"""
import sys
import types

import pytest

from transcription import whisper_engine
from utils.errors import ModelDownloadError


def _install_fake_faster_whisper(monkeypatch, behaviour):
    """Injecte un faux module faster_whisper dont WhisperModel suit `behaviour`,
    une fonction appelee a chaque tentative de chargement."""
    calls = {"count": 0}

    def fake_model(name, device=None, compute_type=None):
        calls["count"] += 1
        return behaviour(calls["count"])

    module = types.ModuleType("faster_whisper")
    module.WhisperModel = fake_model
    monkeypatch.setitem(sys.modules, "faster_whisper", module)
    return calls


def test_cache_dir_is_named_after_the_real_hugging_face_layout():
    path = whisper_engine.hf_cache_dir_for("large-v3")

    assert path.name == "models--Systran--faster-whisper-large-v3"
    assert path.parent.name == "hub"


def test_an_incomplete_cache_is_deleted_and_the_download_retried(monkeypatch, tmp_path):
    cache_dir = tmp_path / "models--Systran--faster-whisper-large-v3"
    cache_dir.mkdir()
    (cache_dir / "snapshots").mkdir()
    monkeypatch.setattr(whisper_engine, "hf_cache_dir_for", lambda name: cache_dir)

    def behaviour(attempt):
        if attempt == 1:
            raise RuntimeError("Unable to open file 'model.bin' in model '/x/snapshots/abc'")
        return "modele charge"

    calls = _install_fake_faster_whisper(monkeypatch, behaviour)

    assert whisper_engine._load_model("large-v3", "cpu", "int8") == "modele charge"
    assert calls["count"] == 2, "le chargement doit etre retente apres nettoyage"
    assert not cache_dir.exists(), "le cache incomplet doit avoir ete supprime"


def test_a_working_cache_is_never_deleted(monkeypatch, tmp_path):
    cache_dir = tmp_path / "models--Systran--faster-whisper-small"
    cache_dir.mkdir()
    monkeypatch.setattr(whisper_engine, "hf_cache_dir_for", lambda name: cache_dir)
    calls = _install_fake_faster_whisper(monkeypatch, lambda attempt: "modele charge")

    assert whisper_engine._load_model("small", "cpu", "int8") == "modele charge"
    assert calls["count"] == 1
    assert cache_dir.exists()


def test_a_network_error_does_not_wipe_the_cache(monkeypatch, tmp_path):
    # Une panne reseau n'est pas un cache corrompu : supprimer le modele
    # deja telecharge obligerait a tout retelecharger pour rien.
    cache_dir = tmp_path / "models--Systran--faster-whisper-small"
    cache_dir.mkdir()
    monkeypatch.setattr(whisper_engine, "hf_cache_dir_for", lambda name: cache_dir)

    def behaviour(attempt):
        raise ConnectionError("Max retries exceeded with url: /Systran/faster-whisper-small")

    _install_fake_faster_whisper(monkeypatch, behaviour)

    with pytest.raises(ConnectionError):
        whisper_engine._load_model("small", "cpu", "int8")
    assert cache_dir.exists()


def test_a_second_failure_names_the_folder_and_the_disk_space_needed(monkeypatch, tmp_path):
    cache_dir = tmp_path / "models--Systran--faster-whisper-large-v3"
    cache_dir.mkdir()
    monkeypatch.setattr(whisper_engine, "hf_cache_dir_for", lambda name: cache_dir)

    def behaviour(attempt):
        raise RuntimeError("Unable to open file 'model.bin'")

    _install_fake_faster_whisper(monkeypatch, behaviour)

    with pytest.raises(ModelDownloadError) as excinfo:
        whisper_engine._load_model("large-v3", "cpu", "int8")

    message = str(excinfo.value)
    assert "3 Go" in message           # l'ordre de grandeur du telechargement
    assert "espace disque" in message
    assert "modele plus petit" in message


def test_no_cache_directory_means_the_original_error_is_raised(monkeypatch, tmp_path):
    monkeypatch.setattr(whisper_engine, "hf_cache_dir_for", lambda name: tmp_path / "absent")

    def behaviour(attempt):
        raise RuntimeError("Unable to open file 'model.bin'")

    _install_fake_faster_whisper(monkeypatch, behaviour)

    with pytest.raises(RuntimeError):
        whisper_engine._load_model("small", "cpu", "int8")
