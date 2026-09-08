"""Page Resultats -- grille de clips classes par score, previsualisation
integree, export. Section 6/7/8/10 du cahier des charges."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from export.exporter import update_clip_metadata
from content_factory.report import build_report
from gui.widgets.performance_dialog import PerformanceDialog
from performance.store import PerformanceStore
from performance.tracker import clip_identifier
from gui.controller import AppController
from gui.thumbnails import ThumbnailThread
from gui.widgets.clip_card import RIGHTS_NOTICE, ClipCard
from gui.widgets.metadata_dialog import MetadataDialog
from gui.widgets.thumbnail_picker import ThumbnailPicker
from gui.widgets.video_player import VideoPlayerDialog

_COLUMNS = 2


class ResultsPage(QWidget):
    def __init__(self, controller: AppController):
        super().__init__()
        self.controller = controller
        self._thumb_thread: ThumbnailThread | None = None
        self._cards: dict[str, ClipCard] = {}
        self._cards_by_index: dict[int, ClipCard] = {}
        self._performances: dict = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(40, 32, 40, 24)
        outer.setSpacing(4)

        header_row = QHBoxLayout()
        self.title_label = QLabel("Résultats")
        self.title_label.setProperty("role", "pageTitle")
        header_row.addWidget(self.title_label)
        header_row.addStretch(1)

        self.export_all_btn = QPushButton("Exporter tout")
        self.export_all_btn.setProperty("variant", "primary")
        self.export_all_btn.setToolTip(
            "Copie les clips, leurs miniatures, leurs sous-titres et leurs métadonnées"
        )
        self.export_all_btn.clicked.connect(self._export_all)
        header_row.addWidget(self.export_all_btn)

        self.open_folder_btn = QPushButton("Ouvrir le dossier")
        self.open_folder_btn.clicked.connect(self._open_folder)
        header_row.addWidget(self.open_folder_btn)
        outer.addLayout(header_row)

        self.summary_label = QLabel("")
        self.summary_label.setProperty("role", "subtitle")
        outer.addWidget(self.summary_label)

        self.rights_banner = QLabel(
            "⚠️ Cette vidéo provient de YouTube. Trouver une vidéo ne signifie pas disposer "
            "des droits nécessaires pour republier ou monétiser un extrait."
        )
        self.rights_banner.setWordWrap(True)
        self.rights_banner.setStyleSheet(
            "background: #FBEEDA; color: #7A5010; border-radius: 8px; padding: 10px 14px; font-size: 12.5px;"
        )
        self.rights_banner.setVisible(False)
        outer.addWidget(self.rights_banner)
        outer.addSpacing(12)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll, stretch=1)

        self.grid_container = QWidget()
        self.grid = QGridLayout(self.grid_container)
        self.grid.setSpacing(18)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(self.grid_container)

    def on_shown(self) -> None:
        self._clear_grid()

        results = self.controller.last_results
        # Une seule lecture du magasin par affichage, partagee par les cartes.
        self._performances = PerformanceStore().load_performances()
        name = self.controller.current_project_name or ""
        source_kind = self.controller.last_source_kind

        # Bilan de ce qui a REELLEMENT ete produit (section 8), pas du nombre
        # demande : un module desactive doit se voir ici.
        report = build_report(results, elapsed_s=self.controller.last_elapsed_s)
        headline = "🎉  " + "  •  ".join(report.lines())
        self.summary_label.setText(f"{headline}\n{name}" if name else headline)
        self.summary_label.setWordWrap(True)
        self.rights_banner.setVisible(source_kind == "youtube")

        clip_paths: list[str] = []
        for i, clip in enumerate(results):
            clip_dict = clip.to_dict()
            clip_dict["index"] = clip.index
            clip_path = str(Path(self.controller.current_project_folder or ".") / clip_dict["clip"])
            clip_paths.append(clip_path)

            card = ClipCard(clip_path, clip_dict, source_kind=source_kind)
            card.play_requested.connect(lambda path=clip_path: self._play(path))
            card.metadata_requested.connect(self._edit_metadata)
            card.thumbnails_requested.connect(self._pick_thumbnail)
            card.performance_requested.connect(self._enter_performance)
            self._refresh_performance_button(card, clip)
            self._cards[clip_path] = card
            self._cards_by_index[clip.index] = card
            self.grid.addWidget(card, i // _COLUMNS, i % _COLUMNS)

        if clip_paths:
            self._thumb_thread = ThumbnailThread(clip_paths)
            self._thumb_thread.thumbnail_ready.connect(self._on_thumbnail_ready)
            self._thumb_thread.start()

    def _clear_grid(self) -> None:
        while self.grid.count():
            item = self.grid.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        self._cards.clear()
        self._cards_by_index.clear()

    def cleanup(self) -> None:
        """A la fermeture de l'appli -- voir AppController.shutdown() pour le
        pourquoi (un QThread encore actif a la destruction plante l'appli)."""
        if self._thumb_thread is not None and self._thumb_thread.isRunning():
            self._thumb_thread.wait(2000)
            if self._thumb_thread.isRunning():
                self._thumb_thread.terminate()
                self._thumb_thread.wait()

    def _on_thumbnail_ready(self, clip_path: str, thumb_path: str) -> None:
        card = self._cards.get(clip_path)
        if card:
            card.set_thumbnail(thumb_path)

    # ------------------------------------------------- performances reelles

    def _clip_identifier(self, clip) -> str:
        return clip_identifier(self.controller.current_project_name or "", clip.file_name)

    def _refresh_performance_button(self, card, clip) -> None:
        """Etat du bouton : deja saisi ou non, avec le chiffre principal.

        Une lecture par carte serait une lecture de fichier par clip : le
        magasin est lu une fois par affichage et garde le temps de la page.
        """
        performance = self._performances.get(self._clip_identifier(clip))
        if performance is None:
            card.set_performance_recorded(False)
            return
        summary = f"{performance.views:,} vues".replace(",", " ") if performance.views is not None else "saisies"
        card.set_performance_recorded(True, summary)

    def _enter_performance(self, clip_index: int) -> None:
        clip = next((c for c in self.controller.last_results if c.index == clip_index), None)
        if clip is None:
            return
        clip_id = self._clip_identifier(clip)
        dialog = PerformanceDialog(
            clip_id, Path(clip.file_name).name,
            existing=self._performances.get(clip_id), parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        performance = dialog.result_performance()
        store = PerformanceStore()
        store.save_performance(performance)
        # Relecture plutot que mise a jour locale : une fiche entierement vidée
        # supprime l'entree cote magasin, l'ecran doit refleter ce qui est
        # reellement enregistre.
        self._performances = store.load_performances()
        card = self._cards_by_index.get(clip_index)
        if card is not None:
            self._refresh_performance_button(card, clip)

    def _play(self, clip_path: str) -> None:
        clips = [(Path(p).name, p) for p in self._cards.keys()]
        start_index = list(self._cards.keys()).index(clip_path)
        dialog = VideoPlayerDialog(clips, start_index=start_index, parent=self)
        dialog.exec()

    def _confirm_rights_if_needed(self) -> bool:
        if not self.rights_banner.isVisible():
            return True
        reply = QMessageBox.warning(
            self, "Vérification des droits", RIGHTS_NOTICE,
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
        )
        return reply == QMessageBox.StandardButton.Ok

    def _edit_metadata(self, clip_index: int) -> None:
        card = self._cards_by_index.get(clip_index)
        if card is None:
            return
        dialog = MetadataDialog(clip_index, card.clip_dict.get("metadata") or {}, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        edited = dialog.edited_metadata()
        card.set_metadata(edited)
        # Ecrit dans metadata/clip_XX.json ET results.json : sans ca, rouvrir le
        # projet reafficherait le texte d'origine.
        if self.controller.current_project_folder:
            update_clip_metadata(str(self.controller.current_project_folder), clip_index, edited)
        for clip in self.controller.last_results:
            if clip.index == clip_index:
                clip.metadata = edited

    def _pick_thumbnail(self, clip_index: int) -> None:
        card = self._cards_by_index.get(clip_index)
        if card is None or not self.controller.current_project_folder:
            return
        ThumbnailPicker(
            clip_index, Path(self.controller.current_project_folder),
            card.clip_dict.get("thumbnails") or [], parent=self,
        ).exec()

    def _export_all(self) -> None:
        """Exporte tout ce qui a ete produit pour chaque clip : la video, ses
        miniatures, ses sous-titres et ses metadonnees. Copier les seuls .mp4
        obligerait a retourner farfouiller dans le dossier du projet pour le
        reste."""
        if not self._confirm_rights_if_needed():
            return
        project = self.controller.current_project_folder
        if not project:
            return
        dest_dir = QFileDialog.getExistingDirectory(self, "Exporter tout")
        if not dest_dir:
            return

        import shutil

        destination = Path(dest_dir)
        copied = 0
        for card in self._cards_by_index.values():
            shutil.copy(card.clip_path, destination / Path(card.clip_path).name)
            copied += 1
            for relative in (card.clip_dict.get("thumbnails") or []) + (card.clip_dict.get("subtitles") or []):
                source = Path(project) / relative
                if source.exists():
                    shutil.copy(source, destination / source.name)
                    copied += 1

            metadata = card.clip_dict.get("metadata") or {}
            titles = metadata.get("titles") or []
            if titles or metadata.get("description"):
                lines = [f"[{t.get('kind', '')}] {t.get('text', '')}" for t in titles]
                if metadata.get("description"):
                    lines += ["", metadata["description"]]
                if metadata.get("hashtags"):
                    lines += ["", " ".join(metadata["hashtags"])]
                name = f"{Path(card.clip_path).stem}_textes.txt"
                (destination / name).write_text("\n".join(lines), encoding="utf-8")
                copied += 1

        QMessageBox.information(self, "Export terminé", f"{copied} fichier(s) exportés dans :\n{dest_dir}")

    def _open_folder(self) -> None:
        if self.controller.current_project_folder:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.controller.current_project_folder)))
