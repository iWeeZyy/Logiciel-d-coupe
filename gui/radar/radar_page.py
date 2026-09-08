"""Page Radar : YouTube | Twitch | Tous (sections 1 et 20 du Radar Twitch).

Aucune logique metier : la page lit radar/ et affiche. Le scan tourne dans un
QThread pour ne pas figer l'interface, avec la barre de progression et le bouton
d'annulation exiges par la section 22 -- le meme jeton d'annulation que le
pipeline video, pas un second mecanisme.

Une plateforme sans identifiants n'est pas cachee : son onglet reste visible et
explique ce qu'il faut faire. Masquer la fonctionnalite laisserait croire
qu'elle n'existe pas.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core.cancellation import CancelToken
from radar.creators import CreatorAlreadyWatched, CreatorManager
from radar.engine import DEFAULT_PERIOD, PERIODS, RadarEngine
from radar.models import PLATFORM_TWITCH, PLATFORM_YOUTUBE, PRIORITIES, PRIORITY_LABELS
from radar.platforms.twitch import TwitchAdapter
from radar.platforms.youtube import YouTubeAdapter
from radar.store import RadarStore
from utils.errors import CancelledError, ClipFarmingError

_PLATFORM_LABELS = {PLATFORM_YOUTUBE: "YouTube", PLATFORM_TWITCH: "Twitch"}


class ScanThread(QThread):
    """Scan hors du fil de l'interface (section 22)."""

    progressed = Signal(object)
    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(self, engine, platforms, period, cancel_token):
        super().__init__()
        self.engine = engine
        self.platforms = platforms
        self.period = period
        self.cancel_token = cancel_token

    def run(self) -> None:
        try:
            result = self.engine.scan(
                platforms=self.platforms, period=self.period,
                cancel_token=self.cancel_token,
                on_progress=lambda p: self.progressed.emit(p),
            )
        except CancelledError:
            self.failed.emit("__cancelled__")
        except ClipFarmingError as error:
            self.failed.emit(str(error))
        except Exception as error:  # noqa: BLE001
            self.failed.emit(f"Erreur inattendue pendant le scan : {error}")
        else:
            self.finished_ok.emit(result)


