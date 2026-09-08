"""Page « Ce que le logiciel apprend » (sections 18, 15, 16, 20, 24).

Aucune logique d'analyse ici : la page lit performance/analyzer.py,
performance/profile.py et performance/learning.py, et affiche. Le moteur doit
pouvoir tourner sans interface -- c'est la contrainte de la section 27.

Trois regles d'affichage tenues dans cette page :
- « corrélation » et jamais « cause » ;
- rien n'est presente tant que les donnees sont insuffisantes, et on le DIT ;
- aucune ponderation n'est modifiee sans un clic explicite, et le retour
  arriere reste possible.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from performance import learning
from performance.analyzer import analyse
from performance.profile import build_profile
from performance.store import PerformanceStore

_CORRELATION_ICONS = {"forte": "🟢", "moyenne": "🟡", "faible": "🟠", "negligeable": "⚪"}


def _card() -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setProperty("role", "card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(20, 18, 20, 18)
    layout.setSpacing(8)
    return frame, layout


def _heading(text: str) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet("font-weight: 700; font-size: 13.5px;")
    return label


class LearningPage(QWidget):
    def __init__(self, controller=None):
        super().__init__()
        self.controller = controller
        self.store = PerformanceStore()
        self._proposal = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(40, 32, 40, 24)
        outer.setSpacing(8)

        title = QLabel("🧠  Ce que le logiciel apprend")
        title.setProperty("role", "pageTitle")
        outer.addWidget(title)

        self.status_label = QLabel("")
        self.status_label.setProperty("role", "subtitle")
        self.status_label.setWordWrap(True)
        outer.addWidget(self.status_label)
        outer.addSpacing(8)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.content = QWidget()
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(14)
        self.content_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(self.content)
        outer.addWidget(scroll, stretch=1)

    # ------------------------------------------------------------ rendu
    def on_shown(self) -> None:
        while self.content_layout.count():
            item = self.content_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        records = self.store.records()
        summary = analyse(records)
        self.status_label.setText(
            f"{summary.sample_size} clip(s) avec des performances saisies  •  "
            f"{summary.level_label}  •  confiance {summary.confidence} %"
        )

        if not summary.usable:
            self._add_insufficient(summary)
            self._add_reset_card()
            return

        self._add_correlations(summary)
        self._add_categories(summary)
        self._add_deviations(summary)
        self._add_profile(records)
        self._add_proposal(records)
        self._add_reset_card()

    def _add_insufficient(self, summary) -> None:
        frame, layout = _card()
        layout.addWidget(_heading("Données insuffisantes"))
        message = QLabel(
            "Publiez vos clips, puis saisissez leurs statistiques depuis la page "
            "Résultats (bouton « Ajouter les performances » sur chaque clip).\n\n"
            "Aucune tendance n'est affichée tant qu'il n'y a pas assez de données : "
            "une corrélation calculée sur quelques clips ne voudrait rien dire."
        )
        message.setWordWrap(True)
        message.setProperty("role", "muted")
        layout.addWidget(message)
        self.content_layout.addWidget(frame)

    def _add_correlations(self, summary) -> None:
        if not summary.correlations:
            return
        frame, layout = _card()
        layout.addWidget(_heading("Facteurs corrélés à vos performances"))
        for item in summary.correlations[:8]:
            row = QHBoxLayout()
            row.addWidget(QLabel(f"{_CORRELATION_ICONS.get(item.label, '⚪')}  {item.factor}"))
            row.addStretch(1)
            value = QLabel(f"corrélation {item.label} ({item.correlation:+.2f})")
            value.setProperty("role", "muted")
            row.addWidget(value)
            layout.addLayout(row)

        caveat = QLabel(
            "Corrélation ne signifie pas cause : ces chiffres décrivent ce qui a été "
            "observé ensemble, pas ce qui produit quoi."
        )
        caveat.setWordWrap(True)
        caveat.setProperty("role", "muted")
        layout.addWidget(caveat)
        self.content_layout.addWidget(frame)

    def _add_categories(self, summary) -> None:
        if not summary.categories:
            return
        frame, layout = _card()
        layout.addWidget(_heading("Moyennes constatées par choix d'édition"))
        for item in summary.categories[:10]:
            row = QHBoxLayout()
            row.addWidget(QLabel(f"{item.factor} : {item.value}"))
            row.addStretch(1)
            value = QLabel(f"{item.average_score:.0f}/100 sur {item.sample_size} clip(s)")
            value.setProperty("role", "muted")
            row.addWidget(value)
            layout.addLayout(row)
        self.content_layout.addWidget(frame)

    def _add_deviations(self, summary) -> None:
        if not summary.deviations:
            return
        frame, layout = _card()
        layout.addWidget(_heading("Potentiel estimé et performance constatée"))
        for item in sorted(summary.deviations, key=lambda d: abs(d.gap), reverse=True)[:8]:
            row = QHBoxLayout()
            row.addWidget(QLabel(item.clip_id.rsplit("/", 1)[-1]))
            row.addStretch(1)
            value = QLabel(f"estimé {item.estimated:.0f}  →  constaté {item.observed:.0f}  ({item.gap:+.0f})")
            value.setProperty("role", "muted")
            row.addWidget(value)
            layout.addLayout(row)
        self.content_layout.addWidget(frame)

    def _add_profile(self, records) -> None:
        profile = build_profile(records)
        lines = profile.lines()
        if not lines:
            return
        frame, layout = _card()
        layout.addWidget(_heading("Mon profil de performance"))
        for line in lines:
            layout.addWidget(QLabel(line))
        influence = QLabel(
            f"Ce profil pèse {profile.influence * 100:.0f} % dans la sélection des prochains "
            "clips. L'analyse générale reste majoritaire."
        )
        influence.setWordWrap(True)
        influence.setProperty("role", "muted")
        layout.addWidget(influence)
        self.content_layout.addWidget(frame)

    def _add_proposal(self, records) -> None:
        profile_data = self.store.load_profile()
        proposal = learning.propose(records, profile_data)
        frame, layout = _card()
        layout.addWidget(_heading("Pondérations des scores"))

        weights = learning.current_weights(profile_data)
        layout.addWidget(QLabel("  •  ".join(
            f"{name.capitalize()} {value * 100:.0f} %" for name, value in weights.items()
        )))

        if proposal is None or learning.is_rejected(profile_data, proposal):
            none_label = QLabel(
                "Aucun ajustement proposé : les données ne montrent pas d'écart assez net."
            )
            none_label.setWordWrap(True)
            none_label.setProperty("role", "muted")
            layout.addWidget(none_label)
        else:
            self._proposal = proposal
            reason = QLabel(proposal.reason)
            reason.setWordWrap(True)
            layout.addWidget(reason)
            layout.addWidget(QLabel("  •  ".join(
                f"{name.capitalize()} {before * 100:.0f} % → {after * 100:.0f} %"
                for name, before, after in proposal.changes()
            )))

            buttons = QHBoxLayout()
            apply_btn = QPushButton("Appliquer les nouveaux paramètres")
            apply_btn.setProperty("variant", "primary")
            apply_btn.clicked.connect(self._apply_proposal)
            buttons.addWidget(apply_btn)

            refuse_btn = QPushButton("Refuser")
            refuse_btn.clicked.connect(self._reject_proposal)
            buttons.addWidget(refuse_btn)
            buttons.addStretch(1)
            layout.addLayout(buttons)

        if learning.can_revert(profile_data):
            revert_btn = QPushButton("Revenir aux paramètres précédents")
            revert_btn.clicked.connect(self._revert)
            layout.addWidget(revert_btn)

        self.content_layout.addWidget(frame)

    def _add_reset_card(self) -> None:
        frame, layout = _card()
        layout.addWidget(_heading("Données d'apprentissage"))
        info = QLabel(
            "Toutes ces données restent sur cette machine et ne sont envoyées nulle part."
        )
        info.setWordWrap(True)
        info.setProperty("role", "muted")
        layout.addWidget(info)

        row = QHBoxLayout()
        reset_btn = QPushButton("Réinitialiser l'apprentissage")
        reset_btn.setProperty("variant", "danger")
        reset_btn.clicked.connect(self._reset_learning)
        row.addWidget(reset_btn)
        row.addStretch(1)
        layout.addLayout(row)
        self.content_layout.addWidget(frame)

    # ------------------------------------------------------------ actions
    def _apply_proposal(self) -> None:
        if self._proposal is None:
            return
        self.store.save_profile(learning.apply_proposal(self.store.load_profile(), self._proposal))
        self._proposal = None
        self.on_shown()

    def _reject_proposal(self) -> None:
        if self._proposal is None:
            return
        self.store.save_profile(learning.reject(self.store.load_profile(), self._proposal))
        self._proposal = None
        self.on_shown()

    def _revert(self) -> None:
        self.store.save_profile(learning.revert(self.store.load_profile()))
        self.on_shown()

    def _reset_learning(self) -> None:
        reply = QMessageBox.warning(
            self, "Réinitialiser l'apprentissage",
            "Cette action supprimera les données utilisées pour personnaliser les scores.\n\n"
            "Vos clips et les performances que vous avez saisies sont conservés : "
            "seules les pondérations apprises et le profil sont remis à zéro.",
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Reset,
            QMessageBox.StandardButton.Cancel,
        )
        if reply == QMessageBox.StandardButton.Reset:
            self.store.reset_learning()
            self.on_shown()
