"""AppController -- LE seul pont entre la GUI et le moteur (pipeline.py,
youtube/, projects/). Aucune page ne doit importer pipeline/youtube/projects
directement : tout passe par ici, en signaux Qt, jamais en appel bloquant
depuis le thread GUI.
"""
from __future__ import annotations

import time

import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

from PySide6.QtCore import QObject, QThread, QTimer, Signal

import pipeline
from core.cancellation import CancelToken
from core.config_loader import Settings, load_settings, load_youtube_config
from core.models import ClipResult, ProgressEvent
from gui import settings_store
from projects import store as project_store
from projects.models import Project, ProjectSummary
from utils.errors import CancelledError, ClipFarmingError

_CANCELLED_SENTINEL = "__cancelled__"
_FORCE_CANCEL_DELAY_MS = 4000


class AnalysisThread(QThread):
    progress = Signal(object)       # ProgressEvent
    finished_ok = Signal(list)      # list[ClipResult]
    failed = Signal(str)

    def __init__(self, settings: Settings, cancel_token: CancelToken,
                 youtube_source: Optional[str] = None, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.cancel_token = cancel_token
        self.youtube_source = youtube_source

    def run(self) -> None:
        youtube_tmp_dir = None
        try:
            if self.youtube_source:
                self.progress.emit(ProgressEvent(
                    step_index=0, total_steps=5, label="Telechargement YouTube",
                    sub_label=None, elapsed_s=0.0,
                ))
                from youtube.downloader import download_video

                youtube_tmp_dir = tempfile.mkdtemp(prefix="clip_farming_youtube_")
                # Le consentement (droits) est deja verrouille au niveau de la page
                # Recherche (case a cocher obligatoire avant que "Analyser" ne soit
                # cliquable) -- ce thread ne fait qu'executer une decision deja prise.
                self.settings.input = download_video(
                    self.youtube_source, youtube_tmp_dir, consent_confirmed=True,
                )

            results = pipeline.run(
                self.settings, on_progress=self.progress.emit, cancel_token=self.cancel_token,
            )
            self.finished_ok.emit(results)
        except CancelledError:
            self.failed.emit(_CANCELLED_SENTINEL)
        except ClipFarmingError as e:
            self.failed.emit(str(e))
        except Exception as e:  # noqa: BLE001 -- jamais laisser le thread mourir silencieusement
            self.failed.emit(f"Erreur inattendue : {e}")
        finally:
            if youtube_tmp_dir:
                shutil.rmtree(youtube_tmp_dir, ignore_errors=True)


class SearchThread(QThread):
    finished_ok = Signal(list)   # list[RankedVideo]
    failed = Signal(str)

    def __init__(self, query: str, filters, max_results: int, sort_by: str, parent=None):
        super().__init__(parent)
        self.query = query
        self.filters = filters
        self.max_results = max_results
        self.sort_by = sort_by

    def run(self) -> None:
        try:
            from youtube.quota import QuotaTracker
            from youtube.ranking import rank_videos
            from youtube.search import search_videos

            yt_config = load_youtube_config()
            quota = QuotaTracker(daily_limit=yt_config.get("daily_quota_limit", 10000))
            videos = search_videos(self.query, self.max_results, self.filters, quota)
            ranked = rank_videos(
                videos, self.query, clip_duration=45, min_gap=20,
                weights=yt_config["weights"], params=yt_config["params"], sort_by=self.sort_by,
            )
            self.finished_ok.emit(ranked)
        except ClipFarmingError as e:
            self.failed.emit(str(e))
        except Exception as e:  # noqa: BLE001
            self.failed.emit(f"Erreur inattendue : {e}")


class VideoDownloadThread(QThread):
    """Telechargement d'une video complete depuis la page Recherche.

    Un fil a part, et non le fil de l'analyse : il n'y a ici aucun pipeline,
    aucun modele a charger, rien a annuler d'autre que le transfert lui-meme.
    Le jeton d'annulation est le meme que partout ailleurs
    (core/cancellation.py), verifie par le crochet de progression de yt-dlp.
    """

    progress = Signal(object, float, object)   # fraction (ou None), Mo recus, Mo total (ou None)
    finished_ok = Signal(str)                  # chemin du fichier telecharge
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, url: str, out_dir: str, max_height: int,
                 cancel_token: CancelToken, parent=None):
        super().__init__(parent)
        self.url = url
        self.out_dir = out_dir
        self.max_height = max_height
        self.cancel_token = cancel_token

    def run(self) -> None:
        from youtube.downloader import download_video

        try:
            path = download_video(
                self.url, self.out_dir, consent_confirmed=True,
                max_height=self.max_height,
                on_progress=lambda fraction, done_mb, total_mb: self.progress.emit(
                    fraction, done_mb, total_mb),
                cancel_token=self.cancel_token,
            )
        except CancelledError:
            self.cancelled.emit()
        except ClipFarmingError as error:
            # Ces erreurs portent deja un message ecrit pour un humain.
            self.failed.emit(str(error))
        except Exception as error:  # noqa: BLE001 - garde-fou : jamais de trace Python a l'ecran
            self.failed.emit(
                "Le téléchargement s'est interrompu de façon inattendue. "
                f"Détail technique : {type(error).__name__} — {error}"
            )
        else:
            self.finished_ok.emit(path)


