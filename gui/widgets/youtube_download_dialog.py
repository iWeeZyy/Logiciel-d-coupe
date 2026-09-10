"""Dialogue "Telecharger la video" de la page Recherche.

Deux differences volontaires avec le dialogue d'analyse voisin :

- il ne demande AUCUN reglage de clip (duree, nombre, modele Whisper) : on ne
  decoupe rien ici, on recupere le fichier complet ;
- il demande ou ranger le fichier. Une video entiere pese des centaines de
  megaoctets ; l'ecrire d'office sur le disque systeme sans le dire serait le
  meilleur moyen de le remplir a l'insu de son proprietaire.

Le verrou de consentement est le meme que pour l'analyse, et pour la meme
raison : recuperer un media YouTube autrement que par le bouton officiel de
YouTube va a l'encontre de ses conditions d'utilisation, quelle que soit la
licence affichee sur le contenu.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from gui import settings_store
from youtube.downloader import RIGHTS_WARNING

# 0 = aucun plafond, donc la meilleure definition reellement disponible. Les
# autres valeurs sont des hauteurs, comme le selecteur de format de yt-dlp les
# attend -- pas des libelles decoratifs.
QUALITY_CHOICES = [
    (0, "Meilleure qualité disponible"),
    (1080, "1080p maximum"),
    (720, "720p maximum"),
    (480, "480p maximum"),
]


class YoutubeDownloadDialog(QDialog):
    def __init__(self, video_title: str, duration_seconds=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Télécharger cette vidéo")
        self.setMinimumWidth(520)
        self._destination = Path(settings_store.videos_dir())

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        title = QLabel(video_title)
        title.setWordWrap(True)
        title.setStyleSheet("font-weight: 700; font-size: 13.5px;")
        layout.addWidget(title)

        if duration_seconds:
            minutes = int(duration_seconds) // 60
            hint = QLabel(f"Durée : {minutes} min — le téléchargement peut prendre "
                          "plusieurs minutes selon ta connexion.")
            hint.setProperty("role", "muted")
            hint.setWordWrap(True)
            layout.addWidget(hint)

        layout.addWidget(QLabel("Qualité"))
        self.quality_combo = QComboBox()
        for height, label in QUALITY_CHOICES:
            self.quality_combo.addItem(label, height)
        layout.addWidget(self.quality_combo)

        layout.addWidget(QLabel("Dossier de destination"))
        folder_row = QHBoxLayout()
        self.folder_label = QLabel(str(self._destination))
        self.folder_label.setWordWrap(True)
        folder_row.addWidget(self.folder_label, stretch=1)
        browse_btn = QPushButton("Parcourir…")
        browse_btn.clicked.connect(self._browse)
        folder_row.addWidget(browse_btn)
        layout.addLayout(folder_row)

        self.remember_checkbox = QCheckBox("Utiliser ce dossier par défaut pour les prochaines vidéos")
        self.remember_checkbox.setChecked(True)
        layout.addWidget(self.remember_checkbox)

        self.voice_studio_checkbox = QCheckBox(
            "Ouvrir la vidéo dans Voice Studio à la fin du téléchargement")
        layout.addWidget(self.voice_studio_checkbox)

        notice = QLabel(RIGHTS_WARNING)
        notice.setWordWrap(True)
        notice.setStyleSheet(
            "background: #FBEEDA; color: #7A5010; border-radius: 8px; padding: 10px; font-size: 12px;"
        )
        layout.addWidget(notice)

        self.consent_checkbox = QCheckBox(
            "Je confirme disposer des droits nécessaires pour télécharger et traiter cette vidéo.")
        self.consent_checkbox.toggled.connect(self._update_ok_enabled)
        layout.addWidget(self.consent_checkbox)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Télécharger")
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        self._update_ok_enabled()

    # ----------------------------------------------------------------- etat
    def _update_ok_enabled(self) -> None:
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(
            self.consent_checkbox.isChecked())

    def _browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Dossier de destination", str(self._destination))
        if folder:
            self._destination = Path(folder)
            self.folder_label.setText(folder)

    def _accept(self) -> None:
        if self.remember_checkbox.isChecked():
            settings_store.save({"videos_dir": str(self._destination)})
        self.accept()

    # ------------------------------------------------------------- reponses
    def max_height(self) -> int:
        return int(self.quality_combo.currentData() or 0)

    def destination(self) -> str:
        return str(self._destination)

    def open_in_voice_studio(self) -> bool:
        return self.voice_studio_checkbox.isChecked()
