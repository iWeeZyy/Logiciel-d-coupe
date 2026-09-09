"""Fenetre principale : barre laterale (Accueil/Recherche/Projets/Apprentissage/Parametres)
+ zone de contenu empilee (QStackedWidget). Les pages "Analyse en cours" et
"Resultats" ne sont PAS des destinations de la barre laterale -- ce sont des
vues transitoires poussees depuis Accueil, exactement comme le flux decrit
dans le cahier des charges (l'utilisateur ne "navigue" pas vers l'analyse en
cours, il y arrive en cliquant Generer)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from gui import theme
from gui.branding import APP_NAME, APP_TAGLINE
from gui.controller import AppController
from gui.pages.analysis_page import AnalysisPage
from gui.pages.home_page import HomePage
from gui.pages.learning_page import LearningPage
from gui.radar.radar_page import RadarPage
from gui.pages.projects_page import ProjectsPage
from gui.pages.results_page import ResultsPage
from gui.pages.search_page import SearchPage
from gui.pages.settings_page import SettingsPage
from gui.voice_studio.page import VoiceStudioPage

_NAV_ITEMS = [
    ("home", "🎬  Accueil"),
    ("search", "🔎  Recherche"),
    ("radar", "📡  Radar"),
    ("voice", "🎙️  Voice Studio"),
    ("projects", "📁  Projets"),
    ("learning", "🧠  Apprentissage"),
    ("settings", "⚙️  Paramètres"),
]


class MainWindow(QMainWindow):
    def __init__(self, controller: AppController):
        super().__init__()
        self.controller = controller
        self.setWindowTitle(APP_NAME)
        self.resize(1180, 760)
        self.setMinimumSize(940, 620)

        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_sidebar())

        self.stack = QStackedWidget()
        self.stack.setObjectName("ContentArea")
        root.addWidget(self.stack, stretch=1)

        self.setCentralWidget(central)

        self.pages: dict[str, QWidget] = {}
        self._build_pages()

        self.show_page("home")

    def _build_sidebar(self) -> QFrame:
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(theme.SIDEBAR_WIDTH)

        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        brand = QLabel(APP_NAME)
        brand.setObjectName("SidebarBrand")
        layout.addWidget(brand)

        tagline = QLabel(APP_TAGLINE)
        tagline.setObjectName("SidebarTagline")
        tagline.setWordWrap(True)
        layout.addWidget(tagline)

        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        self.nav_buttons: dict[str, QPushButton] = {}

        for key, label in _NAV_ITEMS:
            btn = QPushButton(label)
            btn.setProperty("navItem", True)
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _checked, k=key: self.show_page(k))
            self.nav_group.addButton(btn)
            self.nav_buttons[key] = btn
            layout.addWidget(btn)

        layout.addStretch(1)
        return sidebar

    def _build_pages(self) -> None:
        self.pages["home"] = HomePage(self.controller)
        self.pages["analysis"] = AnalysisPage(self.controller)
        self.pages["results"] = ResultsPage(self.controller)
        self.pages["search"] = SearchPage(self.controller)
        self.pages["radar"] = RadarPage(self.controller)
        self.pages["voice"] = VoiceStudioPage(self.controller)
        self.pages["projects"] = ProjectsPage(self.controller)
        self.pages["learning"] = LearningPage(self.controller)
        self.pages["settings"] = SettingsPage(self.controller)

        for page in self.pages.values():
            self.stack.addWidget(page)

        # Navigation pilotee par le controller (ex: Accueil -> Analyse -> Resultats)
        self.controller.navigate_requested.connect(self.show_page)
        self.controller.analysis_failed.connect(self._show_analysis_error)
        self.controller.analysis_cancelled.connect(self._show_analysis_cancelled)
        self.controller.search_failed.connect(self._show_search_error)

    def _show_analysis_error(self, message: str) -> None:
        QMessageBox.critical(self, "Échec de l'analyse", message)

    def _show_analysis_cancelled(self) -> None:
        QMessageBox.information(self, "Analyse annulée", "L'analyse a été annulée.")

    def _show_search_error(self, message: str) -> None:
        QMessageBox.critical(self, "Échec de la recherche", message)

    def closeEvent(self, event) -> None:
        """Attend/arrete proprement les threads en cours (analyse, recherche,
        miniatures) avant de laisser Qt detruire la fenetre -- sinon un
        QThread encore actif a la destruction fait planter l'appli."""
        for page in self.pages.values():
            if hasattr(page, "cleanup"):
                page.cleanup()
        self.controller.shutdown()
        super().closeEvent(event)

    def show_page(self, key: str) -> None:
        page = self.pages.get(key)
        if page is None:
            return
        if hasattr(page, "on_shown"):
            page.on_shown()
        self.stack.setCurrentWidget(page)
        if key in self.nav_buttons:
            self.nav_buttons[key].setChecked(True)
