"""Bloc « Réécriture originale » de Voice Studio.

Un widget a part plutot que trois cents lignes de plus dans page.py : la page
sait deja beaucoup de choses, et ce bloc a sa propre vie (analyse, modele,
variantes, comparaison). Il ne parle a la page que par un signal : « voici le
texte a lire », que la page envoie au TTS EXISTANT.

Sans modele installe, l'analyse s'affiche quand meme et la generation est
desactivee avec la raison : c'est une information utile en soi, et cela evite
un bouton qui echouerait.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.cancellation import CancelToken
from gui import settings_store
from gui.voice_studio.models_dialog import ModelsDialog
from gui.voice_studio.rewrite_workers import RewriteWorker
from voice_studio import llm_models
from voice_studio.rewriting import analysis as analysis_module
from voice_studio.rewriting.models import (
    CONFIDENCE_LABELS,
    DISCLAIMER,
    HOOK_LABELS,
    NARRATION_SPEEDS,
    RewriteRequest,
    STRENGTH_LABELS,
    STYLE_LABELS,
)
from voice_studio.rewriting.providers.llama_cpp_provider import LlamaCppProvider

DURATIONS = [(30, "00:30"), (45, "00:45"), (61, "01:01"), (75, "01:15"),
             (90, "01:30"), (120, "02:00")]

NICHES = ["Rénovation immobilière", "Cuisine", "Finance", "Sport", "Gaming",
          "Technologie", "Automobile", "Histoire", "Business", "Bricolage"]


class RewritePanel(QWidget):
    """Analyse du transcript, réglages, variantes, envoi au TTS."""

    send_to_voice = Signal(str)          # texte choisi

    def __init__(self, parent=None):
        super().__init__(parent)
        self._transcript_text = ""
        self._result = None
        self._worker = None
        self._cancel_token = None
        self._variant_widgets = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        heading = QLabel("✍️  RÉÉCRITURE ORIGINALE")
        heading.setStyleSheet("font-weight: 700; font-size: 13.5px;")
        layout.addWidget(heading)

        intro = QLabel("Transformez votre transcript en un nouveau script avec une "
                       "formulation originale, en conservant les informations importantes.")
        intro.setWordWrap(True)
        intro.setProperty("role", "muted")
        layout.addWidget(intro)

        self.analysis_label = QLabel("")
        self.analysis_label.setWordWrap(True)
        self.analysis_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.analysis_label)

        layout.addLayout(self._build_settings())
        layout.addLayout(self._build_options())
        layout.addLayout(self._build_actions())

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setProperty("role", "muted")
        layout.addWidget(self.status)

        self.variants_box = QVBoxLayout()
        self.variants_box.setSpacing(8)
        layout.addLayout(self.variants_box)

        disclaimer = QLabel(DISCLAIMER)
        disclaimer.setWordWrap(True)
        disclaimer.setProperty("role", "muted")
        layout.addWidget(disclaimer)

        self.refresh_models()

    # ---------------------------------------------------------- reglages
    def _build_settings(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addWidget(QLabel("Niche"))
        self.niche_combo = QComboBox()
        self.niche_combo.setEditable(True)
        self.niche_combo.addItem("Détectée automatiquement", "")
        for niche in NICHES:
            self.niche_combo.addItem(niche, niche)
        row.addWidget(self.niche_combo)

        row.addWidget(QLabel("Durée"))
        self.duration_combo = QComboBox()
        for seconds, label in DURATIONS:
            self.duration_combo.addItem(label, seconds)
        self.duration_combo.setCurrentIndex(2)
        self.duration_combo.setEditable(True)
        row.addWidget(self.duration_combo)

        row.addWidget(QLabel("Débit"))
        self.speed_combo = QComboBox()
        for key, words_per_minute in NARRATION_SPEEDS.items():
            self.speed_combo.addItem(f"{key} ({words_per_minute} mots/min)", key)
        self.speed_combo.setCurrentIndex(1)
        row.addWidget(self.speed_combo)
        row.addStretch(1)
        return row

    def _build_options(self) -> QVBoxLayout:
        box = QVBoxLayout()

        row = QHBoxLayout()
        row.addWidget(QLabel("Style"))
        self.style_combo = QComboBox()
        self.style_combo.addItem("Trois styles différents (A/B/C)", "auto")
        for key, label in STYLE_LABELS.items():
            self.style_combo.addItem(label, key)
        row.addWidget(self.style_combo)

        row.addWidget(QLabel("Intensité"))
        self.strength_combo = QComboBox()
        for key, label in STRENGTH_LABELS.items():
            self.strength_combo.addItem(label, key)
        self.strength_combo.setCurrentIndex(1)
        row.addWidget(self.strength_combo, stretch=1)
        box.addLayout(row)

        hook_row = QHBoxLayout()
        hook_row.addWidget(QLabel("Accroche"))
        self.hook_combo = QComboBox()
        for key, label in HOOK_LABELS.items():
            self.hook_combo.addItem(label, key)
        self.hook_combo.setCurrentIndex(1)
        hook_row.addWidget(self.hook_combo)

        hook_row.addWidget(QLabel("Modèle"))
        self.model_combo = QComboBox()
        hook_row.addWidget(self.model_combo, stretch=1)

        self.manage_btn = QPushButton("⚙️ Gérer les modèles")
        self.manage_btn.clicked.connect(self._manage_models)
        hook_row.addWidget(self.manage_btn)
        box.addLayout(hook_row)

        checks = QHBoxLayout()
        self.checks = {}
        for key, label in (("preserve_hook", "Conserver le hook"),
                           ("preserve_information", "Conserver les informations"),
                           ("preserve_structure", "Conserver la progression"),
                           ("preserve_payoff", "Garder le résultat pour la fin"),
                           ("payoff_reminders", "Rappels du résultat"),
                           ("optimize_retention", "Optimiser la rétention")):
            box_check = QCheckBox(label)
            box_check.setChecked(True)
            self.checks[key] = box_check
            checks.addWidget(box_check)
        checks.addStretch(1)
        box.addLayout(checks)
        return box

    def _build_actions(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self.generate_btn = QPushButton("✨  Générer")
        self.generate_btn.setProperty("variant", "primary")
        self.generate_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.generate_btn.clicked.connect(lambda: self._generate(stricter=False))
        row.addWidget(self.generate_btn)

        self.strict_btn = QPushButton("Régénérer sans ajout")
        self.strict_btn.setVisible(False)
        self.strict_btn.clicked.connect(lambda: self._generate(stricter=True))
        row.addWidget(self.strict_btn)

        self.compare_btn = QPushButton("🔍 Comparer")
        self.compare_btn.setEnabled(False)
        self.compare_btn.clicked.connect(self._compare)
        row.addWidget(self.compare_btn)

        self.cancel_btn = QPushButton("Annuler")
        self.cancel_btn.setVisible(False)
        self.cancel_btn.clicked.connect(self._cancel)
        row.addWidget(self.cancel_btn)
        row.addStretch(1)
        return row

    # ------------------------------------------------------------- etats
    def set_transcript(self, text: str) -> None:
        """Recoit le texte du transcript et affiche ce qu'on en a compris."""
        self._transcript_text = (text or "").strip()
        self.setVisible(bool(self._transcript_text))
        if not self._transcript_text:
            return
        analysis = analysis_module.analyse(self._transcript_text)
        if analysis.niche and not self.niche_combo.currentData():
            self.niche_combo.setItemText(0, f"Détectée : {analysis.niche}")
        pieces = [f"{analysis.word_count} mots"]
        pieces.append(f"Hook : « {analysis.hook[:80]} »" if analysis.hook
                      else "Aucune accroche nette détectée")
        pieces.append(f"Résultat final : « {analysis.payoff[:80]} »" if analysis.payoff
                      else "Aucun résultat final détecté")
        numbers = [p.text for p in analysis.key_points if p.kind == "chiffre"]
        if numbers:
            pieces.append("Chiffres à conserver : " + ", ".join(numbers[:8]))
        self.analysis_label.setText("  •  ".join(pieces))
        self._refresh_state()

    def refresh_models(self) -> None:
        """Ne liste que les modeles reellement installes."""
        previous = self.model_combo.currentData()
        self.model_combo.clear()
        for key in llm_models.installed_keys():
            self.model_combo.addItem(llm_models.describe(key).label, key)
        if previous:
            index = self.model_combo.findData(previous)
            if index >= 0:
                self.model_combo.setCurrentIndex(index)
        elif self.model_combo.count():
            preferred = settings_store.get("rewrite_model")
            index = self.model_combo.findData(preferred) if preferred else -1
            self.model_combo.setCurrentIndex(index if index >= 0 else 0)
        self._refresh_state()

    def _refresh_state(self) -> None:
        has_model = self.model_combo.count() > 0
        has_library = LlamaCppProvider.library_available()
        ready = has_model and has_library and bool(self._transcript_text)
        self.generate_btn.setEnabled(ready)
        self.model_combo.setEnabled(has_model)
        if not has_library:
            self.status.setText(
                "Le moteur de réécriture n'est pas disponible dans cette version de "
                "l'application.")
        elif not has_model:
            self.status.setText(
                "Un modèle local de réécriture est nécessaire pour générer le script.")
        elif not self._transcript_text:
            self.status.setText("Lance d'abord une transcription.")

    # ---------------------------------------------------------- generation
    def _request(self) -> RewriteRequest:
        duration = self.duration_combo.currentData()
        if duration is None:                            # saisie libre "01:30"
            raw = self.duration_combo.currentText().strip()
            try:
                if ":" in raw:
                    minutes, seconds = raw.split(":", 1)
                    duration = int(minutes) * 60 + int(seconds)
                else:
                    duration = int(raw)
            except ValueError:
                duration = 61
        return RewriteRequest(
            source_text=self._transcript_text,
            niche=self.niche_combo.currentData() or self.niche_combo.currentText()
            if self.niche_combo.currentIndex() > 0 else "",
            style=self.style_combo.currentData(),
            strength=self.strength_combo.currentData(),
            target_duration_s=int(duration),
            narration_speed=self.speed_combo.currentData(),
            hook_mode=self.hook_combo.currentData(),
            **{key: box.isChecked() for key, box in self.checks.items()},
        )

    def _generate(self, stricter: bool = False) -> None:
        if self._worker is not None:
            return
        key = self.model_combo.currentData()
        if not key:
            return
        provider = LlamaCppProvider(model_path=str(llm_models.model_path(key)))
        self._cancel_token = CancelToken()
        self._worker = RewriteWorker(self._request(), provider, self._cancel_token,
                                     model_name=key, stricter=stricter,
                                     use_cache=not stricter)
        self._worker.step.connect(self._on_step)
        self._worker.done.connect(self._on_done)
        self._worker.failed.connect(self._on_failed)
        self._worker.cancelled.connect(self._on_cancelled)
        self._worker.finished.connect(self._on_finished)
        self.generate_btn.setEnabled(False)
        self.cancel_btn.setVisible(True)
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self.status.setText("Préparation...")
        settings_store.save({"rewrite_model": key})
        self._worker.start()

    def _cancel(self) -> None:
        if self._cancel_token is not None:
            self._cancel_token.cancel()
        self.status.setText("Annulation en cours...")

    def _on_step(self, index: int, total: int, label: str) -> None:
        self.progress.setRange(0, total)
        self.progress.setValue(index)
        self.status.setText(f"{index}/{total}  {label}")

    def _on_done(self, result) -> None:
        self._result = result
        self._show_variants(result)

    def _on_failed(self, message: str) -> None:
        self.status.setText("")
        QMessageBox.warning(self, "Réécriture impossible", message)

    def _on_cancelled(self) -> None:
        self.status.setText("Réécriture annulée.")

    def _on_finished(self) -> None:
        self._worker = None
        self._cancel_token = None
        self.progress.setVisible(False)
        self.cancel_btn.setVisible(False)
        self._refresh_state()

    # ---------------------------------------------------------- variantes
    def _show_variants(self, result) -> None:
        while self.variants_box.count():
            item = self.variants_box.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._variant_widgets = []

        suspicious = False
        for variant in result.variants:
            frame, editor, flagged = self._variant_card(variant)
            suspicious = suspicious or flagged
            self.variants_box.addWidget(frame)
            self._variant_widgets.append((variant, editor))

        self.compare_btn.setEnabled(bool(result.variants))
        self.strict_btn.setVisible(suspicious)
        self.status.setText(f"{len(result.variants)} version(s) produite(s).")

    def _variant_card(self, variant):
        frame = QFrame()
        frame.setProperty("role", "card")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(6)

        header = QHBoxLayout()
        title = QLabel(variant.label)
        title.setStyleSheet("font-weight: 600;")
        header.addWidget(title, stretch=1)
        header.addWidget(QLabel(CONFIDENCE_LABELS.get(variant.confidence, "")))
        layout.addLayout(header)

        editor = QTextEdit()
        editor.setPlainText(variant.text)
        editor.setMinimumHeight(140)
        layout.addWidget(editor)

        minutes, seconds = divmod(int(variant.estimated_duration_s), 60)
        stats = [f"{variant.word_count} mots",
                 f"durée estimée {minutes:02d}:{seconds:02d}",
                 f"chiffres conservés {variant.preserved_numbers}/{variant.total_numbers}",
                 f"informations conservées {variant.preserved_points}/{variant.total_points}",
                 f"différence de formulation estimée {variant.transformation_index * 100:.0f} %"]
        line = QLabel("  •  ".join(stats))
        line.setProperty("role", "muted")
        line.setWordWrap(True)
        layout.addWidget(line)

        flagged = False
        for warning in variant.warnings:
            flagged = flagged or warning.kind.endswith(("_ajoute", "_ajoutee"))
            text = f"⚠️ {warning.message}"
            if warning.excerpt:
                text += f"\n    « {warning.excerpt} »"
            label = QLabel(text)
            label.setWordWrap(True)
            layout.addWidget(label)

        actions = QHBoxLayout()
        copy = QPushButton("Copier")
        copy.clicked.connect(lambda _c=False, e=editor: self._copy(e.toPlainText()))
        actions.addWidget(copy)

        restore = QPushButton("Restaurer le texte généré")
        restore.clicked.connect(lambda _c=False, e=editor, t=variant.text: e.setPlainText(t))
        actions.addWidget(restore)

        use = QPushButton("🎙️ Utiliser cette version")
        use.setProperty("variant", "primary")
        use.clicked.connect(lambda _c=False, e=editor: self.send_to_voice.emit(e.toPlainText()))
        actions.addWidget(use)
        actions.addStretch(1)
        layout.addLayout(actions)
        return frame, editor, flagged

    def _copy(self, text: str) -> None:
        from PySide6.QtWidgets import QApplication

        if text.strip():
            QApplication.clipboard().setText(text)
            self.status.setText("Copié dans le presse-papiers.")

    def _compare(self) -> None:
        if not self._result or not self._variant_widgets:
            return
        variant, editor = self._variant_widgets[0]
        dialog = QMessageBox(self)
        dialog.setWindowTitle("Comparaison")
        dialog.setText(
            f"Informations conservées : {variant.preserved_points}/{variant.total_points}\n"
            f"Chiffres conservés : {variant.preserved_numbers}/{variant.total_numbers}\n"
            f"Informations potentiellement ajoutées : "
            f"{len([w for w in variant.warnings if w.kind.endswith(('_ajoute', '_ajoutee'))])}\n"
            f"Différence de formulation estimée : "
            f"{variant.transformation_index * 100:.0f} %\n\n" + DISCLAIMER)
        dialog.setDetailedText("TRANSCRIPT SOURCE\n\n" + self._transcript_text
                               + "\n\n\nSCRIPT RÉÉCRIT\n\n" + editor.toPlainText())
        dialog.exec()

    def _manage_models(self) -> None:
        dialog = ModelsDialog(self)
        dialog.exec()
        dialog.cleanup()
        self.refresh_models()

    def cleanup(self) -> None:
        if self._cancel_token is not None:
            self._cancel_token.cancel()
        if self._worker is not None and self._worker.isRunning():
            self._worker.wait(4000)
