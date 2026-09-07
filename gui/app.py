"""Bootstrap de l'application graphique -- cree QApplication, charge les
polices/le theme, instancie le controller et la fenetre principale."""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from gui import theme
from gui.branding import APP_NAME
from gui.controller import AppController
from gui.main_window import MainWindow

_ICON_PATH = Path(__file__).resolve().parent.parent / "assets" / "icons" / "clipfarming.ico"


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
