"""Bootstrap de l'application graphique -- cree QApplication, charge les
polices/le theme, instancie le controller et la fenetre principale."""
from __future__ import annotations

import os
import sys

# A definir AVANT toute creation de QApplication : le greffon multimedia de Qt
# lit ces variables a son initialisation.
#
# QT_DISABLE_HW_TEXTURES_CONVERSION force la remontee des images decodees par le
# GPU en memoire centrale au lieu de les partager sous forme de texture. Sur une
# partie des pilotes Windows, ce partage de texture echoue silencieusement : le
# son se joue, la position avance, et l'image reste noire -- exactement le
# symptome constate. Le decodage materiel lui-meme reste actif ; seul le chemin
# de remise des images change. Une variable deja definie par l'utilisateur n'est
# jamais ecrasee, pour que le comportement d'origine reste atteignable.
os.environ.setdefault("QT_DISABLE_HW_TEXTURES_CONVERSION", "1")

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from core.migrate_user_data import migrate_legacy_user_data
from core.paths import app_base_dir
from gui import theme
from gui.branding import APP_NAME
from gui.controller import AppController
from gui.main_window import MainWindow

# app_base_dir(), pas Path(__file__) : voir la note dans gui/theme.py.
_ICON_PATH = app_base_dir() / "assets" / "icons" / "clipfarming.ico"


def main(argv: list[str] | None = None) -> int:
    # Avant toute lecture de reglages : les versions precedentes ecrivaient
    # dans Program Files, on recupere ce qui s'y trouve encore.
    migrate_legacy_user_data()

    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)
    if _ICON_PATH.exists():
        app.setWindowIcon(QIcon(str(_ICON_PATH)))

    theme.load_fonts()
    app.setStyleSheet(theme.build_stylesheet())

    controller = AppController()
    window = MainWindow(controller)
    window.show()

    return app.exec()
