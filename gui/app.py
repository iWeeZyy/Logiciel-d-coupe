"""Bootstrap de l'application graphique -- cree QApplication, charge les
polices/le theme, instancie le controller et la fenetre principale."""
from __future__ import annotations

import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from core.paths import app_base_dir
from gui import theme
from gui.branding import APP_NAME
from gui.controller import AppController
from gui.main_window import MainWindow

# app_base_dir(), pas Path(__file__) : voir la note dans gui/theme.py.
_ICON_PATH = app_base_dir() / "assets" / "icons" / "clipfarming.ico"


def main(argv: list[str] | None = None) -> int:
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
