"""Page Accueil -- deposer une video, choisir 3 reglages rapides, lancer.
Les reglages avances (pre/post-roll, min-gap, style de sous-titres, GPU/CPU)
vivent dans Paramètres, pas ici : le cahier des charges est explicite sur le
fait que cette page doit rester simple (section 19 -- "simplicite + rapidite
+ lisibilite")."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QMessageBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from gui import settings_store
from gui.branding import APP_TAGLINE
from content_factory import planning
from video import ffmpeg_utils
from radar import clip_download
from youtube import downloader
from gui.controller import AppController
from gui.widgets.drop_zone import DropZone
from gui.widgets.production_options import ProductionOptionsBox
from utils.hardware import cuda_device_count

_DURATION_PRESETS = [15, 30, 45, 60]

# Duree "automatique" : la detection de contexte ajuste ensuite chaque clip sur
# la structure du discours. On part d'une valeur mediane plutot que d'un choix
# fantaisiste -- et l'etiquette le dit, pour ne pas laisser croire a une magie
# qui devinerait la duree ideale.
_AUTO_DURATION = 45

# Modules proposes directement sur l'accueil (section 8). Les autres
# (contexte, cadrage, silences, hesitations, zoom) se reglent dans Parametres :
# les mettre tous ici transformerait l'accueil en tableau de bord.
_MODEL_CHOICES = [
    ("tiny", "Tiny", "Très rapide • qualité basique • ~1 Go RAM"),
    ("base", "Base", "Rapide • qualité correcte • ~1 Go RAM"),
    ("small", "Small", "Bon compromis vitesse/qualité • ~2 Go RAM • recommandé sans GPU"),
    ("medium", "Medium", "Lent sur CPU • bonne qualité • ~5 Go RAM • ~1,5 Go à télécharger"),
    ("large-v3", "Large", "Très lent sur CPU • meilleure qualité • ~10 Go RAM • ~3 Go à télécharger"),
]

# Modeles dont le cout sur processeur justifie une confirmation explicite : sans
# GPU, ils transcrivent souvent plus lentement que la duree de la video, et
# l'utilisateur lancerait des heures de calcul sans l'avoir voulu.
_HEAVY_MODELS = {"medium", "large", "large-v2", "large-v3"}


def _safe_name(source: str) -> str:
    """Un nom de dossier de projet tire d'un lien, sans caractere interdit.

    Windows refuse : \\ / : * ? " < > | -- et une URL en contient toujours.
    On garde les 40 derniers caracteres utiles : l'identifiant de la video est
    en fin d'adresse, c'est lui qui distingue deux projets."""
    import re

    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", (source or "").strip()).strip("-")
    return cleaned[-40:] or "video"


def _field_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet("font-size: 12.5px; font-weight: 600; margin-top: 4px;")
    return label


class HomePage(QWidget):
    def __init__(self, controller: AppController):
        super().__init__()
        self.controller = controller
        self.selected_video_path: str | None = None
        self.video_duration_s: float | None = None
        # Un lien et un fichier local s'excluent : la derniere source
        # designee gagne, et l'autre est effacee. Laisser les deux renseignees
        # obligerait a deviner laquelle l'utilisateur voulait.
        self.link_source: str = ""
        # "" | "youtube" | "twitch". Le champ est unique -- l'utilisateur colle
        # une adresse, il n'a pas a declarer d'abord de quelle plateforme elle
        # vient -- mais ce qui suit differe assez (consentement, module de
        # telechargement, nom de projet) pour que la page sache laquelle.
        self.link_kind: str = ""

        # La page defile. Sans cela, des que la carte depassait la hauteur de la
        # fenetre, Qt comprimait les widgets les uns sur les autres : les champs
        # "Duree" et "Nombre de clips" se retrouvaient PAR-DESSUS la zone de
        # depot. Une page trop haute doit defiler, pas se replier sur elle-meme.
        page_layout = QVBoxLayout(self)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        scroll.setWidget(content)
        page_layout.addWidget(scroll)

        outer = QVBoxLayout(content)
        outer.setContentsMargins(40, 36, 40, 36)
        outer.setSpacing(0)
        outer.setAlignment(Qt.AlignmentFlag.AlignTop)

        title = QLabel(APP_TAGLINE)
        title.setProperty("role", "pageTitle")
        outer.addWidget(title)
        outer.addSpacing(20)

        card = QFrame()
        card.setProperty("role", "card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(28, 28, 28, 28)
        card_layout.setSpacing(14)
        card.setMaximumWidth(620)
        outer.addWidget(card)

        self.drop_zone = DropZone()
        self.drop_zone.file_selected.connect(self._on_file_selected)
        card_layout.addWidget(self.drop_zone)

        # --- ou un lien YouTube / un clip Twitch ---
        # Le telechargement passe par youtube/downloader.py ou par
        # radar/clip_download.py, exactement comme la page Recherche et le
        # Radar : meme selecteur de qualite, meme verrou de consentement, meme
        # gestion d'erreurs. Rien n'est reecrit ici.
        link_label = QLabel("… ou colle un lien YouTube ou un clip Twitch")
        link_label.setProperty("role", "muted")
        card_layout.addWidget(link_label)

        link_row = QHBoxLayout()
        self.link_edit = QLineEdit()
        self.link_edit.setPlaceholderText(
            "https://www.youtube.com/watch?v=…  ou  https://clips.twitch.tv/…")
        self.link_edit.setClearButtonEnabled(True)
        self.link_edit.textChanged.connect(self._on_link_changed)
        link_row.addWidget(self.link_edit, stretch=1)
        card_layout.addLayout(link_row)

        self.link_hint = QLabel("")
        self.link_hint.setProperty("role", "muted")
        self.link_hint.setWordWrap(True)
        card_layout.addWidget(self.link_hint)

        # Le MEME verrou que la page Recherche, et pour la meme raison : rien
        # n'est telecharge de YouTube sans que l'utilisateur ait confirme ses
        # droits. Il n'apparait qu'avec un lien YOUTUBE : Twitch propose
        # lui-meme le telechargement d'un clip (menu Partager), donc exiger une
        # confirmation avant de telecharger interdirait ce que la plateforme
        # autorise. Le rappel sur les droits reste affiche pour un clip, mais
        # comme un rappel avant publication, pas comme une condition --
        # l'explication complete est dans radar/clip_download.py.
        self.consent_check = QCheckBox(
            "Je confirme disposer des droits nécessaires pour ce traitement.")
        self.consent_check.setVisible(False)
        self.consent_check.toggled.connect(self._refresh_generate_enabled)
        card_layout.addWidget(self.consent_check)

        # --- Toute la video, ou des clips ---
        # Ce choix commande les deux champs suivants : quand on garde la video
        # entiere, il n'y a ni duree de clip ni nombre de clips a decider. Les
        # champs sont donc grises plutot que laisses actifs sans effet.
        self.whole_video_check = QCheckBox("Garder toute la vidéo (une seule sortie, sans découpage)")
        self.whole_video_check.toggled.connect(self._on_whole_video_changed)
        card_layout.addWidget(self.whole_video_check)

        self.whole_video_hint = QLabel(
            "Aucun passage n'est cherché : la vidéo est traitée en entier, avec "
            "les mêmes sous-titres, le même cadrage et le même filigrane qu'un clip."
        )
        self.whole_video_hint.setProperty("role", "muted")
        self.whole_video_hint.setWordWrap(True)
        self.whole_video_hint.setVisible(False)
        card_layout.addWidget(self.whole_video_hint)

        # --- Duree des clips ---
        self.duration_label = _field_label("Durée des clips")
        card_layout.addWidget(self.duration_label)
        duration_row = QHBoxLayout()
        self.duration_combo = QComboBox()
        self.duration_combo.addItem(f"Automatique (~{_AUTO_DURATION} s, ajustée au contexte)", 0)
        for seconds in _DURATION_PRESETS:
            self.duration_combo.addItem(f"{seconds} secondes", seconds)
        self.duration_combo.addItem("Personnalisée", -1)
        self.duration_combo.setCurrentIndex(0)
        self.duration_combo.currentIndexChanged.connect(self._on_duration_changed)
        duration_row.addWidget(self.duration_combo, stretch=1)

        self.custom_duration_spin = QSpinBox()
        self.custom_duration_spin.setRange(5, 600)
        self.custom_duration_spin.setValue(45)
        self.custom_duration_spin.setSuffix(" s")
        self.custom_duration_spin.setVisible(False)
        duration_row.addWidget(self.custom_duration_spin)
        card_layout.addLayout(duration_row)

        # --- Nombre de clips ---
        self.nb_clips_label = _field_label("Nombre de clips")
        card_layout.addWidget(self.nb_clips_label)
        nb_row = QHBoxLayout()
        self.nb_clips_auto = QCheckBox("Automatique")
        self.nb_clips_auto.setChecked(True)
        self.nb_clips_auto.toggled.connect(self._on_nb_clips_mode_changed)
        nb_row.addWidget(self.nb_clips_auto)

        self.nb_clips_spin = QSpinBox()
        self.nb_clips_spin.setRange(1, 50)
        self.nb_clips_spin.setValue(planning.MIN_CLIPS)
        self.nb_clips_spin.setEnabled(False)
        nb_row.addWidget(self.nb_clips_spin, stretch=1)
        card_layout.addLayout(nb_row)

        # Le chiffre propose ne doit pas tomber du ciel : on dit d'ou il vient.
        self.nb_clips_hint = QLabel("Déduit de la durée de la vidéo une fois celle-ci choisie.")
        self.nb_clips_hint.setProperty("role", "muted")
        card_layout.addWidget(self.nb_clips_hint)

        # --- Modele Whisper ---
        card_layout.addWidget(_field_label("Modèle Whisper"))
        self.model_combo = QComboBox()
        for key, label, _hint in _MODEL_CHOICES:
            self.model_combo.addItem(label, key)
        default_model = settings_store.get("default_model")
        default_index = next((i for i, (k, _, _) in enumerate(_MODEL_CHOICES) if k == default_model), 2)
        self.model_combo.setCurrentIndex(default_index)
        self.model_combo.currentIndexChanged.connect(self._update_model_hint)
        card_layout.addWidget(self.model_combo)

        self.model_hint = QLabel("")
        self.model_hint.setProperty("role", "muted")
        card_layout.addWidget(self.model_hint)
        self._update_model_hint()

        # --- GPU / CPU (information seule -- le choix se fait dans Parametres) ---
        gpu_count = cuda_device_count()
        gpu_text = f"GPU détecté ✓ ({gpu_count})" if gpu_count > 0 else "Aucun GPU compatible détecté — traitement CPU"
        gpu_label = QLabel(gpu_text)
        gpu_label.setProperty("role", "muted")
        card_layout.addWidget(gpu_label)

        # --- Options de production ---
        # La ligne "Format : 9:16 (1080 x 1920)" qui se trouvait ici annoncait
        # un fait ; c'est devenu un choix, et la laisser aurait affiche deux
        # formats a l'ecran, l'un fixe et l'autre reglable.
        card_layout.addWidget(_field_label("Édition automatique"))
        # Trois colonnes : six cases sur une seule ligne dans une carte de
        # 620 px tronquaient les libelles ("Cadrage int...", "Montage au...").
        self.production_options = ProductionOptionsBox(columns=3)
        card_layout.addWidget(self.production_options)

        modules_hint = QLabel(
            "Cadrage suivi, contexte, silences et zooms se règlent dans Paramètres."
        )
        modules_hint.setProperty("role", "muted")
        card_layout.addWidget(modules_hint)

        card_layout.addSpacing(8)
        self.generate_btn = QPushButton("🚀  CRÉER LES CONTENUS")
        self.generate_btn.setProperty("variant", "primary")
        self.generate_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.generate_btn.setMinimumHeight(52)
        self.generate_btn.setStyleSheet("font-size: 15px; font-weight: 800;")
        self.generate_btn.setEnabled(False)
        self.generate_btn.clicked.connect(self._on_generate_clicked)
        card_layout.addWidget(self.generate_btn)

    def _on_whole_video_changed(self, whole: bool) -> None:
        """Garder toute la video retire deux decisions, il faut donc retirer
        leurs champs : les laisser actifs laisserait croire qu'ils comptent
        encore."""
        self.whole_video_hint.setVisible(whole)
        for widget in (self.duration_label, self.duration_combo, self.custom_duration_spin,
                       self.nb_clips_label, self.nb_clips_auto, self.nb_clips_hint):
            widget.setEnabled(not whole)
        self.nb_clips_spin.setEnabled(not whole and not self.nb_clips_auto.isChecked())
        self.generate_btn.setText("🚀  TRAITER TOUTE LA VIDÉO" if whole
                                  else "🚀  CRÉER LES CONTENUS")

    def _on_duration_changed(self) -> None:
        is_custom = self.duration_combo.currentData() == -1
        self.custom_duration_spin.setVisible(is_custom)

    def _update_model_hint(self) -> None:
        key = self.model_combo.currentData()
        hint = next((h for k, _, h in _MODEL_CHOICES if k == key), "")
        self.model_hint.setText(hint)

    def _on_nb_clips_mode_changed(self, automatic: bool) -> None:
        self.nb_clips_spin.setEnabled(not automatic and not self.whole_video_check.isChecked())
        self._refresh_nb_clips_hint()

    def _refresh_nb_clips_hint(self) -> None:
        if not self.nb_clips_auto.isChecked():
            self.nb_clips_hint.setText("Nombre fixé manuellement.")
            return
        if self.video_duration_s is None:
            self.nb_clips_hint.setText("Déduit de la durée de la vidéo une fois celle-ci choisie.")
            return
        self.nb_clips_hint.setText(planning.describe(self.video_duration_s))

    def _on_file_selected(self, path: str) -> None:
        self.selected_video_path = path
        # Les deux sources s'excluent : choisir un fichier abandonne le lien.
        if self.link_edit.text().strip():
            self.link_edit.clear()
        self._refresh_generate_enabled()

        # Duree lue tout de suite : c'est elle qui permet de proposer un nombre
        # de clips. Une video illisible par ffprobe ne doit pas empecher de
        # lancer -- le pipeline le signalera bien mieux que l'accueil.
        try:
            self.video_duration_s = ffmpeg_utils.video_duration(path)
        except Exception:
            self.video_duration_s = None
        if self.video_duration_s:
            self.nb_clips_spin.setValue(planning.suggested_clip_count(self.video_duration_s))
        self._refresh_nb_clips_hint()

    def _on_link_changed(self, value: str) -> None:
        """Le lien decide de l'etat du bouton, et le dit.

        On ne verifie PAS que la video existe : cela demanderait un appel
        reseau a chaque frappe. On verifie que l'adresse designe bien YouTube
        ou un clip Twitch, ce qui evite d'envoyer un lien Vimeo a yt-dlp pour
        qu'il echoue trente secondes plus tard sur un message technique.

        L'ORDRE DES DEUX TESTS NE COMPTE PAS : les deux fonctions travaillent
        sur des listes d'hotes disjointes, aucune adresse ne peut satisfaire
        les deux.
        """
        value = (value or "").strip()
        if value:
            # Les deux sources s'excluent : coller un lien abandonne le fichier.
            self.selected_video_path = None
            self.video_duration_s = None

        if not value:
            self.link_source, self.link_kind = "", ""
            self.link_hint.setText("")
        elif downloader.looks_like_youtube(value):
            self.link_source, self.link_kind = value, "youtube"
            self.link_hint.setText(
                "La vidéo sera téléchargée dans la meilleure qualité disponible, "
                "puis analysée comme un fichier local. Elle n'est pas conservée "
                "après l'analyse.")
        elif clip_download.looks_like_twitch_clip(value):
            self.link_source, self.link_kind = value, "twitch"
            self.link_hint.setText(
                "Le clip sera téléchargé dans la meilleure qualité disponible, "
                "puis analysé comme un fichier local. Il est gardé avec les clips "
                "du Radar, pour ne pas être retéléchargé à chaque analyse.\n"
                "Pouvoir télécharger un clip ne donne aucun droit de le republier : "
                "Twitch fournit un fichier, pas une licence.")
        else:
            self.link_source, self.link_kind = "", ""
            self.link_hint.setText(
                "Ce lien n'est reconnu ni comme une adresse YouTube, ni comme un "
                "clip Twitch. Colle une adresse youtube.com ou youtu.be, ou un "
                "clip clips.twitch.tv / twitch.tv/…/clip/… — une VOD ou un direct "
                "Twitch ne peuvent pas être téléchargés.")

        self.consent_check.setVisible(self.link_kind == "youtube")
        self._refresh_nb_clips_hint()
        self._refresh_generate_enabled()

    def _refresh_generate_enabled(self) -> None:
        """Une seule regle, un seul endroit : le bouton ne ment jamais sur ce
        qui manque."""
        if self.link_kind == "youtube":
            self.generate_btn.setEnabled(self.consent_check.isChecked())
        elif self.link_source:
            self.generate_btn.setEnabled(True)
        else:
            self.generate_btn.setEnabled(bool(self.selected_video_path))

    def _whole_clip_duration(self) -> int:
        """Duree annoncee quand on garde toute la video.

        Celle de la source quand elle a pu etre mesuree, sinon la valeur par
        defaut : le pipeline prend de toute facon la fenetre entiere, cette
        valeur ne sert qu'aux plafonds et aux notes."""
        if self.video_duration_s:
            return max(5, int(self.video_duration_s))
        return 45

    def _clip_duration(self) -> int:
        data = self.duration_combo.currentData()
        if data == -1:
            return self.custom_duration_spin.value()
        return _AUTO_DURATION if data == 0 else int(data)

    def _editing_overrides(self) -> dict:
        return self.production_options.editing_overrides()

    def _confirm_heavy_model(self) -> bool:
        """Sans GPU, un gros modele peut transcrire plus lentement que la duree
        de la video. Mieux vaut le dire avant de lancer que de laisser
        l'utilisateur decouvrir au bout d'une heure que ce n'est pas fini."""
        model = self.model_combo.currentData()
        if model not in _HEAVY_MODELS or cuda_device_count() > 0:
            return True

        reply = QMessageBox.warning(
            self,
            "Modèle lourd sans GPU",
            f"Aucun GPU n'a été détecté sur cette machine.\n\n"
            f"Sur processeur, le modèle « {self.model_combo.currentText()} » transcrit souvent "
            f"plus lentement que la durée de la vidéo elle-même, et doit d'abord être "
            f"téléchargé (plusieurs Go au premier lancement).\n\n"
            f"Le modèle « Small » donne une très bonne qualité en une fraction du temps.\n\n"
            f"Lancer quand même avec ce modèle ?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return reply == QMessageBox.StandardButton.Yes

    def _on_generate_clicked(self) -> None:
        if not (self.selected_video_path or self.link_source):
            return
        if self.link_kind == "youtube" and not self.consent_check.isChecked():
            return
        if not self._confirm_heavy_model():
            return

        whole = self.whole_video_check.isChecked()
        cli_args = SimpleNamespace(
            # Vide pour un lien : le fil d'analyse le remplit apres
            # telechargement, exactement comme le fait deja la page Recherche.
            input=self.selected_video_path or "",
            # Toute la video : une seule sortie, et une duree demandee qui
            # couvre la source entiere. La duree reste renseignee parce que
            # d'autres reglages s'y rapportent (plafonds, notes affichees) --
            # elle n'a simplement plus a decider d'un decoupage.
            clip_duration=self._whole_clip_duration() if whole else self._clip_duration(),
            nb_clips=1 if whole else self.nb_clips_spin.value(),
            whole_source=whole,
            model=self.model_combo.currentData(),
            language=None,
            pre_roll=None,
            post_roll=None,
            min_gap=None,
            # Le style de montage impose son style de sous-titres ; sans style
            # de montage, celui des Parametres reprend la main.
            subtitle_style=(self.production_options.subtitle_style()
                            or settings_store.get("default_subtitle_style")),
            device=settings_store.get("default_device"),
            no_cache=False,
            debug_scores=False,
            aspect=self.production_options.aspect(),
            fill_mode=self.production_options.fill_mode(),
            fit_mode=self.production_options.fit_mode(),
        )
        if self.link_kind == "youtube":
            # Le nom du projet ne peut pas etre le titre de la video : il n'est
            # pas encore connu, et aller le chercher demanderait un appel
            # reseau et une cle API que l'accueil n'exige pas. L'identifiant
            # suffit, et le titre reste lisible dans le lien enregistre.
            label = downloader.resolve_watch_url(self.link_source)
            self.controller.start_analysis(
                cli_args, name=f"youtube-{_safe_name(self.link_source)}",
                source_label=label, source_kind="youtube", source_url=label,
                youtube_source=self.link_source,
                editing_overrides=self._editing_overrides(),
            )
            return

        if self.link_kind == "twitch":
            # Meme raisonnement que pour YouTube : le titre du clip n'est connu
            # qu'apres l'appel a Twitch. Le slug, lui, est dans l'adresse et
            # suffit a distinguer deux projets.
            label = clip_download.clip_url(self.link_source)
            self.controller.start_analysis(
                cli_args, name=f"twitch-{_safe_name(self.link_source)}",
                source_label=label, source_kind="twitch", source_url=label,
                twitch_source=self.link_source,
                editing_overrides=self._editing_overrides(),
            )
            return

        name = Path(self.selected_video_path).stem
        self.controller.start_analysis(
            cli_args, name=name, source_label=Path(self.selected_video_path).name,
            source_kind="local", editing_overrides=self._editing_overrides(),
        )

    def on_shown(self) -> None:
        pass