def _card() -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setProperty("role", "card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(18, 16, 18, 16)
    layout.setSpacing(8)
    return frame, layout


class RadarPage(QWidget):
    def __init__(self, controller=None):
        super().__init__()
        self.controller = controller
        self.store = RadarStore()
        self.adapters = {PLATFORM_YOUTUBE: YouTubeAdapter(), PLATFORM_TWITCH: TwitchAdapter()}
        self.engine = RadarEngine(self.store, self.adapters)
        self.creators = CreatorManager(self.store)
        self._thread: ScanThread | None = None
        self._cancel_token: CancelToken | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(40, 32, 40, 24)
        outer.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel("📡  Radar")
        title.setProperty("role", "pageTitle")
        header.addWidget(title)
        header.addStretch(1)

        self.period_combo = QComboBox()
        for key in PERIODS:
            self.period_combo.addItem(f"Dernières {key}", key)
        self.period_combo.setCurrentIndex(list(PERIODS).index(DEFAULT_PERIOD))
        header.addWidget(self.period_combo)

        self.scan_btn = QPushButton("🔄  Scanner maintenant")
        self.scan_btn.setProperty("variant", "primary")
        self.scan_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.scan_btn.clicked.connect(self._start_scan)
        header.addWidget(self.scan_btn)

        self.cancel_btn = QPushButton("Annuler")
        self.cancel_btn.setVisible(False)
        self.cancel_btn.clicked.connect(self._cancel_scan)
        header.addWidget(self.cancel_btn)
        outer.addLayout(header)

        self.subtitle = QLabel("Surveille tes créateurs et découvre les contenus "
                               "les plus intéressants du moment.")
        self.subtitle.setProperty("role", "subtitle")
        outer.addWidget(self.subtitle)

        self.dashboard_label = QLabel("")
        self.dashboard_label.setWordWrap(True)
        outer.addWidget(self.dashboard_label)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        outer.addWidget(self.progress)

        self.progress_label = QLabel("")
        self.progress_label.setProperty("role", "muted")
        self.progress_label.setVisible(False)
        outer.addWidget(self.progress_label)

        self.tabs = QTabWidget()
        self.tab_contents: dict[str, QVBoxLayout] = {}
        for key, label in (("all", "Tous"), (PLATFORM_YOUTUBE, "YouTube"),
                           (PLATFORM_TWITCH, "Twitch")):
            self.tabs.addTab(self._build_tab(key), label)
        outer.addWidget(self.tabs, stretch=1)

    def _build_tab(self, key: str) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(12)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.tab_contents[key] = layout
        scroll.setWidget(content)
        return scroll

    # ------------------------------------------------------------ rendu
    def on_shown(self) -> None:
        self._refresh_dashboard()
        for key in self.tab_contents:
            self._refresh_tab(key)

    def _refresh_dashboard(self) -> None:
        data = self.engine.dashboard(period=self.period_combo.currentData())
        self.dashboard_label.setText(
            f"📡  {data['creators_watched']} créateur(s) surveillé(s)  •  "
            f"{data['live_now']} en live  •  {data['opportunities']} contenu(s) détecté(s)  •  "
            f"{data['strong_opportunities']} opportunité(s) forte(s)  •  "
            f"{data['trends']} tendance(s)"
        )

    def _clear(self, layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _refresh_tab(self, key: str) -> None:
        layout = self.tab_contents[key]
        self._clear(layout)

        platforms = (PLATFORM_YOUTUBE, PLATFORM_TWITCH) if key == "all" else (key,)
        for platform in platforms:
            status = self.adapters[platform].status()
            if not status.available:
                layout.addWidget(self._unavailable_card(platform, status))

        if key != "all":
            layout.addWidget(self._creators_card(key))

        opportunities = []
        for platform in platforms:
            opportunities.extend(self.store.list_opportunities(platform=platform, limit=60))
        opportunities.sort(key=lambda o: o.radar_score or 0, reverse=True)

        if not opportunities:
            frame, card_layout = _card()
            card_layout.addWidget(QLabel("Aucun contenu détecté pour l'instant."))
            hint = QLabel("Ajoutez des créateurs, puis lancez un scan. "
                          "Le scan nécessite une connexion internet ; "
                          "les résultats restent ensuite consultables hors ligne.")
            hint.setWordWrap(True)
            hint.setProperty("role", "muted")
            card_layout.addWidget(hint)
            layout.addWidget(frame)
            return

        for opportunity in opportunities[:40]:
            layout.addWidget(self._opportunity_card(opportunity))

    def _unavailable_card(self, platform: str, status) -> QFrame:
        frame, layout = _card()
        layout.addWidget(QLabel(f"⚠️  {_PLATFORM_LABELS[platform]} non configuré"))
        reason = QLabel(status.setup_hint or status.reason)
        reason.setWordWrap(True)
        reason.setProperty("role", "muted")
        layout.addWidget(reason)
        detail = QLabel(status.reason)
        detail.setWordWrap(True)
        detail.setProperty("role", "muted")
        layout.addWidget(detail)
        return frame

    def _creators_card(self, platform: str) -> QFrame:
        frame, layout = _card()
        row = QHBoxLayout()
        row.addWidget(QLabel(f"Créateurs surveillés — {_PLATFORM_LABELS[platform]}"))
        row.addStretch(1)
        add_btn = QPushButton("＋ Ajouter un créateur")
        add_btn.clicked.connect(lambda: self._add_creator(platform))
        row.addWidget(add_btn)
        layout.addLayout(row)

        watched = self.creators.all(platform=platform)
        if not watched:
            empty = QLabel("Aucun créateur surveillé sur cette plateforme.")
            empty.setProperty("role", "muted")
            layout.addWidget(empty)
            return frame

        for creator in watched:
            line = QHBoxLayout()
            state = "🟢 Surveillance active" if creator.active else "⏸️ Surveillance suspendue"
            label = QLabel(f"{PRIORITY_LABELS[creator.priority].split()[0]}  {creator.label}"
                           + (f"  @{creator.username}" if creator.username else ""))
            line.addWidget(label)
            line.addStretch(1)
            state_label = QLabel(state)
            state_label.setProperty("role", "muted")
            line.addWidget(state_label)

            toggle = QPushButton("⏸️" if creator.active else "▶️")
            toggle.setToolTip("Suspendre la surveillance" if creator.active
                              else "Reprendre la surveillance")
            toggle.clicked.connect(
                lambda _=False, k=creator.key, a=creator.active: self._toggle_creator(k, not a))
            line.addWidget(toggle)

            priority_btn = QPushButton("🔥")
            priority_btn.setToolTip("Changer la priorité")
            priority_btn.clicked.connect(lambda _=False, k=creator.key: self._change_priority(k))
            line.addWidget(priority_btn)

            remove = QPushButton("🗑")
            remove.setToolTip("Retirer de la liste (l'historique déjà collecté est conservé)")
            remove.clicked.connect(lambda _=False, k=creator.key: self._remove_creator(k))
            line.addWidget(remove)
            layout.addLayout(line)
        return frame

    def _opportunity_card(self, opportunity) -> QFrame:
        frame, layout = _card()
        creator = self.creators.get(opportunity.creator_key)
        header = QHBoxLayout()
        header.addWidget(QLabel(f"{_PLATFORM_LABELS.get(opportunity.platform, opportunity.platform)}"
                                f"  •  {creator.label if creator else opportunity.creator_key}"))
        header.addStretch(1)
        if opportunity.radar_score is not None:
            score = QLabel(f"📡 {opportunity.radar_score:.0f}/100")
            score.setStyleSheet("font-weight: 700;")
            header.addWidget(score)
        layout.addLayout(header)

        title = QLabel(opportunity.title or opportunity.content_id)
        title.setWordWrap(True)
        title.setStyleSheet("font-weight: 600;")
        layout.addWidget(title)

        stats = []
        if opportunity.view_count is not None:
            stats.append(f"👁️ {opportunity.view_count:,}".replace(",", " ") + " vues")
        if opportunity.viewer_count is not None:
            stats.append(f"👥 {opportunity.viewer_count:,}".replace(",", " ") + " spectateurs")
        if opportunity.like_count is not None:
            stats.append(f"❤️ {opportunity.like_count:,}".replace(",", " "))
        if opportunity.category:
            stats.append(f"🎮 {opportunity.category}")
        if stats:
            line = QLabel("  •  ".join(stats))
            line.setProperty("role", "muted")
            layout.addWidget(line)

        trend = opportunity.trend or {}
        if trend.get("level") in ("forte", "moderee"):
            trend_label = QLabel(f"🚀 {trend.get('growth_percent', 0):+.0f} %  "
                                 f"(confiance {trend.get('confidence', 0)} %)")
            trend_label.setStyleSheet("font-weight: 600;")
            layout.addWidget(trend_label)

        breakdown = opportunity.score_breakdown or {}
        if breakdown.get("missing"):
            missing = QLabel("Non mesuré : " + ", ".join(breakdown["missing"]))
            missing.setProperty("role", "muted")
            layout.addWidget(missing)

        actions = QHBoxLayout()
        open_btn = QPushButton("▶ Ouvrir")
        open_btn.clicked.connect(lambda: self._open(opportunity))
        actions.addWidget(open_btn)

        favorite = self.store.is_favorite(opportunity.key)
        fav_btn = QPushButton("⭐ Favori" if not favorite else "★ Retirer des favoris")
        fav_btn.clicked.connect(lambda: self._toggle_favorite(opportunity))
        actions.addWidget(fav_btn)

        send_btn = QPushButton("🏭 Envoyer au Content Factory")
        send_btn.clicked.connect(lambda: self._send_to_factory(opportunity))
        actions.addWidget(send_btn)
        actions.addStretch(1)
        layout.addLayout(actions)
        return frame

    # ---------------------------------------------------------- actions
    def _add_creator(self, platform: str) -> None:
        query, accepted = QInputDialog.getText(
            self, f"Ajouter un créateur {_PLATFORM_LABELS[platform]}",
            "Nom de la chaîne, @handle ou URL :")
        if not accepted or not query.strip():
            return
        try:
            candidate = self.creators.resolve(self.adapters[platform], query.strip())
        except ClipFarmingError as error:
            QMessageBox.warning(self, "Recherche impossible", str(error))
            return
        if candidate is None:
            QMessageBox.information(self, "Introuvable",
                                    "Aucune chaîne ne correspond à cette recherche.")
            return
        if candidate.already_watched:
            QMessageBox.information(self, "Déjà surveillé",
                                    f"⚠️ Ce créateur est déjà surveillé : {candidate.creator.label}")
            return
        # Confirmation explicite : jamais d'ajout automatique (sections 3 et 17).
        reply = QMessageBox.question(
            self, "Chaîne trouvée",
            f"🎬 {candidate.summary()}\n\nAjouter à la surveillance ?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes)
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            self.creators.add(candidate.creator)
        except CreatorAlreadyWatched as error:
            QMessageBox.information(self, "Déjà surveillé", str(error))
        self.on_shown()

    def _toggle_creator(self, key: str, active: bool) -> None:
        self.creators.set_active(key, active)
        self.on_shown()

    def _change_priority(self, key: str) -> None:
        labels = [PRIORITY_LABELS[p] for p in PRIORITIES]
        choice, accepted = QInputDialog.getItem(self, "Priorité", "Priorité du créateur :",
                                                labels, 0, False)
        if accepted:
            self.creators.set_priority(key, PRIORITIES[labels.index(choice)])
            self.on_shown()

    def _remove_creator(self, key: str) -> None:
        reply = QMessageBox.question(
            self, "Retirer de la surveillance",
            "Retirer ce créateur de la liste ?\n\n"
            "Les contenus déjà détectés, les relevés et les favoris sont conservés.",
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Cancel)
        if reply == QMessageBox.StandardButton.Yes:
            self.creators.remove(key)
            self.on_shown()

    def _open(self, opportunity) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        if opportunity.url:
            QDesktopServices.openUrl(QUrl(opportunity.url))

    def _toggle_favorite(self, opportunity) -> None:
        if self.store.is_favorite(opportunity.key):
            self.store.remove_favorite(opportunity.key)
        else:
            self.store.add_favorite(opportunity)
        self.on_shown()

    def _send_to_factory(self, opportunity) -> None:
        from radar.bridge import RIGHTS_NOTICE, SourceNotAvailable, build_request
        try:
            request = build_request([opportunity])
        except SourceNotAvailable as error:
            QMessageBox.information(self, "Source non disponible", str(error))
            return
        QMessageBox.information(
            self, "Vérification des droits",
            RIGHTS_NOTICE + f"\n\nProjet à créer : {request.project_name}")

    # ------------------------------------------------------------- scan
    def _start_scan(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            return
        available = [name for name, adapter in self.adapters.items()
                     if adapter.status().available]
        if not available:
            QMessageBox.information(
                self, "Aucune plateforme configurée",
                "Ni YouTube ni Twitch ne sont configurés. Le Radar a besoin d'au moins "
                "une clé d'API officielle pour récupérer des métadonnées.")
            return

        self._cancel_token = CancelToken()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setVisible(True)
        self.progress_label.setVisible(True)
        self.scan_btn.setEnabled(False)
        self.cancel_btn.setVisible(True)

        self._thread = ScanThread(self.engine, available,
                                  self.period_combo.currentData(), self._cancel_token)
        self._thread.progressed.connect(self._on_progress)
        self._thread.finished_ok.connect(self._on_finished)
        self._thread.failed.connect(self._on_failed)
        self._thread.start()

    def _cancel_scan(self) -> None:
        if self._cancel_token is not None:
            self._cancel_token.cancel()
        self.cancel_btn.setEnabled(False)

    def _on_progress(self, progress) -> None:
        self.progress.setValue(int(progress.fraction * 100))
        self.progress_label.setText(progress.label())

    def _reset_scan_ui(self) -> None:
        self.progress.setVisible(False)
        self.progress_label.setVisible(False)
        self.scan_btn.setEnabled(True)
        self.cancel_btn.setVisible(False)
        self.cancel_btn.setEnabled(True)

    def _on_finished(self, result) -> None:
        self._reset_scan_ui()
        self.on_shown()
        if result.errors:
            QMessageBox.warning(
                self, "Scan terminé avec des avertissements",
                f"{result.opportunities_found} contenu(s) détecté(s) sur "
                f"{result.creators_scanned} créateur(s).\n\n" + "\n".join(result.errors[:5]))

    def _on_failed(self, message: str) -> None:
        self._reset_scan_ui()
        if message == "__cancelled__":
            self.on_shown()
            return
        QMessageBox.warning(self, "Scan interrompu", message)

    def cleanup(self) -> None:
        """Appelee par MainWindow.closeEvent -- meme nom que les autres pages,
        pas une seconde convention. Un QThread encore actif a la destruction
        fait planter l'application."""
        if self._thread is not None and self._thread.isRunning():
            if self._cancel_token is not None:
                self._cancel_token.cancel()
            self._thread.wait(3000)