class AppController(QObject):
    navigate_requested = Signal(str)         # cle de page ("home"/"analysis"/"results"/...)
    progress_updated = Signal(object)         # ProgressEvent
    analysis_finished = Signal(list)          # list[ClipResult]
    analysis_failed = Signal(str)
    analysis_cancelled = Signal()

    search_finished = Signal(list)            # list[RankedVideo]
    search_failed = Signal(str)

    # Une video telechargee depuis Recherche que l'utilisateur veut ouvrir dans
    # Voice Studio. La page Recherche ne connait pas Voice Studio et n'a pas a
    # le connaitre : elle emet, la fenetre principale aiguille.
    open_in_voice_studio = Signal(object)     # dict {path, title, video_id, url}

    def __init__(self):
        super().__init__()
        self._analysis_thread: Optional[AnalysisThread] = None
        self._search_thread: Optional[SearchThread] = None
        self._download_thread: Optional[VideoDownloadThread] = None
        self._download_token: Optional[CancelToken] = None
        self._cancel_token: Optional[CancelToken] = None
        # Threads qu'on a renonce a attendre (terminate() n'a pas suffi -- bloques
        # dans un appel systeme non interruptible). Garde une reference forte
        # pour ne jamais les laisser etre garbage-collectes pendant qu'ils
        # tournent encore ("QThread: Destroyed while thread is still running").
        self._orphaned_threads: list[QThread] = []

        self.last_results: list[ClipResult] = []
        # Duree reelle de la derniere production, pour le bilan de fin
        # (section 8). Mesuree ici et non deduite des clips : c'est le temps
        # passe par l'utilisateur a attendre, pas la somme des durees produites.
        self.last_elapsed_s: float = 0.0
        self._analysis_started_at: float = 0.0
        self.last_settings: Optional[Settings] = None
        self.current_project_folder: Optional[Path] = None
        self.current_project_name: str = ""
        self.last_source_kind: str = "local"

    # ---------- Analyse locale / YouTube ----------

    def start_analysis(
        self,
        cli_args: SimpleNamespace,
        name: str,
        source_label: str,
        source_kind: str = "local",
        source_url: Optional[str] = None,
        youtube_source: Optional[str] = None,
        editing_overrides: Optional[dict] = None,
    ) -> None:
        """`editing_overrides` : {nom_de_module: actif} choisi pour CE run
        depuis l'accueil. Les valeurs par defaut restent celles de
        config/editing.json (page Parametres) -- l'accueil ne fait que les
        surcharger le temps d'une analyse."""
        project_folder = project_store.create_project_folder(name, projects_dir=settings_store.projects_dir())
        cli_args.output = str(project_folder)
        cli_args.overwrite = True

        settings = load_settings(cli_args)
        if editing_overrides:
            editing = {k: dict(v) if isinstance(v, dict) else v for k, v in settings.editing.items()}
            for module, active in editing_overrides.items():
                if isinstance(editing.get(module), dict):
                    editing[module]["enabled"] = bool(active)
            settings.editing = editing

        project_store.write_manifest(
            project_folder, name=name, source_label=source_label, source_kind=source_kind,
            settings_used={
                "clip_duration": settings.clip_duration,
                "nb_clips": settings.nb_clips,
                "model": settings.model,
                "language": settings.language,
                "subtitle_style": settings.subtitle_style,
                "device": settings.device,
                # Section 12 : les modules actifs sont enregistres avec le
                # projet, pour qu'on sache plus tard comment un clip a ete
                # produit.
                "editing_modules": {
                    name: bool(block.get("enabled", False))
                    for name, block in settings.editing.items()
                    if isinstance(block, dict) and "enabled" in block
                },
            },
            source_url=source_url,
        )

        self.current_project_folder = project_folder
        self.current_project_name = name
        self.last_source_kind = source_kind
        self.last_settings = settings

        self._cancel_token = CancelToken()
        self._analysis_started_at = time.monotonic()
        self._analysis_thread = AnalysisThread(settings, self._cancel_token, youtube_source=youtube_source)
        self._analysis_thread.progress.connect(self.progress_updated)
        self._analysis_thread.finished_ok.connect(self._on_analysis_finished)
        self._analysis_thread.failed.connect(self._on_analysis_failed)

        self.navigate_requested.emit("analysis")
        self._analysis_thread.start()

    def cancel_analysis(self) -> None:
        """Limite connue : si le thread est bloque dans un appel reseau/C qui
        accapare le GIL sans jamais le liberer (observe dans le sandbox de dev
        de ce projet avec une resolution reseau qui ne repond pas), meme le
        QTimer du filet de securite ci-dessous peut mettre du temps a
        s'executer -- ce cas releve d'une bibliotheque tierce mal comportee,
        pas d'un blocage introduit par ce code. Sur une machine avec un acces
        reseau normal (ou hors ligne avec echec DNS rapide), ce n'est pas
        observe."""
        if self._cancel_token is not None:
            self._cancel_token.cancel()
        # Le jeton cooperatif n'est verifie qu'entre les etapes/segments/clips
        # -- s'il est bloque dans un appel bibliotheque tiers sans point de
        # controle (ex: chargement du modele Whisper, resolution reseau qui
        # traine), "Annuler" resterait sans effet indefiniment. Filet de
        # securite : on force l'arret si rien n'a bouge apres quelques secondes,
        # plutot que de laisser le bouton Annuler mentir a l'utilisateur.
        thread = self._analysis_thread
        if thread is not None:
            QTimer.singleShot(_FORCE_CANCEL_DELAY_MS, lambda: self._force_stop_if_still_running(thread))

    def _force_stop_if_still_running(self, thread: "AnalysisThread") -> None:
        if thread is self._analysis_thread and thread.isRunning():
            # terminate() est brutal (peut sauter les finally/ -- fichiers
            # temporaires potentiellement non nettoyes dans ce cas precis) mais
            # c'est le seul recours face a un appel tiers qui ne repond jamais ;
            # mieux vaut ca qu'une appli qui parait figee malgre "Annuler".
            thread.terminate()
            if not thread.wait(1000):
                # terminate() n'a meme pas ete honore sous 1s -- le thread est
                # bloque dans un appel non interruptible (ex: connexion reseau
                # qui ne repond pas). On n'attend pas plus longtemps : l'UI ne
                # doit jamais rester figee pour ca. Reference gardee pour eviter
                # le crash "Destroyed while thread is still running" quand ce
                # thread finira par se terminer de lui-meme (timeout OS/reseau).
                self._orphaned_threads.append(thread)
                thread.finished.connect(lambda t=thread: self._forget_orphan(t))
            self.analysis_cancelled.emit()
            self.navigate_requested.emit("home")

    def _forget_orphan(self, thread: QThread) -> None:
        if thread in self._orphaned_threads:
            self._orphaned_threads.remove(thread)

    def _on_analysis_finished(self, results: list[ClipResult]) -> None:
        self.last_elapsed_s = (
            time.monotonic() - self._analysis_started_at if self._analysis_started_at else 0.0
        )
        self.last_results = results
        self.analysis_finished.emit(results)
        self.navigate_requested.emit("results")

    def _on_analysis_failed(self, message: str) -> None:
        if message == _CANCELLED_SENTINEL:
            self.analysis_cancelled.emit()
        else:
            self.analysis_failed.emit(message)
        self.navigate_requested.emit("home")

    # ---------- Recherche YouTube ----------

    def start_search(self, query: str, filters, max_results: int, sort_by: str) -> None:
        self._search_thread = SearchThread(query, filters, max_results, sort_by)
        self._search_thread.finished_ok.connect(self.search_finished)
        self._search_thread.failed.connect(self.search_failed)
        self._search_thread.start()

    def start_video_download(self, url: str, out_dir: str, max_height: int) -> VideoDownloadThread:
        """Lance le telechargement et renvoie le fil, pour que la page branche
        ses propres signaux : la progression n'interesse qu'elle."""
        self._download_token = CancelToken()
        self._download_thread = VideoDownloadThread(url, out_dir, max_height,
                                                    self._download_token)
        return self._download_thread

    def cancel_video_download(self) -> None:
        if self._download_token is not None:
            self._download_token.cancel()

    # ---------- Projets ----------

    def list_projects(self) -> list[ProjectSummary]:
        return project_store.list_projects(projects_dir=settings_store.projects_dir())

    def load_project(self, folder: str) -> Project:
        return project_store.load_project(folder)

    def delete_project(self, folder: str) -> None:
        project_store.delete_project(folder)

    # ---------- Cycle de vie ----------

    def shutdown(self) -> None:
        """A appeler a la fermeture de l'appli -- un QThread encore en cours
        au moment ou son objet Python est detruit plante l'appli
        ("QThread: Destroyed while thread is still running"). Annule
        proprement l'analyse en cours (le pipeline nettoie deja ses fichiers
        temporaires dans son propre finally/), laisse une poignee de
        secondes, puis force l'arret plutot que de bloquer indefiniment la
        fermeture de la fenetre."""
        for thread in (self._analysis_thread, self._search_thread, self._download_thread):
            if thread is not None and thread.isRunning():
                if thread is self._analysis_thread and self._cancel_token is not None:
                    self._cancel_token.cancel()
                if thread is self._download_thread and self._download_token is not None:
                    self._download_token.cancel()
                thread.wait(5000)
                if thread.isRunning():
                    thread.terminate()
                    if not thread.wait(1000):
                        # Toujours bloque -- ne jamais empecher la fenetre de se
                        # fermer pour autant (voir _force_stop_if_still_running).
                        self._orphaned_threads.append(thread)
                        thread.finished.connect(lambda t=thread: self._forget_orphan(t))

    def open_project(self, folder: str) -> None:
        """Charge un projet passe et l'affiche comme si l'analyse venait de finir."""
        project = self.load_project(folder)
        self.last_results = [ClipResult(
            index=i + 1,
            file_name=c.get("clip", ""),
            start=c.get("start", 0.0),
            end=c.get("end", 0.0),
            duration=c.get("duration", 0.0),
            score=c.get("score", 0.0),
            scores=c.get("scores", {}),
            transcript=c.get("transcript", ""),
            language=c.get("language", ""),
            reasons=c.get("reasons", []),
        ) for i, c in enumerate(project.clips)]
        self.current_project_folder = Path(project.summary.folder)
        self.current_project_name = project.summary.name
        self.last_source_kind = project.summary.source_kind
        self.navigate_requested.emit("results")
