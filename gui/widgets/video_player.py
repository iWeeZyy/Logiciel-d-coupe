"""Lecteur video integre -- l'utilisateur ne doit jamais avoir besoin d'ouvrir
VLC ou un autre logiciel pour voir un clip (section 7 du cahier des charges).
QtMultimedia (QMediaPlayer/QVideoWidget) : deja inclus avec PySide6, aucune
dependance supplementaire, decodage natif sur Windows.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
)


def _format_ms(ms: int) -> str:
    seconds = max(0, ms // 1000)
    m, s = divmod(seconds, 60)
    return f"{m:02d}:{s:02d}"


class VideoPlayerDialog(QDialog):
    """clips : liste de (titre, chemin_mp4). start_index : clip ouvert au depart.
    Fleches Precedent/Suivant pour naviguer sans fermer la fenetre."""

    def __init__(self, clips: list[tuple[str, str]], start_index: int = 0, parent=None):
        super().__init__(parent)
        self.clips = clips
        self.index = start_index
        self.setWindowTitle("Aperçu du clip")
        self.resize(480, 640)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        self.title_label = QLabel("")
        self.title_label.setStyleSheet("font-weight: 700; font-size: 13.5px;")
        layout.addWidget(self.title_label)

        # Le lecteur et le message d'erreur occupent la meme place : une lecture
        # qui echoue doit REMPLACER l'image, pas laisser un rectangle noir qui
        # ne dit rien. C'etait le defaut principal ici -- aucune erreur du
        # lecteur n'etait remontee, quelle qu'elle soit.
        self.video_widget = QVideoWidget()
        self.video_widget.setMinimumHeight(420)

        self.error_label = QLabel("")
        self.error_label.setWordWrap(True)
        self.error_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.error_label.setProperty("role", "muted")

        self.stack = QStackedWidget()
        self.stack.setMinimumHeight(420)
        self.stack.addWidget(self.video_widget)
        self.stack.addWidget(self.error_label)
        layout.addWidget(self.stack, stretch=1)

        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_output)
        self.player.setVideoOutput(self.video_widget)
        self.player.positionChanged.connect(self._on_position_changed)
        self.player.durationChanged.connect(self._on_duration_changed)
        self.player.playbackStateChanged.connect(self._on_playback_state_changed)
        self.player.errorOccurred.connect(self._on_error)

        seek_row = QHBoxLayout()
        self.position_label = QLabel("00:00")
        self.position_label.setProperty("role", "mono")
        seek_row.addWidget(self.position_label)

        self.seek_slider = QSlider(Qt.Orientation.Horizontal)
        self.seek_slider.sliderMoved.connect(self._on_seek)
        seek_row.addWidget(self.seek_slider, stretch=1)

        self.duration_label = QLabel("00:00")
        self.duration_label.setProperty("role", "mono")
        seek_row.addWidget(self.duration_label)
        layout.addLayout(seek_row)

        controls_row = QHBoxLayout()

        self.prev_btn = QPushButton("⏮ Précédent")
        self.prev_btn.clicked.connect(self._play_previous)
        controls_row.addWidget(self.prev_btn)

        self.play_btn = QPushButton("▶ Lecture")
        self.play_btn.setProperty("variant", "primary")
        self.play_btn.clicked.connect(self._toggle_play)
        controls_row.addWidget(self.play_btn)

        self.next_btn = QPushButton("Suivant ⏭")
        self.next_btn.clicked.connect(self._play_next)
        controls_row.addWidget(self.next_btn)

        controls_row.addStretch(1)
        controls_row.addWidget(QLabel("🔊"))
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setFixedWidth(90)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(80)
        self.volume_slider.valueChanged.connect(lambda v: self.audio_output.setVolume(v / 100))
        self.audio_output.setVolume(0.8)
        controls_row.addWidget(self.volume_slider)

        layout.addLayout(controls_row)

        # Toujours propose, pas seulement en cas d'echec : meme lecteur integre
        # qui fonctionne, ouvrir le fichier dans son lecteur habituel reste une
        # demande legitime, et c'est le seul chemin qui ne depende ni de Qt ni
        # du pilote graphique.
        self.system_btn = QPushButton("Ouvrir dans le lecteur système")
        self.system_btn.clicked.connect(self._open_in_system_player)
        layout.addWidget(self.system_btn)

        self._load_current()

    def _open_in_system_player(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(self.clips[self.index][1]))

    def _show_error(self, message: str) -> None:
        self.error_label.setText(
            f"{message}\n\nLe fichier est intact : « Ouvrir dans le lecteur système » "
            "ci-dessous le lit avec le lecteur de Windows."
        )
        self.stack.setCurrentWidget(self.error_label)
        self.play_btn.setEnabled(False)

    def _on_error(self, error, error_string: str = "") -> None:
        if error == QMediaPlayer.Error.NoError:
            return
        self._show_error(
            error_string or self.player.errorString() or "Lecture impossible dans l'aperçu."
        )

    def _load_current(self) -> None:
        title, path = self.clips[self.index]
        self.title_label.setText(title)
        self.prev_btn.setEnabled(self.index > 0)
        self.next_btn.setEnabled(self.index < len(self.clips) - 1)

        # Etat remis a zero a chaque clip : une erreur sur le precedent ne doit
        # pas condamner le suivant.
        self.stack.setCurrentWidget(self.video_widget)
        self.play_btn.setEnabled(True)

        if not Path(path).is_file():
            self._show_error(f"Fichier introuvable :\n{path}")
            return

        self.player.setSource(QUrl.fromLocalFile(path))
        self.player.play()

    def _toggle_play(self) -> None:
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def _on_playback_state_changed(self, state) -> None:
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.play_btn.setText("⏸ Pause" if playing else "▶ Lecture")

    def _on_position_changed(self, position_ms: int) -> None:
        self.position_label.setText(_format_ms(position_ms))
        if not self.seek_slider.isSliderDown():
            self.seek_slider.setValue(position_ms)

    def _on_duration_changed(self, duration_ms: int) -> None:
        self.seek_slider.setRange(0, duration_ms)
        self.duration_label.setText(_format_ms(duration_ms))

    def _on_seek(self, position_ms: int) -> None:
        self.player.setPosition(position_ms)

    def _play_previous(self) -> None:
        if self.index > 0:
            self.index -= 1
            self._load_current()

    def _play_next(self) -> None:
        if self.index < len(self.clips) - 1:
            self.index += 1
            self._load_current()

    def closeEvent(self, event) -> None:
        # Detruire un QMediaPlayer encore rattache a son QVideoWidget pendant que
        # le moteur de decodage tourne fige l'application (elle repasse en "ne
        # repond pas"). On demonte donc dans l'ordre inverse du montage --
        # arret, source videe, sorties detachees -- avant que Qt ne detruise le
        # dialogue et ses enfants.
        self.player.stop()
        self.player.setSource(QUrl())
        self.player.setVideoOutput(None)
        self.player.setAudioOutput(None)
        super().closeEvent(event)
