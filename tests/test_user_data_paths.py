"""Emplacements des donnees ecrites par l'application, et recuperation de
celles laissees dans le dossier d'installation par les versions precedentes.

Bug reel : projets, caches, reglages et cle API etaient ecrits a cote de
ClipFarming.exe, donc dans Program Files -- un dossier protege en ecriture, que
le desinstalleur vide, et dont les fichiers crees apres l'installation lui sont
inconnus (d'ou un dossier qui survit entier a la desinstallation).

Tests purs : aucun fichier reel de l'utilisateur, tout se passe dans tmp_path.
"""
from pathlib import Path

from core import migrate_user_data, paths


# ------------------------------------------------------- emplacements

def test_in_development_nothing_moves_out_of_the_repository():
    # Le comportement d'avant doit etre conserve a l'identique hors mode fige :
    # c'est ce qui fait que les tests et les scripts continuent de tourner.
    assert not paths.is_frozen()
    assert paths.user_data_dir() == paths.app_base_dir()
    assert paths.default_projects_dir() == paths.app_base_dir() / "user_projects"


def test_frozen_on_windows_writes_under_local_appdata(monkeypatch):
    monkeypatch.setattr(paths, "is_frozen", lambda: True)
    monkeypatch.setattr(paths.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\Test\AppData\Local")

    data_dir = paths.user_data_dir()

    assert data_dir.name == "ClipFarming"
    assert "Local" in str(data_dir)


def test_frozen_projects_land_in_the_documents_folder_windows_really_uses(monkeypatch):
    # Path.home()/"Documents" est faux des que le dossier est redirige
    # (OneDrive, profil deplace) : on demande son chemin a Windows.
    monkeypatch.setattr(paths, "is_frozen", lambda: True)
    monkeypatch.setattr(paths, "_windows_documents_dir",
                        lambda: Path(r"D:\OneDrive\Documents"))

    assert paths.default_projects_dir() == Path(r"D:\OneDrive\Documents") / "ClipFarming"


def test_an_unavailable_documents_folder_never_breaks_startup(monkeypatch):
    monkeypatch.setattr(paths, "is_frozen", lambda: True)
    monkeypatch.setattr(paths, "_windows_documents_dir", lambda: None)

    assert paths.default_projects_dir().name == "ClipFarming"


# ------------------------------------------------------- recuperation

def _legacy_install(root: Path) -> None:
    (root / "user_projects" / "mon-projet").mkdir(parents=True)
    (root / "user_projects" / "mon-projet" / "results.json").write_text("[]", encoding="utf-8")
    (root / ".cache").mkdir()
    (root / ".cache" / "transcript.json").write_text("{}", encoding="utf-8")
    (root / "youtube_api_key.txt").write_text("cle", encoding="utf-8")
    (root / "config").mkdir()
    (root / "config" / "gui_settings.json").write_text("{}", encoding="utf-8")


def _redirect(monkeypatch, legacy: Path, data: Path, projects: Path) -> None:
    monkeypatch.setattr(migrate_user_data, "is_frozen", lambda: True)
    monkeypatch.setattr(migrate_user_data, "app_base_dir", lambda: legacy)
    monkeypatch.setattr(migrate_user_data, "user_data_dir", lambda: data)
    monkeypatch.setattr(migrate_user_data, "default_projects_dir", lambda: projects)


def test_nothing_is_moved_in_development_mode(monkeypatch, tmp_path):
    monkeypatch.setattr(migrate_user_data, "is_frozen", lambda: False)

    assert migrate_user_data.migrate_legacy_user_data() == []


def test_the_work_left_in_program_files_is_recovered(monkeypatch, tmp_path):
    legacy, data, projects = tmp_path / "pf", tmp_path / "appdata", tmp_path / "docs"
    legacy.mkdir()
    _legacy_install(legacy)
    _redirect(monkeypatch, legacy, data, projects)

    moved = migrate_user_data.migrate_legacy_user_data()

    assert len(moved) == 4
    assert (projects / "mon-projet" / "results.json").read_text(encoding="utf-8") == "[]"
    assert (data / ".cache" / "transcript.json").is_file()
    assert (data / "youtube_api_key.txt").read_text(encoding="utf-8") == "cle"
    assert (data / "gui_settings.json").is_file()
    assert not (legacy / "user_projects").exists()


def test_an_existing_destination_is_never_overwritten(monkeypatch, tmp_path):
    # Deuxieme lancement, ou nouvelle installation par-dessus des donnees
    # deja recuperees : l'ancienne copie ne doit jamais ecraser la nouvelle.
    legacy, data, projects = tmp_path / "pf", tmp_path / "appdata", tmp_path / "docs"
    legacy.mkdir()
    _legacy_install(legacy)
    projects.mkdir(parents=True)
    (projects / "recent").mkdir()
    (projects / "recent" / "results.json").write_text("[1]", encoding="utf-8")
    _redirect(monkeypatch, legacy, data, projects)

    migrate_user_data.migrate_legacy_user_data()

    assert (projects / "recent" / "results.json").read_text(encoding="utf-8") == "[1]"
    assert not (projects / "mon-projet").exists()
    assert (legacy / "user_projects" / "mon-projet").exists(), \
        "l'ancienne copie doit rester en place plutot que d'etre perdue"


def test_a_fresh_install_with_nothing_to_recover_is_a_no_op(monkeypatch, tmp_path):
    legacy, data, projects = tmp_path / "pf", tmp_path / "appdata", tmp_path / "docs"
    legacy.mkdir()
    _redirect(monkeypatch, legacy, data, projects)

    assert migrate_user_data.migrate_legacy_user_data() == []


def test_a_locked_source_falls_back_to_a_copy_rather_than_losing_the_data(monkeypatch, tmp_path):
    # Program Files est protege : la suppression de la source peut echouer la
    # ou la copie aurait reussi. L'utilisateur doit garder ses donnees.
    legacy, data, projects = tmp_path / "pf", tmp_path / "appdata", tmp_path / "docs"
    legacy.mkdir()
    _legacy_install(legacy)
    _redirect(monkeypatch, legacy, data, projects)
    monkeypatch.setattr(migrate_user_data.shutil, "move",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("acces refuse")))

    moved = migrate_user_data.migrate_legacy_user_data()

    assert len(moved) == 4
    assert (projects / "mon-projet" / "results.json").is_file()
    assert (legacy / "user_projects" / "mon-projet").exists(), "l'original reste, rien n'est perdu"
