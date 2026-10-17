"""Fenetre « Créer une compilation » (video/compilation.py).

Liste les clips deja produits (projets les plus recents d'abord) ; l'ordre
de la compilation est l'ordre dans lequel on les coche. Les clips produits
AVEC l'intro « Follow » sont signales : elle reviendrait a chaque clip, la
compilation la pose une seule fois au debut.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from core.cancellation import CancelToken

_MAX_PROJECTS = 40


def _fmt(seconds: float) -> str:
    seconds = int(round(seconds))
    return f"{seconds // 60} min {seconds % 60:02d} s" if seconds >= 60 else f"{seconds} s"


def available_clips(summaries, load_project) -> list[dict]:
    """[{path, label, duration, has_intro}] des clips produits, projets les
    plus recents d'abord. Fonction sans Qt (testable)."""
    clips = []
    for summary in summaries[:_MAX_PROJECTS]:
        try:
            project = load_project(summary.folder)
        except Exception:  # noqa: BLE001 -- un projet abime ne bloque pas la liste
            continue
        has_intro = bool((project.settings_used.get("editing_modules") or {}).get("intro"))
        for clip in project.clips:
            name = clip.get("file_name")
            if not name:
                continue
            path = Path(summary.folder) / name
            if not path.is_file():
                continue
            number = f" — clip {clip.get('index', 0) + 1}" if summary.clip_count > 1 else ""
            clips.append({"path": str(path), "label": f"{summary.name}{number}",
                          "duration": float(clip.get("duration") or 0.0),
                          "has_intro": has_intro})
    return clips


class _Thread(QThread):
    status = Signal(str)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, paths, out_dir, name, parts, countdown, intro, transitions, token):
        super().__init__()
        self.args = (paths, out_dir, name, parts, countdown, intro, transitions)
        self.token = token

    def run(self) -> None:
        from utils.errors import CancelledError
        from video.compilation import compose

        paths, out_dir, name, parts, countdown, intro, transitions = self.args
        try:
            written = compose(paths, out_dir, name=name, parts=parts, countdown=countdown,
                              intro=intro, transitions=transitions,
                              on_progress=self.status.emit, cancel_token=self.token)
        except CancelledError:
            self.failed.emit("")
        except Exception as error:  # noqa: BLE001 -- message montre a l'utilisateur
            self.failed.emit(str(error))
        else:
            self.done.emit(written)


