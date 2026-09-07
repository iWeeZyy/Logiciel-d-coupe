"""Exceptions explicites du pipeline.

Chacune porte un message directement affichable a l'utilisateur (en francais,
sans jargon Python) -- c'est ce que main.py attrape pour eviter une stack trace
incomprehensible (point 14 du cahier des charges : robustesse).
"""
from __future__ import annotations


class ClipFarmingError(Exception):
    """Base pour toutes les erreurs controlees du pipeline."""


class ConfigError(ClipFarmingError):
    """Fichier de configuration manquant, invalide, ou parametre CLI incoherent."""


class InputFileError(ClipFarmingError):
    """Fichier d'entree introuvable, illisible, ou format non reconnu par ffprobe."""


class NoAudioError(ClipFarmingError):
    """La video n'a pas de piste audio -- impossible de transcrire ni de scorer l'audio."""


class UnsupportedLanguageError(ClipFarmingError):
    """Langue detectee/demandee non geree correctement par le modele Whisper choisi."""


class ModelDownloadError(ClipFarmingError):
    """Echec de telechargement d'un modele (Whisper ou detecteur de visage) et pas de copie locale utilisable."""


class FfmpegError(ClipFarmingError):
    """Un appel ffmpeg/ffprobe a echoue. Le message inclut la fin du stderr ffmpeg."""


class InsufficientContentError(ClipFarmingError):
    """Pas assez de passages exploitables dans la video pour produire --nb-clips clips distincts."""


class OutputExistsError(ClipFarmingError):
    """Le dossier de sortie contient deja des fichiers clip_*.mp4 et --overwrite n'a pas ete passe."""


class InsufficientMemoryError(ClipFarmingError):
    """Echec d'allocation memoire (modele trop gros pour la machine) -- suggere un modele plus petit."""
