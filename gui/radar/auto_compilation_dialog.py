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
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

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

_PERIODS = (("cette semaine", 7), ("ce mois-ci", 30), ("depuis 3 mois", 90))


def _fmt(seconds: float) -> str:
    seconds = int(round(seconds or 0))
    return f"{seconds // 60} min {seconds % 60:02d} s" if seconds >= 60 else f"{seconds} s"


def _views(value) -> str:
    return f"{value:,}".replace(",", " ") + " vues" if value is not None else "vues inconnues"


def default_title(count: int, creator_label: str, period_label: str) -> str:
    who = f" de {creator_label}" if creator_label else ""
    return f"Top {count} des moments{who} {period_label}"


def _produce_clip(opportunity, production: dict, cancel_token, on_status=lambda m: None) -> str:
    """Telecharge puis traite UN clip par le pipeline habituel ; renvoie le
    fichier produit. Meme preparation que la fenetre d'analyse du Radar."""
    import pipeline
    from core.config_loader import load_settings
    from gui import settings_store
    from gui.controller import apply_editing_overrides
    from gui.radar.analysis_dialog import _project_name
    from projects import store as project_store
    from radar.analysis import media

    title = opportunity.title or opportunity.content_id
    on_status(f"Téléchargement : {title}")
    source = media.resolve(opportunity, cancel_token=cancel_token)
    source_path = source.path
    fit_mode = production.get("fit_mode")
    from video.filter_graph import FIT_CROP, FIT_SPLIT_WEBCAM

    if fit_mode == FIT_SPLIT_WEBCAM:
        # Exactement comme un clip seul (gui/controller.py, AnalysisThread) :
        # « Webcam en haut + Gameplay en bas » = la version PORTRAIT que
        # Twitch genere lui-meme, puis le cadrage classique dessus -- notre
        # propre decoupage en deux bandes cadrait la webcam trop large.
        # Sans cette version pour ce clip, cadrage classique sur le paysage.
        on_status(f"Version portrait Twitch : {title}")
        source_path = portrait_variant(opportunity, cancel_token) or source_path
        fit_mode = FIT_CROP
    name = _project_name(opportunity)
    folder = project_store.create_project_folder(name, projects_dir=settings_store.projects_dir())
    duration = opportunity.duration_s
    cli_args = SimpleNamespace(
        input=source_path, output=str(folder), overwrite=True,
        clip_duration=max(5, int(duration)) if duration else 45, nb_clips=1, whole_source=True,
        model=settings_store.get("default_model") or "small", language=None,
        pre_roll=None, post_roll=None, min_gap=None,
        subtitle_style=production.get("subtitle_style") or settings_store.get("default_subtitle_style"),
        device=settings_store.get("default_device"), no_cache=False, debug_scores=False,
        aspect=production.get("aspect"), fill_mode=production.get("fill_mode"),
        fit_mode=fit_mode,
    )
    settings = load_settings(cli_args)
    apply_editing_overrides(settings, production.get("editing_overrides") or {})
    project_store.write_manifest(
        folder, name=name, source_label=title, source_kind="local",
        settings_used={
            "clip_duration": settings.clip_duration, "nb_clips": settings.nb_clips,
            "model": settings.model, "language": settings.language,
            "subtitle_style": settings.subtitle_style, "device": settings.device,
            "editing_modules": {key: bool(block.get("enabled", False))
                                for key, block in settings.editing.items()
                                if isinstance(block, dict) and "enabled" in block},
        },
        source_url=opportunity.url or None)
    on_status(f"Montage du clip : {title}")
    results = pipeline.run(settings, cancel_token=cancel_token)
    if not results:
        raise RuntimeError(f"Aucun clip produit pour « {title} ».")
    return str(Path(folder) / results[0].file_name)


def portrait_variant(opportunity, cancel_token) -> str | None:
    """La version portrait (webcam en haut, jeu en bas) que Twitch genere pour
    ce clip, ou None s'il n'en a pas. Rangee dans un sous-dossier : le rendu
    paysage du meme clip porte le meme nom de fichier, et yt-dlp le
    prendrait pour la version deja telechargee."""
    from core.logging_setup import get_logger
    from radar.analysis.media import clips_dir
    from radar.clip_download import download_clip
    from utils.errors import CancelledError
    from video.ffmpeg_utils import video_resolution

    try:
        path = download_clip(opportunity.url or opportunity.content_id,
                             str(clips_dir() / "portrait"), cancel_token=cancel_token,
                             prefer_portrait=True)
        width, height = video_resolution(path)
    except CancelledError:
        raise
    except Exception as error:  # noqa: BLE001 -- repli : cadrage classique sur le paysage
        get_logger().warning(f"Version portrait indisponible ({error}).")
        return None
    return path if height > width else None


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
        from utils.errors import CancelledError
        from video.compilation import compose

        out_dir, name, parts, countdown, intro, transitions = self.args
        try:
            paths = []
            total = len(self.opportunities)
            for index, opportunity in enumerate(self.opportunities, 1):
                paths.append(_produce_clip(
                    opportunity, self.production, self.token,
                    lambda message, i=index: self.status.emit(f"Clip {i}/{total} — {message}")))
            written = compose(paths, out_dir, name=name, parts=parts, countdown=countdown,
                              intro=intro, transitions=transitions,
                              on_progress=self.status.emit, cancel_token=self.token)
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
        days = self.period_combo.currentData() or 7
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
        since = since.replace("+00:00", "Z")
        from radar.display import ORDER_VIEWS

        return self.store.list_opportunities(platform="twitch",
                                             creator_key=self.creator_combo.currentData() or None,
                                             since=since, kinds=("clip",), order=ORDER_VIEWS,
                                             limit=300)

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

        if QMessageBox.question(self, "Vérification des droits", RIGHTS_NOTICE + "\n\nContinuer ?") \
                != QMessageBox.StandardButton.Yes:
            return
        folder = ask_export_folder(self, "Dossier de la compilation")
        if not folder:
            return
        options = self.production_options
        overrides = dict(options.editing_overrides())
        overrides["intro"] = False          # posee une seule fois par la compilation
        production = {"aspect": options.aspect(), "fill_mode": options.fill_mode(),
                      "fit_mode": options.fit_mode(), "subtitle_style": options.subtitle_style(),
                      "editing_overrides": overrides}
        name = " ".join(self.name_edit.text().split()) or "Compilation"
        safe = "".join(ch for ch in name if ch not in '<>:"/\\|?*').strip() or "Compilation"
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
        from video.compilation import MIN_MONETIZABLE_S, caption_text

        self._release_thread()
        self.create_btn.setEnabled(True)
        self.status_label.setText("")
        title = " ".join(self.name_edit.text().split())
        lines = []
        for index, (path, duration) in enumerate(written, 1):
            Path(path).with_suffix(".txt").write_text(caption_text(title, index, len(written)),
                                                     encoding="utf-8")
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