class CompilationDialog(QDialog):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        from projects import store as project_store

        self.setWindowTitle("Créer une compilation")
        self.resize(640, 640)
        self._thread = None
        self._token = CancelToken()
        self._order: list[str] = []

        layout = QVBoxLayout(self)
        intro = QLabel(
            "Coche 3 à 5 clips déjà produits, dans l'ordre de la vidéo. Chaque clip reçoit un "
            "numéro (#5 → #1), l'intro « Follow » est posée une seule fois au début. Produis "
            "les clips avec « Intro ClipsOfStreams » décochée : sinon elle revient à chaque clip.")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.list = QListWidget()
        summaries = controller.list_projects() if controller is not None else []
        self._clips = {c["path"]: c for c in available_clips(summaries, project_store.load_project)}
        for clip in self._clips.values():
            warn = "  ⚠ intro déjà incrustée" if clip["has_intro"] else ""
            item = QListWidgetItem(f"{clip['label']}  ({_fmt(clip['duration'])}){warn}")
            item.setData(Qt.ItemDataRole.UserRole, clip["path"])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            self.list.addItem(item)
        if not self._clips:
            self.list.addItem("Aucun clip produit pour l'instant.")
        self.list.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self.list, stretch=1)

        self.order_label = QLabel("")
        self.order_label.setWordWrap(True)
        layout.addWidget(self.order_label)

        layout.addWidget(QLabel("Titre (nom du fichier et légende)"))
        self.name_edit = QLineEdit("Top moments de la semaine")
        layout.addWidget(self.name_edit)

        row = QHBoxLayout()
        self.parts_combo = QComboBox()
        self.parts_combo.addItem("1 vidéo", 1)
        self.parts_combo.addItem("2 parties (Partie 1 / Partie 2)", 2)
        self.parts_combo.currentIndexChanged.connect(self._refresh_summary)
        row.addWidget(self.parts_combo)
        self.countdown_check = QCheckBox("Compte à rebours (#5 → #1)")
        self.countdown_check.setChecked(True)
        row.addWidget(self.countdown_check)
        self.transitions_check = QCheckBox("Transitions")
        self.transitions_check.setChecked(True)
        self.transitions_check.toggled.connect(self._refresh_summary)
        row.addWidget(self.transitions_check)
        self.intro_check = QCheckBox("Intro « Follow » au début")
        self.intro_check.setChecked(True)
        row.addWidget(self.intro_check)
        row.addStretch(1)
        layout.addLayout(row)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        buttons = QHBoxLayout()
        self.create_btn = QPushButton("🎞 Créer la compilation")
        self.create_btn.setProperty("variant", "primary")
        self.create_btn.clicked.connect(self._create)
        buttons.addWidget(self.create_btn)
        buttons.addStretch(1)
        close_btn = QPushButton("Fermer")
        close_btn.clicked.connect(self.reject)
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)
        self._refresh_summary()

    # ----------------------------------------------------------- selection
    def _on_item_changed(self, item) -> None:
        path = item.data(Qt.ItemDataRole.UserRole)
        if not path:
            return
        if item.checkState() == Qt.CheckState.Checked and path not in self._order:
            self._order.append(path)
        elif item.checkState() != Qt.CheckState.Checked and path in self._order:
            self._order.remove(path)
        self._refresh_summary()

    def _refresh_summary(self) -> None:
        from video.compilation import MIN_MONETIZABLE_S, CompilationError, Clip, plan

        clips = [Clip(p, self._clips[p]["duration"], self._clips[p]["label"]) for p in self._order]
        if not clips:
            self.order_label.setText("Aucun clip coché.")
            return
        lines = [f"{i}. {c.label} ({_fmt(c.duration)})" for i, c in enumerate(clips, 1)]
        try:
            parts = plan(clips, self.parts_combo.currentData() or 1,
                         transitions=self.transitions_check.isChecked())
        except CompilationError as error:
            lines.append(str(error))
        else:
            for part in parts:
                head = f"Partie {part.index} : " if part.total > 1 else "Durée : "
                note = "" if part.duration >= MIN_MONETIZABLE_S else "  ⚠ moins d'une minute"
                lines.append(f"{head}{_fmt(part.duration)}{note}")
        if any(self._clips[p]["has_intro"] for p in self._order):
            lines.append("⚠ Un clip coché contient déjà l'intro : elle apparaîtra au milieu.")
        self.order_label.setText("\n".join(lines))

    # ------------------------------------------------------------- export
    def _create(self) -> None:
        if len(self._order) < 2:
            QMessageBox.information(self, "Compilation", "Coche au moins 2 clips.")
            return
        from gui.radar.daily_top_dialog import ask_export_folder

        folder = ask_export_folder(self, "Dossier de la compilation")
        if not folder:
            return
        name = " ".join(self.name_edit.text().split()) or "Compilation"
        safe = "".join(ch for ch in name if ch not in '<>:"/\\|?*').strip() or "Compilation"
        self.create_btn.setEnabled(False)
        self._thread = _Thread(list(self._order), folder, safe, self.parts_combo.currentData() or 1,
                               self.countdown_check.isChecked(), self.intro_check.isChecked(),
                               self.transitions_check.isChecked(), self._token)
        self._thread.status.connect(self.status_label.setText)
        self._thread.done.connect(self._on_done)
        self._thread.failed.connect(self._on_failed)
        self._thread.start()

    def _release_thread(self) -> None:
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.wait()        # un QThread detruit encore actif arrete l'application

    def _on_done(self, written) -> None:
        from video.compilation import MIN_MONETIZABLE_S, caption_text

        self._release_thread()
        self.create_btn.setEnabled(True)
        self.status_label.setText("")
        title = " ".join(self.name_edit.text().split())
        total = len(written)
        lines = []
        for index, (path, duration) in enumerate(written, 1):
            Path(path).with_suffix(".txt").write_text(caption_text(title, index, total),
                                                     encoding="utf-8")
            note = "" if duration >= MIN_MONETIZABLE_S else " (moins d'une minute)"
            lines.append(f"{Path(path).name} — {_fmt(duration)}{note}")
        QMessageBox.information(self, "Compilation créée",
                                "\n".join(lines) + "\n\nLégende à côté de chaque vidéo (.txt).")

    def _on_failed(self, message: str) -> None:
        self._release_thread()
        self.create_btn.setEnabled(True)
        self.status_label.setText("")
        if message:
            QMessageBox.warning(self, "Compilation impossible", message)

    def reject(self) -> None:
        self._token.cancel()
        self._release_thread()
        super().reject()
