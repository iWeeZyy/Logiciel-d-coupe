"""Script d'installation Windows.

Il n'est compile que sur Windows (par la CI), donc rien ici ne l'execute : ce
sont des garde-fous sur son contenu, sur les deux points ou une erreur se paie
cher -- un dossier d'installation qui survit a la desinstallation, et un
desinstalleur qui emporterait le travail de l'utilisateur.
"""
from pathlib import Path

import pytest

ISS = Path(__file__).resolve().parent.parent / "build" / "installer.iss"


@pytest.fixture(scope="module")
def script():
    return ISS.read_text(encoding="utf-8")


def test_the_whole_installation_folder_is_removed(script):
    # Nommer les restes un par un ne marche pas : les caches Python et numba,
    # les journaux et les reglages reecrits apparaissent APRES l'installation,
    # donc le desinstalleur ne les connait pas et le dossier reste debout.
    section = script[script.index("[UninstallDelete]"):]

    assert 'Type: filesandordirs; Name: "{app}"' in section


def test_the_uninstaller_never_touches_the_user_s_work(script):
    # Projets, reglages et caches vivent sous Documents et %LOCALAPPDATA%.
    # Desinstaller un logiciel ne doit pas emporter ce qu'on a fait avec.
    for protected in ("{userdocs}", "{localappdata}", "{userappdata}", "{commondocs}"):
        assert protected not in script


def test_the_installer_still_installs_everything_pyinstaller_produced(script):
    assert 'Source: "..\\dist\\ClipFarming\\*"' in script
    assert "recursesubdirs" in script
