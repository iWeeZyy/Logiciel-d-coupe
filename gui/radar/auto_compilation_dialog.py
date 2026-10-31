"""« 🏆 Compilation des plus vus » (Radar, Twitch).

Demande de l'utilisateur : une compilation faite des clips Twitch les PLUS
VUS d'un streamer, sans jamais deux fois le meme moment, avec des
transitions. Tout est enchaine sans autre clic :
1. choix (radar/compilation_pick.py) : les N clips les plus vus de la
   periode, un seul par moment -- la liste est montree AVANT, doublons
   ecartes compris, avec leur raison ;
2. chaque clip est telecharge puis traite par le pipeline habituel (memes
   options que « Analyser le contenu » : format, sous-titres, filigrane…),
   SANS l'intro « Follow » -- elle est posee une seule fois par la
   compilation ; chaque clip devient un projet normal de « Mes projets » ;
3. montage (video/compilation.py) : numeros, transitions, intro, ecran de
   fin, 1 ou 2 parties, legende .txt a cote.

La logique vit dans radar/top_compilation.py, partagee avec la compilation
automatique de la semaine (radar/weekly_top.py) : cette fenetre ne fait que
montrer, demander la verification des droits, et lancer.
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
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core.cancellation import CancelToken
from radar.top_compilation import default_title

_PERIODS = (("cette semaine", 7), ("ce mois-ci", 30), ("depuis 3 mois", 90))


def _fmt(seconds: float) -> str:
    seconds = int(round(seconds or 0))
    return f"{seconds // 60} min {seconds % 60:02d} s" if seconds >= 60 else f"{seconds} s"


def _views(value) -> str:
    return f"{value:,}".replace(",", " ") + " vues" if value is not None else "vues inconnues"



class _Worker(QThread):
    status = Signal(str)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, opportunities, production, out_dir, name, parts, countdown, intro,
                 transitions, token):
        super().__init__()
        self.opportunities = opportunities
        self.production = production
        self.args = (out_dir, name, parts, countdown, intro, transitions)
        self.token = token

    def run(self) -> None:
        from radar.top_compilation import build
        from utils.errors import CancelledError

        out_dir, name, parts, countdown, intro, transitions = self.args
        try:
            written = build(self.opportunities, self.production, out_dir, name=name,
                            parts=parts, countdown=countdown, intro=intro,
                            transitions=transitions, cancel_token=self.token,
                            on_status=self.status.emit)
        except CancelledError:
            self.failed.emit("")
        except Exception as error:  # noqa: BLE001 -- message montre a l'utilisateur
            self.failed.emit(str(error))
        else:
            self.done.emit(written)


class AutoCompilationDialog(QDialog):
    def __init__(self, store, creators: dict, parent=None):
        super().__init__(parent)
        from gui.widgets.production_options import ProductionOptionsBox

        self.setWindowTitle("Compilation des clips les plus vus")
        self.resize(760, 820)
        self.store = store
        self._token = CancelToken()
        self._thread = None
        self._chosen = []

        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        layout = QVBoxLayout(body)
        scroll.setWidget(body)
        outer.addWidget(scroll, stretch=1)

        explain = QLabel(
            "Les clips Twitch les plus vus de la période, un seul par moment (deux clips de la "
            "même scène, coupés par deux spectateurs, ne passent jamais tous les deux). Chaque "
            "clip est traité comme d'habitude, puis la compilation est montée avec des "
            "transitions, un numéro par clip et l'intro au début.")
        explain.setWordWrap(True)
        layout.addWidget(explain)

        row = QHBoxLayout()
        self.creator_combo = QComboBox()
        twitch = [c for c in creators.values() if getattr(c, "platform", "") == "twitch"]
        if len(twitch) > 1:
            self.creator_combo.addItem("Tous les streamers", "")
        for creator in twitch:
            self.creator_combo.addItem(creator.label, creator.key)
        self.period_combo = QComboBox()
        for label, days in _PERIODS:
            self.period_combo.addItem(label, days)
        self.count_spin = QSpinBox()
        self.count_spin.setRange(2, 8)
        self.count_spin.setValue(5)
        self.count_spin.setSuffix(" clips")
        self.creator_combo.currentIndexChanged.connect(self._refresh)
        self.period_combo.currentIndexChanged.connect(self._refresh)
        self.count_spin.valueChanged.connect(self._refresh)
        for widget in (self.creator_combo, self.period_combo, self.count_spin):
            row.addWidget(widget)
        row.addStretch(1)
        layout.addLayout(row)

        self.preview = QLabel("")
        self.preview.setWordWrap(True)
        self.preview.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.preview)

        layout.addWidget(QLabel("Titre (nom du fichier et légende)"))
        self.name_edit = QLineEdit("")
        self._auto_name = ""
        layout.addWidget(self.name_edit)

        options = QHBoxLayout()
        self.parts_combo = QComboBox()
        self.parts_combo.addItem("1 vidéo", 1)
        self.parts_combo.addItem("2 parties (Partie 1 / Partie 2)", 2)
        self.parts_combo.currentIndexChanged.connect(self._refresh)
        options.addWidget(self.parts_combo)
        self.countdown_check = QCheckBox("Compte à rebours (#5 → #1)")
        self.countdown_check.setChecked(True)
        options.addWidget(self.countdown_check)
        self.transitions_check = QCheckBox("Transitions")
        self.transitions_check.setChecked(True)
        self.transitions_check.toggled.connect(self._refresh)
        options.addWidget(self.transitions_check)
        self.intro_check = QCheckBox("Intro « Follow » au début")
        self.intro_check.setChecked(True)
        options.addWidget(self.intro_check)
        options.addStretch(1)
        layout.addLayout(options)

        layout.addWidget(QLabel("Traitement de chaque clip"))
        self.production_options = ProductionOptionsBox(columns=3)
        layout.addWidget(self.production_options)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        outer.addWidget(self.status_label)
        buttons = QHBoxLayout()
        self.create_btn = QPushButton("🏆 Créer la compilation")
        self.create_btn.setProperty("variant", "primary")
        self.create_btn.clicked.connect(self._create)
        buttons.addWidget(self.create_btn)
        buttons.addStretch(1)
        close_btn = QPushButton("Fermer")
        close_btn.clicked.connect(self.reject)
        buttons.addWidget(close_btn)
        outer.addLayout(buttons)
        self._refresh()

    # ------------------------------------------------------------- choix
    def _candidates(self):
        from radar.top_compilation import candidates

        return candidates(self.store, self.creator_combo.currentData() or None,
                          self.period_combo.currentData() or 7)

    def _refresh(self, *_args) -> None:
        from radar.compilation_pick import pick_top
        from video.compilation import MIN_MONETIZABLE_S, TRANSITION_S, Clip, plan

        kept, skipped = pick_top(self._candidates(), self.count_spin.value())
        self._chosen = kept
        if not kept:
            self.preview.setText("Aucun clip pour cette période : lance un scan du Radar Twitch.")
        else:
            lines = [f"{i}. {o.title or o.content_id} — {_views(o.view_count)}, "
                     f"{_fmt(o.duration_s or 0)}" for i, o in enumerate(kept, 1)]
            if len(kept) < self.count_spin.value():
                lines.append(f"<i>Seulement {len(kept)} clip(s) différent(s) sur la période.</i>")
            for dup, original, reason in skipped:
                lines.append(f"<span style='color:#999'>✗ écarté : {dup.title or dup.content_id} "
                             f"({reason} que « {original.title or original.content_id} »)</span>")
            fade = TRANSITION_S if self.transitions_check.isChecked() else 0.0
            try:
                parts = plan([Clip("", float(o.duration_s or 0)) for o in kept],
                             self.parts_combo.currentData() or 1,
                             transitions=bool(fade))
            except Exception:  # noqa: BLE001 -- moins de 2 clips : signale juste au-dessus
                parts = []
            for part in parts:
                head = f"Partie {part.index} : " if part.total > 1 else "Durée estimée : "
                note = "" if part.duration >= MIN_MONETIZABLE_S else "  ⚠ moins d'une minute"
                lines.append(f"<b>{head}{_fmt(part.duration)}</b>{note}")
            self.preview.setText("<br>".join(lines))
        creator = self.creator_combo.currentText() if self.creator_combo.currentData() else ""
        title = default_title(len(kept) or self.count_spin.value(), creator,
                              self.period_combo.currentText())
        if self.name_edit.text() in ("", self._auto_name):
            self.name_edit.setText(title)
        self._auto_name = title

    # ------------------------------------------------------------ export
    def _create(self) -> None:
        if len(self._chosen) < 2:
            QMessageBox.information(self, "Compilation", "Il faut au moins 2 clips différents.")
            return
        from gui.radar.daily_top_dialog import ask_export_folder
        from radar.bridge import RIGHTS_NOTICE
        from radar.top_compilation import safe_name

        if QMessageBox.question(self, "Vérification des droits", RIGHTS_NOTICE + "\n\nContinuer ?") \
                != QMessageBox.StandardButton.Yes:
            return
        folder = ask_export_folder(self, "Dossier de la compilation")
        if not folder:
            return
        options = self.production_options
        # L'intro par clip est coupee par build() : posee une seule fois par
        # la compilation.
        production = {"aspect": options.aspect(), "fill_mode": options.fill_mode(),
                      "fit_mode": options.fit_mode(), "subtitle_style": options.subtitle_style(),
                      "editing_overrides": dict(options.editing_overrides())}
        safe = safe_name(self.name_edit.text())
        self.create_btn.setEnabled(False)
        self._thread = _Worker(list(self._chosen), production, folder, safe,
                               self.parts_combo.currentData() or 1,
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
        from radar.top_compilation import write_captions
        from video.compilation import MIN_MONETIZABLE_S

        self._release_thread()
        self.create_btn.setEnabled(True)
        self.status_label.setText("")
        creator = self.creator_combo.currentText() if self.creator_combo.currentData() else ""
        write_captions(written, self.name_edit.text(), creator=creator)
        lines = []
        for path, duration in written:
            note = "" if duration >= MIN_MONETIZABLE_S else " (moins d'une minute)"
            lines.append(f"{Path(path).name} — {_fmt(duration)}{note}")
        QMessageBox.information(self, "Compilation créée",
                                "\n".join(lines) + "\n\nLégende à côté de chaque vidéo (.txt). "
                                "Les clips traités sont aussi dans « Mes projets ».")

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
