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
import threading
import time
import types

import pytest

from pathlib import Path

from transcription import whisper_engine
from utils.errors import ModelDownloadError


def write_model_bin(directory, size=None):
    """Ecrit un model.bin de taille plausible.

    Un fichier d'un octet ne represente plus un modele complet : un model.bin
    en dessous du seuil est justement ce que laisse un telechargement tronque,
    et le detecter est le but de ce module. Le fichier est creux, donc
    instantane et sans occupation reelle sur le disque.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    with open(directory / "model.bin", "wb") as handle:
        handle.truncate(size if size is not None else whisper_engine.MIN_MODEL_BIN_BYTES + 1)
    return directory / "model.bin"


def _install_fake_faster_whisper(monkeypatch, behaviour, plain_dir=None):
    """Injecte un faux module faster_whisper dont WhisperModel suit `behaviour`,
    une fonction appelee a chaque tentative de chargement.

    `_download_with_progress` est neutralise : depuis que la reparation se
    rabat sur un dossier simple, un test qui ne le stoppe pas partirait
    telecharger un vrai modele sur le reseau.
    """
    calls = {"count": 0, "downloads": 0}

    def fake_model(name, device=None, compute_type=None):
        calls["count"] += 1
        return behaviour(calls["count"])

    def fake_download(model_name, cache_dir, on_progress=None, cancel_token=None,
                      output_dir=None):
        calls["downloads"] += 1
        if plain_dir is not None:
            write_model_bin(plain_dir)

    module = types.ModuleType("faster_whisper")
    module.WhisperModel = fake_model
    monkeypatch.setitem(sys.modules, "faster_whisper", module)
    monkeypatch.setattr(whisper_engine, "_download_with_progress", fake_download)
    if plain_dir is not None:
        monkeypatch.setattr(whisper_engine, "plain_model_dir", lambda name: Path(plain_dir))
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


def test_a_model_that_stays_unreadable_reports_the_state_of_the_file(monkeypatch, tmp_path):
    """Un fichier present mais illisible est retelecharge dans un dossier simple.

    Ce test attendait auparavant que l'erreur d'origine remonte telle quelle
    quand aucun cache n'existait. C'etait un abandon : un fichier de poids
    illisible se repare en le retelechargeant ailleurs, et si cela echoue
    encore, le message doit dire ce qu'il y a reellement sur le disque plutot
    que de renvoyer le message de la bibliotheque.
    """
    monkeypatch.setattr(whisper_engine, "hf_cache_dir_for", lambda name: tmp_path / "absent")
    monkeypatch.setattr(whisper_engine, "free_disk_mb", lambda path: 10000.0)

    def behaviour(attempt):
        raise RuntimeError("Unable to open file 'model.bin'")

    calls = _install_fake_faster_whisper(monkeypatch, behaviour,
                                         plain_dir=tmp_path / "simple")

    with pytest.raises(ModelDownloadError) as excinfo:
        whisper_engine._load_model("small", "cpu", "int8")

    assert calls["downloads"] == 1, "un retelechargement complet doit etre tente"
    message = str(excinfo.value)
    assert "Etat du fichier" in message
    assert "model.bin" in message


def test_the_cache_root_follows_hugging_face_own_setting(monkeypatch):
    # Le cache est deplacable (HF_HOME / HF_HUB_CACHE) quand le disque systeme
    # est trop petit. Reconstruire ~/.cache a la main nous ferait supprimer un
    # dossier qui n'est pas celui que la bibliotheque utilise reellement.
    from huggingface_hub import constants as hf_constants

    monkeypatch.setattr(hf_constants, "HF_HUB_CACHE", r"E:\huggingface\hub", raising=False)

    root = whisper_engine.hf_hub_cache_root()

    assert str(root).replace("\\", "/").endswith("huggingface/hub")
    assert whisper_engine.hf_cache_dir_for("small").name == "models--Systran--faster-whisper-small"


def test_a_download_is_refused_before_starting_when_the_disk_is_too_small(monkeypatch, tmp_path):
    """La verification d'espace est faite par la fonction qui telecharge.

    Elle etait auparavant declenchee depuis le chargement du modele ; elle
    appartient a ensure_model_downloaded, seul endroit ou un telechargement
    peut reellement commencer.
    """
    cache_dir = tmp_path / "models--Systran--faster-whisper-large-v3"  # n'existe pas
    monkeypatch.setattr(whisper_engine, "hf_cache_dir_for", lambda name: cache_dir)
    monkeypatch.setattr(whisper_engine, "free_disk_mb", lambda path: 900.0)
    calls = _install_fake_faster_whisper_utils(monkeypatch, None, lambda: cache_dir)

    with pytest.raises(ModelDownloadError) as excinfo:
        whisper_engine.ensure_model_downloaded("large-v3")

    assert calls["downloads"] == 0, "rien ne doit etre telecharge si la place manque"
    message = str(excinfo.value)
    assert "3.0 Go" in message and "0.9" in message
    assert "HF_HOME" in message


def test_enough_space_lets_the_download_proceed(monkeypatch, tmp_path):
    cache_dir = tmp_path / "models--Systran--faster-whisper-small"
    monkeypatch.setattr(whisper_engine, "hf_cache_dir_for", lambda name: cache_dir)
    monkeypatch.setattr(whisper_engine, "free_disk_mb", lambda path: 5000.0)
    calls = _install_fake_faster_whisper(monkeypatch, lambda attempt: "modele charge")

    assert whisper_engine._load_model("small", "cpu", "int8") == "modele charge"
    assert calls["count"] == 1


def test_an_unmeasurable_disk_never_blocks_a_download(monkeypatch, tmp_path):
    # Un doute sur la mesure ne doit pas empecher l'utilisateur d'essayer.
    cache_dir = tmp_path / "models--Systran--faster-whisper-large-v3"
    monkeypatch.setattr(whisper_engine, "hf_cache_dir_for", lambda name: cache_dir)
    monkeypatch.setattr(whisper_engine, "free_disk_mb", lambda path: None)
    calls = _install_fake_faster_whisper(monkeypatch, lambda attempt: "modele charge")

    assert whisper_engine._load_model("large-v3", "cpu", "int8") == "modele charge"
    assert calls["count"] == 1


def test_an_already_downloaded_model_is_never_blocked_by_a_full_disk(monkeypatch, tmp_path):
    # Le modele est deja la : rien a telecharger, l'espace libre n'a plus
    # aucune importance et refuser ici serait absurde.
    cache_dir = tmp_path / "models--Systran--faster-whisper-large-v3"
    cache_dir.mkdir()
    monkeypatch.setattr(whisper_engine, "hf_cache_dir_for", lambda name: cache_dir)
    monkeypatch.setattr(whisper_engine, "free_disk_mb", lambda path: 10.0)
    calls = _install_fake_faster_whisper(monkeypatch, lambda attempt: "modele charge")

    assert whisper_engine._load_model("large-v3", "cpu", "int8") == "modele charge"
    assert calls["count"] == 1


def test_free_disk_mb_walks_up_to_an_existing_parent(tmp_path):
    # Au premier telechargement le dossier du cache n'existe pas encore : la
    # mesure doit porter sur le volume, pas echouer.
    missing = tmp_path / "pas" / "encore" / "cree"

    assert whisper_engine.free_disk_mb(missing) is not None


def _install_fake_faster_whisper_utils(monkeypatch, cached_path, on_download):
    """Faux faster_whisper.utils : download_model(local_files_only=True) renvoie
    `cached_path` (ou leve si None), et le telechargement reel delegue a
    `on_download`."""
    calls = {"downloads": 0}
    state = {"path": cached_path}

    def fake_download_model(name, local_files_only=False, **kwargs):
        if local_files_only:
            # Apres un telechargement reussi, le modele EST dans le cache : le
            # faux doit le refleter, sinon ensure_model_downloaded conclut a un
            # echec et part se rabattre sur un dossier simple.
            if state["path"] is None:
                raise FileNotFoundError("modele absent du cache")
            return str(state["path"])
        calls["downloads"] += 1
        state["path"] = on_download()
        return str(state["path"])

    utils = types.ModuleType("faster_whisper.utils")
    utils.download_model = fake_download_model
    utils._MODELS = {"large": "Systran/faster-whisper-large-v3",
                     "small": "Systran/faster-whisper-small"}
    package = types.ModuleType("faster_whisper")
    package.utils = utils
    monkeypatch.setitem(sys.modules, "faster_whisper", package)
    monkeypatch.setitem(sys.modules, "faster_whisper.utils", utils)
    return calls


def test_large_resolves_to_the_v3_repository(monkeypatch):
    # "large" ne pointe PAS sur un depot "...-large" : deduire le nom du dossier
    # du nom du modele nous ferait chercher un dossier qui n'existe pas, et la
    # reparation ne se declencherait jamais pour ce modele.
    _install_fake_faster_whisper_utils(monkeypatch, None, lambda: "")

    assert whisper_engine.model_repo_id("large") == "Systran/faster-whisper-large-v3"
    assert whisper_engine.hf_cache_dir_for("large").name == "models--Systran--faster-whisper-large-v3"


def test_a_snapshot_without_its_weights_is_not_complete(tmp_path):
    snapshot = tmp_path / "snapshots" / "abc"
    snapshot.mkdir(parents=True)
    (snapshot / "config.json").write_text("{}", encoding="utf-8")

    assert not whisper_engine.snapshot_is_complete(snapshot)

    write_model_bin(snapshot)
    assert whisper_engine.snapshot_is_complete(snapshot)


def test_a_truncated_weights_file_is_not_complete_either(tmp_path):
    """Verifier la seule PRESENCE du fichier laissait passer une copie
    interrompue : le chargement echouait alors avec un message de bibliotheque,
    et la reparation ne se declenchait pas."""
    snapshot = tmp_path / "snapshots" / "abc"
    write_model_bin(snapshot, size=1024)

    assert not whisper_engine.snapshot_is_complete(snapshot)
    assert "tronqué" in whisper_engine.describe_snapshot(snapshot)


def test_the_state_of_the_model_is_described_in_plain_words(tmp_path):
    snapshot = tmp_path / "snapshots" / "abc"
    snapshot.mkdir(parents=True)

    assert "absent" in whisper_engine.describe_snapshot(snapshot)
    write_model_bin(snapshot)
    assert "model.bin fait" in whisper_engine.describe_snapshot(snapshot)
    assert "aucun dossier" in whisper_engine.describe_snapshot(None)


def test_a_complete_cache_downloads_nothing(monkeypatch, tmp_path):
    snapshot = tmp_path / "snapshots" / "abc"
    write_model_bin(snapshot)
    calls = _install_fake_faster_whisper_utils(monkeypatch, snapshot, lambda: snapshot)

    whisper_engine.ensure_model_downloaded("small")

    assert calls["downloads"] == 0


def test_an_incomplete_snapshot_is_removed_then_redownloaded(monkeypatch, tmp_path):
    cache_dir = tmp_path / "models--Systran--faster-whisper-small"
    snapshot = cache_dir / "snapshots" / "abc"
    snapshot.mkdir(parents=True)
    (snapshot / "config.json").write_text("{}", encoding="utf-8")   # pas de model.bin
    monkeypatch.setattr(whisper_engine, "hf_cache_dir_for", lambda name: cache_dir)
    monkeypatch.setattr(whisper_engine, "free_disk_mb", lambda path: 10000.0)

    def redownload():
        write_model_bin(snapshot)
        return snapshot

    calls = _install_fake_faster_whisper_utils(monkeypatch, snapshot, redownload)

    whisper_engine.ensure_model_downloaded("small")

    assert calls["downloads"] == 1
    assert (snapshot / "model.bin").is_file()


def test_the_download_reports_its_progress(monkeypatch, tmp_path):
    # Sans compte rendu, l'interface reste figee pendant tout le telechargement
    # et fermer une application qui semble bloquee laisse le cache incomplet.
    cache_dir = tmp_path / "models--Systran--faster-whisper-small"
    monkeypatch.setattr(whisper_engine, "hf_cache_dir_for", lambda name: cache_dir)
    monkeypatch.setattr(whisper_engine, "free_disk_mb", lambda path: 10000.0)

    def slow_download():
        snapshot = cache_dir / "snapshots" / "abc"
        snapshot.mkdir(parents=True, exist_ok=True)
        for i in range(3):
            (snapshot / f"part{i}.bin").write_bytes(b"z" * 400_000)
            time.sleep(0.6)
        write_model_bin(snapshot)
        return snapshot

    _install_fake_faster_whisper_utils(monkeypatch, None, slow_download)

    seen: list[tuple[float, float | None]] = []
    whisper_engine.ensure_model_downloaded("small", lambda done, total: seen.append((done, total)))

    assert seen, "au moins un compte rendu doit remonter pendant le telechargement"
    assert seen[-1][1] == 484.0, "la taille annoncee du modele doit accompagner l'avancement"
    assert seen[-1][0] > seen[0][0], "l'avancement doit progresser"


def test_a_download_failure_is_raised_in_the_calling_thread(monkeypatch, tmp_path):
    # Une erreur survenue dans le fil de telechargement ne doit pas etre avalee.
    cache_dir = tmp_path / "models--Systran--faster-whisper-small"
    monkeypatch.setattr(whisper_engine, "hf_cache_dir_for", lambda name: cache_dir)
    monkeypatch.setattr(whisper_engine, "free_disk_mb", lambda path: 10000.0)

    def failing():
        raise ConnectionError("coupure reseau")

    _install_fake_faster_whisper_utils(monkeypatch, None, failing)

    with pytest.raises(ConnectionError):
        whisper_engine.ensure_model_downloaded("small")


def test_cancelling_during_the_download_returns_control_immediately(monkeypatch, tmp_path):
    # Sans point de controle pendant le telechargement, "Annuler" restait sans
    # effet et l'interface finissait par appeler QThread.terminate(), qui coupe
    # l'ecriture du fichier de poids n'importe ou -- la cause meme du cache
    # incomplet que ce module repare.
    from core.cancellation import CancelToken
    from utils.errors import CancelledError

    cache_dir = tmp_path / "models--Systran--faster-whisper-small"
    monkeypatch.setattr(whisper_engine, "hf_cache_dir_for", lambda name: cache_dir)
    monkeypatch.setattr(whisper_engine, "free_disk_mb", lambda path: 10000.0)

    token = CancelToken()

    def endless_download():
        snapshot = cache_dir / "snapshots" / "abc"
        snapshot.mkdir(parents=True, exist_ok=True)
        for _ in range(200):
            time.sleep(0.05)
        return snapshot

    _install_fake_faster_whisper_utils(monkeypatch, None, endless_download)

    def cancel_soon():
        time.sleep(0.6)
        token.cancel()

    threading.Thread(target=cancel_soon, daemon=True).start()

    started = time.monotonic()
    with pytest.raises(CancelledError):
        whisper_engine.ensure_model_downloaded("small", None, token)

    assert time.monotonic() - started < 4.0, "l'annulation doit rendre la main tout de suite"
    assert cache_dir.exists(), "le cache partiel est conserve : huggingface sait le reprendre"


class TestRepliDossierSimple:
    """Le cache Hugging Face peut rester inutilisable apres un telechargement
    qui n'a pourtant leve aucune erreur. C'est le bug reel : le modele semblait
    telecharge, et le chargement echouait toujours sur un model.bin absent."""

    def test_un_telechargement_sans_erreur_mais_sans_modele_declenche_le_repli(
            self, monkeypatch, tmp_path):
        cache_dir = tmp_path / "models--Systran--faster-whisper-small"
        plain = tmp_path / "simple"
        monkeypatch.setattr(whisper_engine, "hf_cache_dir_for", lambda name: cache_dir)
        monkeypatch.setattr(whisper_engine, "plain_model_dir", lambda name: plain)
        monkeypatch.setattr(whisper_engine, "free_disk_mb", lambda path: 10000.0)
        monkeypatch.setattr(whisper_engine, "_cached_snapshot_path", lambda name: None)

        attempts = {"count": 0}

        def fake_download(model_name, target, on_progress=None, cancel_token=None,
                          output_dir=None):
            attempts["count"] += 1
            if output_dir is not None:      # le repli, lui, produit le fichier
                write_model_bin(output_dir)

        monkeypatch.setattr(whisper_engine, "_download_with_progress", fake_download)

        resolved = whisper_engine.ensure_model_downloaded("small")

        assert resolved == str(plain), "le modele doit etre charge depuis le dossier simple"
        assert attempts["count"] == 2, "cache d'abord, dossier simple ensuite"

    def test_un_dossier_simple_deja_complet_ne_retelecharge_rien(self, monkeypatch, tmp_path):
        plain = tmp_path / "simple"
        write_model_bin(plain)
        monkeypatch.setattr(whisper_engine, "plain_model_dir", lambda name: plain)
        monkeypatch.setattr(whisper_engine, "_cached_snapshot_path", lambda name: None)

        def refuse(*args, **kwargs):
            raise AssertionError("le modele est deja la, rien a telecharger")

        monkeypatch.setattr(whisper_engine, "_download_with_progress", refuse)

        assert whisper_engine.ensure_model_downloaded("small") == str(plain)

    def test_un_cache_sain_reste_prioritaire(self, monkeypatch, tmp_path):
        snapshot = tmp_path / "snapshots" / "abc"
        write_model_bin(snapshot)
        monkeypatch.setattr(whisper_engine, "_cached_snapshot_path", lambda name: str(snapshot))

        def refuse(*args, **kwargs):
            raise AssertionError("rien a telecharger")

        monkeypatch.setattr(whisper_engine, "_download_with_progress", refuse)

        assert whisper_engine.ensure_model_downloaded("small") == "small"

    def test_le_repli_echoue_en_nommant_les_deux_emplacements(self, monkeypatch, tmp_path):
        cache_dir = tmp_path / "models--Systran--faster-whisper-small"
        plain = tmp_path / "simple"
        monkeypatch.setattr(whisper_engine, "hf_cache_dir_for", lambda name: cache_dir)
        monkeypatch.setattr(whisper_engine, "plain_model_dir", lambda name: plain)
        monkeypatch.setattr(whisper_engine, "free_disk_mb", lambda path: 10000.0)
        monkeypatch.setattr(whisper_engine, "_cached_snapshot_path", lambda name: None)
        monkeypatch.setattr(whisper_engine, "_download_with_progress",
                            lambda *args, **kwargs: None)

        with pytest.raises(ModelDownloadError) as excinfo:
            whisper_engine.ensure_model_downloaded("small")

        message = str(excinfo.value)
        assert "Cache :" in message and "Dossier simple :" in message
