"""Fenetre d'analyse d'un ou plusieurs clips (sections 3, 16, 19).

Trois etats, une seule fenetre : preparation (choisir le niveau et le fichier),
analyse en cours (progression et annulation), resultat. Passer d'un etat a
l'autre change le contenu, jamais la fenetre -- ouvrir une seconde fenetre pour
afficher le resultat ferait perdre le fil.

Le travail tourne dans un QThread construit sur le meme modele que ScanThread de
la page Radar, avec le meme CancelToken que le pipeline video. L'interface reste
donc utilisable pendant l'analyse, et le bouton Annuler arrete reellement le
traitement au lieu de se contenter de fermer la fenetre.

L'analyse ne demarre JAMAIS toute seule : ni a l'ouverture de la fenetre, ni a
la selection de plusieurs clips. C'est le bouton "Lancer l'analyse" qui decide.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from core.cancellation import CancelToken
from gui import settings_store
from gui.widgets.production_options import ProductionOptionsBox
from gui.radar.analysis_view import ClipAnalysisView
from radar.analysis import media, runner
from radar.analysis.models import (
    LEVEL_DESCRIPTIONS,
    LEVEL_LABELS,
    LEVEL_STANDARD,
    LEVELS,
    format_timestamp,
)
from utils.errors import CancelledError, ClipFarmingError

def _project_name(opportunity) -> str:
    """Nom de projet lisible, via la regle deja utilisee par le pont."""
    from radar.bridge import _safe_project_name

    return _safe_project_name(getattr(opportunity, "title", "") or opportunity.content_id)


MEDIA_FILTER = ("Vidéos et audio (*.mp4 *.mkv *.mov *.avi *.webm *.wav *.mp3 *.m4a *.flac);;"
                "Tous les fichiers (*)")


class AnalysisThread(QThread):
    """Une ou plusieurs analyses, hors du fil de l'interface.

    Sequentiel et non parallele : la transcription sature deja le processeur, et
    lancer trois modeles Whisper a la fois sur une machine ordinaire les rendrait
    tous les trois plus lents, en risquant de manquer de memoire (section 19).
    """

    progressed = Signal(object)          # ProgressEvent
    item_started = Signal(int, int, str)  # index, total, titre
    item_done = Signal(object)           # ClipAnalysis
    item_failed = Signal(str, str)       # titre, message
    finished_all = Signal()

    def __init__(self, requests, store, cancel_token):
        super().__init__()
        self.requests = list(requests)
        self.store = store
        self.cancel_token = cancel_token

    def run(self) -> None:
        total = len(self.requests)
        for index, request in enumerate(self.requests, start=1):
            if self.cancel_token.is_cancelled:
                break
            title = getattr(request.opportunity, "title", "") or request.opportunity.key
            self.item_started.emit(index, total, title)
            try:
                analysis = runner.run(request, store=self.store,
                                      cancel_token=self.cancel_token,
                                      on_progress=lambda event: self.progressed.emit(event))
            except CancelledError:
                break
            except ClipFarmingError as error:
                self.item_failed.emit(title, str(error))
            except Exception as error:      # noqa: BLE001
                self.item_failed.emit(title, f"Erreur inattendue : {error}")
            else:
                self.item_done.emit(analysis)
        self.finished_all.emit()


class MediaThread(QThread):
    """Recupere le fichier du clip hors du fil de l'interface.

    Le telechargement dure quelques secondes : le faire dans le fil graphique
    figerait la fenetre juste apres un clic, ce qui est le moment ou une
    interface doit justement rester vivante.
    """

    progressed = Signal(object)      # fraction, ou None si la taille est inconnue
    ready = Signal(str)
    failed = Signal(str)

    def __init__(self, opportunity, local_path, cancel_token):
        super().__init__()
        self.opportunity = opportunity
        self.local_path = local_path
        self.cancel_token = cancel_token

    def run(self) -> None:
        try:
            source = media.resolve(self.opportunity, local_path=self.local_path,
                                   on_progress=lambda f: self.progressed.emit(f),
                                   cancel_token=self.cancel_token)
        except CancelledError:
            self.failed.emit("__cancelled__")
        except ClipFarmingError as error:
            self.failed.emit(str(error))
        except Exception as error:      # noqa: BLE001
            self.failed.emit(f"Erreur inattendue : {error}")
        else:
            self.ready.emit(source.path)


class ClipAnalysisDialog(QDialog):
    """Analyse d'un clip, ou d'une selection de clips."""

    analysis_saved = Signal(str)     # content_id, pour rafraichir la carte du Radar

    def __init__(self, opportunities, store, creators=None, parent=None, controller=None):
        super().__init__(parent)
        self.setWindowTitle("Analyse du contenu")
        self.setMinimumSize(600, 560)

        self.opportunities = list(opportunities)
        self.store = store
        self.creators = creators or {}
        self.controller = controller
        self._media_thread: MediaThread | None = None
        # Vrai quand l'analyse doit enchainer sur une production. La
        # transcription est mise en cache sur le chemin du fichier : la
        # production qui suit ne la refait pas.
        self._chain_production = False
        self.media_paths: dict[str, str] = {}
        self.results: list = []
        self.errors: list[tuple[str, str]] = []
        self._thread: AnalysisThread | None = None
        self._cancel_token: CancelToken | None = None

        outer = QVBoxLayout(self)
        outer.setSpacing(12)

        self.header = QLabel()
        self.header.setWordWrap(True)
        self.header.setStyleSheet("font-weight: 700; font-size: 15px;")
        outer.addWidget(self.header)

        self.facts = QLabel()
        self.facts.setWordWrap(True)
        self.facts.setProperty("role", "muted")
        outer.addWidget(self.facts)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        outer.addWidget(self.scroll, 1)

        # Les options vivent dans la FENETRE et non dans le panneau defilant :
        # afficher le resultat remplace ce panneau, ce qui detruisait le
        # composant et faisait planter la production lancee juste apres. Elles
        # restent aussi visibles a cote du resultat, ce qui est plus utile.
        production_title = QLabel("Production du clip")
        production_title.setStyleSheet("font-weight: 700;")
        outer.addWidget(production_title)
        self.production_options = ProductionOptionsBox(columns=3)
        outer.addWidget(self.production_options)

        self.progress_label = QLabel()
        self.progress_label.setWordWrap(True)
        self.progress_label.setVisible(False)
        outer.addWidget(self.progress_label)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setVisible(False)
        outer.addWidget(self.progress)

        buttons = QHBoxLayout()
        # Une action principale qui fait la chaine complete : recuperer le clip,
        # l'ecouter, puis le produire avec les options choisies. C'est le geste
        # attendu depuis le Radar -- separer les deux obligeait a cliquer deux
        # fois pour un enchainement qui n'a jamais de raison d'etre coupe.
        self.full_button = QPushButton("🚀 Analyser et produire")
        self.full_button.setProperty("variant", "primary")
        self.full_button.clicked.connect(self._analyze_and_produce)
        buttons.addWidget(self.full_button)

        self.start_button = QPushButton("▶ Analyser seulement")
        self.start_button.clicked.connect(self._start)
        buttons.addWidget(self.start_button)

        self.reanalyze_button = QPushButton("🔄 Réanalyser")
        self.reanalyze_button.clicked.connect(lambda: self._start(force=True))
        self.reanalyze_button.setVisible(False)
        buttons.addWidget(self.reanalyze_button)

        self.cancel_button = QPushButton("Annuler l'analyse")
        self.cancel_button.clicked.connect(self._cancel)
        self.cancel_button.setVisible(False)
        buttons.addWidget(self.cancel_button)

        buttons.addStretch(1)
        self.close_button = QPushButton("Fermer")
        self.close_button.clicked.connect(self.reject)
        buttons.addWidget(self.close_button)
        outer.addLayout(buttons)

        self._build_preparation()

    # ------------------------------------------------------- preparation
    @property
    def is_batch(self) -> bool:
        return len(self.opportunities) > 1

    def _creator_label(self, opportunity) -> str:
        creator = self.creators.get(getattr(opportunity, "creator_key", ""))
        return getattr(creator, "label", "") if creator else ""

    def _existing_analysis(self, opportunity):
        return self.store.get_analysis(opportunity.key) if self.store is not None else None

    def _build_preparation(self) -> None:
        first = self.opportunities[0]
        if self.is_batch:
            self.header.setText(f"🎬 Analyse de {len(self.opportunities)} clips")
            self.facts.setText("Chaque clip est analysé l'un après l'autre.")
        else:
            self.header.setText("🎬 " + (first.title or first.content_id))
            self.facts.setText(self._facts_line(first))

        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        existing = None if self.is_batch else self._existing_analysis(first)
        if existing is not None:
            done = QLabel("✓ Déjà analysé le "
                          + existing.analyzed_at.replace("T", " à ")[:19])
            done.setStyleSheet("font-weight: 600;")
            layout.addWidget(done)

        layout.addWidget(QLabel("Niveau d'analyse"))
        self.level_combo = QComboBox()
        for level in LEVELS:
            self.level_combo.addItem(LEVEL_LABELS[level], level)
        self.level_combo.setCurrentIndex(LEVELS.index(LEVEL_STANDARD))
        self.level_combo.currentIndexChanged.connect(self._update_level_hint)
        layout.addWidget(self.level_combo)

        self.level_hint = QLabel(LEVEL_DESCRIPTIONS[LEVEL_STANDARD])
        self.level_hint.setWordWrap(True)
        self.level_hint.setProperty("role", "muted")
        layout.addWidget(self.level_hint)

        explanation = QLabel(media.explanation_for(first))
        explanation.setWordWrap(True)
        explanation.setProperty("role", "muted")
        layout.addWidget(explanation)

        self.media_label = QLabel()
        self.media_label.setWordWrap(True)
        layout.addWidget(self.media_label)

        # Le choix manuel reste possible meme quand le telechargement marche :
        # un montage deja monte, une meilleure copie, un contenu dont on a la
        # source. Le libelle dit lequel des deux est le cas courant.
        automatic = all(media.can_download(o) for o in self.opportunities)
        pick = QPushButton("📁 Utiliser un fichier de mon ordinateur…" if automatic
                           else "📁 Choisir le fichier du clip…")
        pick.clicked.connect(self._pick_media)
        layout.addWidget(pick)

        # Fichier deja utilise lors d'une precedente analyse : repropose s'il
        # existe toujours, pour ne pas redemander le meme fichier a chaque fois.
        for opportunity in self.opportunities:
            previous = self._existing_analysis(opportunity)
            path = getattr(previous, "media_path", "") if previous else ""
            if path and Path(path).is_file():
                self.media_paths[opportunity.key] = path
        self._refresh_media_label()

        layout.addStretch(1)
        self.scroll.setWidget(panel)

        if existing is not None:
            self.start_button.setText("👁️ Voir l'analyse")
            self.reanalyze_button.setVisible(True)

    def _facts_line(self, opportunity) -> str:
        parts = []
        label = self._creator_label(opportunity)
        if label:
            parts.append(label)
        if opportunity.duration_s:
            parts.append("⏱️ " + format_timestamp(opportunity.duration_s))
        if opportunity.published_at:
            parts.append("📅 " + opportunity.published_at[:10])
        if opportunity.view_count is not None:
            parts.append(f"👁️ {opportunity.view_count:,}".replace(",", " ") + " vues")
        if opportunity.radar_score is not None:
            parts.append(f"📡 {opportunity.radar_score:.0f}/100")
        return "  •  ".join(parts)

    def _update_level_hint(self) -> None:
        level = self.level_combo.currentData()
        self.level_hint.setText(LEVEL_DESCRIPTIONS.get(level, ""))

    def _refresh_media_label(self) -> None:
        chosen = len(self.media_paths)
        total = len(self.opportunities)
        downloadable = sum(1 for o in self.opportunities if media.can_download(o))
        if chosen == 0:
            self.media_label.setText(
                "Le clip sera téléchargé automatiquement." if downloadable == total
                else "Aucun fichier choisi pour l'instant.")
        elif total == 1:
            self.media_label.setText("Fichier : " + Path(next(iter(self.media_paths.values()))).name)
        else:
            self.media_label.setText(f"{chosen} fichier(s) choisi(s) sur {total}.")

    def _pick_media(self) -> None:
        for opportunity in self.opportunities:
            if opportunity.key in self.media_paths:
                continue
            caption = "Fichier du clip : " + (opportunity.title or opportunity.content_id)
            path, _ = QFileDialog.getOpenFileName(self, caption, "", MEDIA_FILTER)
            if not path:
                break
            self.media_paths[opportunity.key] = path
        self._refresh_media_label()

    # ------------------------------------------------------------ analyse
    def _requests(self, force: bool) -> list:
        level = self.level_combo.currentData()
        model = settings_store.get("default_model") or "small"
        requests = []
        for opportunity in self.opportunities:
            requests.append(runner.AnalysisRequest(
                opportunity=opportunity,
                level=level,
                local_path=self.media_paths.get(opportunity.key),
                model=model,
                creator_label=self._creator_label(opportunity),
                force=force,
            ))
        return requests

    def _start(self, force: bool = False) -> None:
        if self._thread is not None and self._thread.isRunning():
            return

        # Une analyse deja faite s'affiche, elle ne se refait pas. Cette
        # verification passe AVANT celle du media : depuis que l'application
        # telecharge les clips, plus rien ne manquait, et "Voir l'analyse"
        # relancait un telechargement au lieu d'ouvrir le resultat existant.
        if not force and not self.is_batch:
            existing = self._existing_analysis(self.opportunities[0])
            if existing is not None:
                self._show_result(existing)
                return

        # Un clip que l'application sait telecharger n'a pas besoin d'un fichier :
        # exiger le contraire etait la consequence d'une erreur de fait, corrigee
        # (voir radar/clip_download.py).
        missing = [o for o in self.opportunities
                   if o.key not in self.media_paths and not media.can_download(o)]
        if missing:
            QMessageBox.information(
                self, "Fichier manquant",
                "Indiquez d'abord le fichier du clip.\n\n"
                + media.explanation_for(missing[0]))
            return

        self._cancel_token = CancelToken()
        self._thread = AnalysisThread(self._requests(force), self.store, self._cancel_token)
        self._thread.progressed.connect(self._on_progress)
        self._thread.item_started.connect(self._on_item_started)
        self._thread.item_done.connect(self._on_item_done)
        self._thread.item_failed.connect(self._on_item_failed)
        self._thread.finished_all.connect(self._on_finished)

        self.start_button.setVisible(False)
        self.full_button.setVisible(False)
        self.reanalyze_button.setVisible(False)
        self.cancel_button.setVisible(True)
        self.progress.setVisible(True)
        self.progress.setValue(0)
        self.progress_label.setVisible(True)
        self.progress_label.setText("Préparation…")
        self._thread.start()

    def _cancel(self) -> None:
        if self._cancel_token is not None:
            self._cancel_token.cancel()
        self.cancel_button.setEnabled(False)
        self.progress_label.setText("Annulation en cours…")

    def _on_item_started(self, index: int, total: int, title: str) -> None:
        if total > 1:
            self.header.setText(f"🎬 Analyse {index} / {total} — {title}")

    def _on_progress(self, event) -> None:
        fraction = event.step_fraction if event.step_fraction is not None else 0.0
        steps = max(1, event.total_steps)
        overall = ((event.step_index - 1) + fraction) / steps
        self.progress.setValue(int(max(0.0, min(1.0, overall)) * 100))
        detail = f" — {event.sub_label}" if event.sub_label else ""
        self.progress_label.setText(f"{event.label}{detail}")

    def _on_item_done(self, analysis) -> None:
        self.results.append(analysis)
        self.analysis_saved.emit(analysis.content_id)

    def _on_item_failed(self, title: str, message: str) -> None:
        self.errors.append((title, message))

    def _on_finished(self) -> None:
        self.progress.setVisible(False)
        self.progress_label.setVisible(False)
        self.cancel_button.setVisible(False)
        self.cancel_button.setEnabled(True)
        self._thread = None

        if self._cancel_token is not None and self._cancel_token.is_cancelled and not self.results:
            self._chain_production = False
            self.header.setText("Analyse annulée")
            self.start_button.setText("▶ Relancer l'analyse")
            self.start_button.setVisible(True)
            self.full_button.setVisible(True)
            self.full_button.setEnabled(True)
            return

        if self.errors and not self.results:
            self._chain_production = False
            title, message = self.errors[0]
            QMessageBox.warning(self, "Analyse impossible", f"{title}\n\n{message}")
            self.start_button.setVisible(True)
            self.full_button.setVisible(True)
            self.full_button.setEnabled(True)
            return

        if self.results and self._chain_production:
            self._chain_production = False
            self._show_result(self.results[-1])
            produced = self.results[-1]
            if getattr(produced, "media_path", ""):
                self.media_paths[produced.content_id] = produced.media_path
            self._produce()
            return

        if self.results:
            self._show_result(self.results[-1])

        if self.errors:
            details = "\n\n".join(f"{title} : {message}" for title, message in self.errors)
            QMessageBox.warning(self, "Certains clips n'ont pas pu être analysés", details)

    def _show_result(self, analysis) -> None:
        self.header.setText("🎬 " + (analysis.clip_title or analysis.content_id))
        self.facts.setText("")
        self.scroll.setWidget(ClipAnalysisView(analysis))
        self.start_button.setVisible(False)
        # Le bouton principal reste, avec un libelle qui dit ce qu'il reste a
        # faire : l'analyse est la, la production non.
        self.full_button.setText("🚀 Produire le clip")
        self.full_button.setVisible(True)
        self.full_button.setEnabled(True)
        self.reanalyze_button.setVisible(True)
        self.cancel_button.setVisible(False)

    # ---------------------------------------------------------- production
    def _analyze_and_produce(self) -> None:
        """Ecoute le clip puis le produit, en une seule action."""
        if self.is_batch:
            QMessageBox.information(
                self, "Un clip à la fois",
                "Le découpage traite une source à la fois. Sélectionnez un seul clip "
                "pour l'analyser et le produire.")
            return
        self._chain_production = True
        self._start()
        if self._thread is None:
            # Rien n'a demarre (analyse deja faite et affichee, ou media
            # manquant) : on produit directement plutot que d'attendre un fil
            # qui n'existe pas.
            self._chain_production = False
            self._produce()

    def _produce(self) -> None:
        """Lance le decoupage complet de ce clip par le pipeline existant.

        Aucun second pipeline : on prepare les memes arguments que la page
        Accueil et on appelle le meme controleur. La seule difference est qu'il
        faut d'abord disposer du fichier.
        """
        if self.controller is None:
            QMessageBox.information(
                self, "Production indisponible",
                "Cette fenêtre a été ouverte sans accès au moteur de traitement.")
            return
        if self.is_batch:
            QMessageBox.information(
                self, "Un clip à la fois",
                "Le découpage traite une source à la fois : il découpe UNE vidéo en "
                "plusieurs clips. Sélectionnez un seul clip pour le produire.")
            return
        if self._media_thread is not None and self._media_thread.isRunning():
            return

        opportunity = self.opportunities[0]
        local = self.media_paths.get(opportunity.key)
        if not local and not media.can_download(opportunity):
            QMessageBox.information(
                self, "Fichier manquant",
                "Indiquez d'abord le fichier de cette vidéo.\n\n"
                + media.explanation_for(opportunity))
            return

        self._cancel_token = CancelToken()
        self._media_thread = MediaThread(opportunity, local, self._cancel_token)
        self._media_thread.progressed.connect(self._on_media_progress)
        self._media_thread.ready.connect(self._on_media_ready)
        self._media_thread.failed.connect(self._on_media_failed)

        self.full_button.setEnabled(False)
        self.start_button.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setValue(0)
        self.progress_label.setVisible(True)
        self.progress_label.setText("Récupération du clip…")
        self._media_thread.start()

    def _on_media_progress(self, fraction) -> None:
        if fraction is None:
            self.progress.setRange(0, 0)     # indetermine : la taille est inconnue
            return
        self.progress.setRange(0, 100)
        self.progress.setValue(int(max(0.0, min(1.0, fraction)) * 100))

    def _on_media_failed(self, message: str) -> None:
        self.progress.setRange(0, 100)
        self.progress.setVisible(False)
        self.progress_label.setVisible(False)
        self.full_button.setEnabled(True)
        self.start_button.setEnabled(True)
        self._media_thread = None
        if message != "__cancelled__":
            QMessageBox.warning(self, "Production impossible", message)

    def _on_media_ready(self, path: str) -> None:
        from types import SimpleNamespace

        opportunity = self.opportunities[0]
        self.media_paths[opportunity.key] = path
        self._media_thread = None
        self.progress.setRange(0, 100)
        self.progress.setVisible(False)
        self.progress_label.setVisible(False)

        duration = getattr(opportunity, "duration_s", None)
        cli_args = SimpleNamespace(
            input=path,
            # Un clip est deja court : on en tire UNE sortie, pas cinq morceaux
            # de quelques secondes. La duree demandee couvre le clip entier
            # quand elle est connue.
            clip_duration=max(5, int(duration)) if duration else 45,
            nb_clips=1,
            # Le clip est deja decoupe : on le prend EN ENTIER. Sans cela le
            # moteur y cherchait un passage et pouvait rendre un clip ampute de
            # son debut, alors qu'il n'y avait rien a chercher.
            whole_source=True,
            model=settings_store.get("default_model") or "small",
            language=None,
            pre_roll=None,
            post_roll=None,
            min_gap=None,
            subtitle_style=settings_store.get("default_subtitle_style"),
            device=settings_store.get("default_device"),
            no_cache=False,
            debug_scores=False,
            aspect=self.production_options.aspect(),
        )
        name = _project_name(opportunity)
        self.controller.start_analysis(
            cli_args, name=name,
            source_label=opportunity.title or opportunity.content_id,
            source_kind="local", source_url=opportunity.url or None,
            editing_overrides=self.production_options.editing_overrides(),
        )
        self.accept()

    # ------------------------------------------------------------ fermeture
    def reject(self) -> None:
        self.cleanup()
        super().reject()

    def cleanup(self) -> None:
        """Arrete proprement le fil s'il tourne encore.

        Meme nom que sur les pages de l'application : un QThread encore actif a
        la destruction de la fenetre fait planter Qt.
        """
        if self._cancel_token is not None:
            self._cancel_token.cancel()
        for attribute in ("_thread", "_media_thread"):
            thread = getattr(self, attribute, None)
            if thread is not None and thread.isRunning():
                thread.wait(5000)
            setattr(self, attribute, None)
