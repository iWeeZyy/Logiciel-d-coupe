"""Synthese vocale LOCALE.

Rien ne sort de la machine : ni le texte, ni l'audio produit. Aucun compte,
aucune cle d'API, aucun abonnement. C'est la contrainte qui a decide du moteur.

Moteur retenu : les voix DEJA INSTALLEES sur le systeme, atteintes par pyttsx3
-- SAPI5 sous Windows (les voix de Windows, dont les francaises quand elles
sont installees), espeak-ng sous Linux, NSSpeechSynthesizer sous macOS. Deux
raisons : rien a telecharger (une voix Piper pese des dizaines de megaoctets et
vient d'un depot distant), et rien a compiler.

Piper est PREVU mais pas impose : `PiperEngine` s'active seulement si un
executable et une voix sont reellement presents sur la machine. Tant qu'ils ne
le sont pas, il n'apparait pas dans la liste -- plutot qu'un choix qui echoue
au moment de generer.

Le clonage de voix n'existe pas ici, volontairement (section 19 du cahier des
charges) : reproduire la voix d'une personne reelle a partir de sa video
demande son autorisation, et rien dans ce logiciel ne peut la verifier.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path

from core.logging_setup import get_logger

logger = get_logger()

MIN_RATE, MAX_RATE = 0.5, 2.0
MIN_VOLUME, MAX_VOLUME = 0.0, 1.0
MAX_PAUSE_S = 3.0

# pyttsx3 raisonne en mots par minute ; l'interface propose un multiplicateur.
BASE_WORDS_PER_MINUTE = 175


class TtsError(Exception):
    """Echec de la synthese, formule pour l'utilisateur."""


@dataclass(frozen=True)
class Voice:
    """Une voix disponible sur CETTE machine."""

    id: str
    label: str
    language: str = ""
    engine: str = "system"

    @property
    def is_french(self) -> bool:
        return (self.language or "").lower().startswith("fr")


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


def split_sentences(text: str) -> list[str]:
    """Decoupe utilisee UNIQUEMENT pour la pause entre phrases.

    Le texte n'est pas modifie : les morceaux remis bout a bout redonnent
    exactement ce qui a ete ecrit.
    """
    import re

    parts = re.split(r"(?<=[.!?…])\s+", (text or "").strip())
    return [p for p in parts if p.strip()]


class SystemVoiceEngine:
    """Voix du systeme, via pyttsx3."""

    name = "system"

    def __init__(self):
        self._engine = None

    def _driver(self):
        if self._engine is None:
            try:
                import pyttsx3
            except ImportError as error:
                raise TtsError(
                    "Le moteur de voix n'est pas installé. Lance : pip install -r requirements.txt"
                ) from error
            try:
                self._engine = pyttsx3.init()
            except Exception as error:
                raise TtsError(
                    "Aucun moteur de synthèse vocale n'a pu être démarré sur cet ordinateur. "
                    "Sous Windows, les voix se ajoutent dans Paramètres > Heure et langue > Voix."
                ) from error
        return self._engine

    def available(self) -> bool:
        try:
            return bool(self.voices())
        except TtsError:
            return False

    def voices(self) -> list[Voice]:
        driver = self._driver()
        found: list[Voice] = []
        for voice in driver.getProperty("voices") or []:
            languages = []
            for language in getattr(voice, "languages", []) or []:
                if isinstance(language, bytes):
                    language = language.decode("utf-8", "replace")
                languages.append(str(language).strip("\x05 ").replace("_", "-"))
            code = languages[0] if languages else ""
            if not code:
                # SAPI5 ne renseigne pas toujours `languages` : le nom porte
                # alors la langue ("Microsoft Hortense - French (France)").
                lowered = (voice.name or "").lower()
                code = "fr" if "french" in lowered or "français" in lowered else ""
            found.append(Voice(id=voice.id, label=voice.name or voice.id,
                               language=code, engine=self.name))
        return found

    def synthesize(self, text: str, out_wav: str, voice_id: str = "", rate: float = 1.0,
                   volume: float = 1.0, sentence_pause_s: float = 0.0) -> str:
        driver = self._driver()
        if voice_id:
            try:
                driver.setProperty("voice", voice_id)
            except Exception as error:                 # pragma: no cover - depend du systeme
                raise TtsError("Cette voix n'est plus disponible sur cet ordinateur.") from error
        driver.setProperty("rate", int(BASE_WORDS_PER_MINUTE * clamp(rate, MIN_RATE, MAX_RATE)))
        driver.setProperty("volume", clamp(volume, MIN_VOLUME, MAX_VOLUME))

        Path(out_wav).parent.mkdir(parents=True, exist_ok=True)
        pause = clamp(sentence_pause_s, 0.0, MAX_PAUSE_S)
        if pause <= 0:
            driver.save_to_file(text, out_wav)
            driver.runAndWait()
        else:
            # La pause est un silence AJOUTE entre les phrases, pas une
            # modification du texte : chaque phrase est synthetisee telle
            # quelle, les morceaux sont ensuite colles avec du silence.
            # Une phrase, un runAndWait : enchainer plusieurs save_to_file
            # avant un seul runAndWait ne produit qu'un fichier chez certains
            # pilotes (constate ici avec espeak-ng). Verifie fichier par
            # fichier plutot que suppose.
            pieces = []
            for index, sentence in enumerate(split_sentences(text)):
                piece = f"{out_wav}.part{index}.wav"
                driver.save_to_file(sentence, piece)
                driver.runAndWait()
                if Path(piece).is_file():
                    pieces.append(piece)
            if not pieces:
                raise TtsError(
                    "La voix n'a produit aucun son. Essaie une autre voix, ou vérifie "
                    "qu'une voix est bien installée sur cet ordinateur."
                )
            concat_wavs(pieces, out_wav, silence_s=pause)
            for piece in pieces:
                Path(piece).unlink(missing_ok=True)

        if not Path(out_wav).is_file() or Path(out_wav).stat().st_size < 128:
            raise TtsError(
                "La voix n'a produit aucun son. Essaie une autre voix, ou vérifie "
                "qu'une voix est bien installée sur cet ordinateur."
            )
        return out_wav


class PiperEngine:
    """Piper, s'il est reellement installe sur la machine.

    Rien n'est telecharge par l'application : l'executable et le fichier de
    voix (.onnx) doivent etre presents. Sans eux, ce moteur ne se declare pas
    disponible et n'apparait nulle part -- une voix proposee doit fonctionner.

    Emplacements cherches : la variable d'environnement PIPER_BIN /
    PIPER_VOICES, puis un dossier "piper" a cote des donnees de
    l'application.
    """

    name = "piper"

    def __init__(self, binary: str | None = None, voices_dir: str | None = None):
        self._binary = binary or os.environ.get("PIPER_BIN") or shutil.which("piper") or ""
        self._voices_dir = Path(voices_dir or os.environ.get("PIPER_VOICES") or self._default_dir())

    @staticmethod
    def _default_dir() -> Path:
        from core.paths import user_data_dir

        return user_data_dir() / "voice_studio_data" / "piper"

    def available(self) -> bool:
        return bool(self._binary) and Path(self._binary).exists() and bool(self.voices())

    def voices(self) -> list[Voice]:
        if not self._voices_dir.is_dir():
            return []
        return [Voice(id=str(path), label=f"Piper — {path.stem}",
                      language=path.stem.split("-")[0], engine=self.name)
                for path in sorted(self._voices_dir.glob("*.onnx"))]

    def synthesize(self, text: str, out_wav: str, voice_id: str = "", rate: float = 1.0,
                   volume: float = 1.0, sentence_pause_s: float = 0.0) -> str:
        if not self.available():
            raise TtsError("Piper n'est pas installé sur cet ordinateur.")
        model = voice_id or (self.voices()[0].id if self.voices() else "")
        Path(out_wav).parent.mkdir(parents=True, exist_ok=True)
        command = [self._binary, "--model", model, "--output_file", out_wav]
        if rate and rate != 1.0:
            command += ["--length_scale", f"{1.0 / clamp(rate, MIN_RATE, MAX_RATE):.3f}"]
        if sentence_pause_s:
            command += ["--sentence_silence", f"{clamp(sentence_pause_s, 0.0, MAX_PAUSE_S):.2f}"]
        try:
            subprocess.run(command, input=(text or "").encode("utf-8"),
                           capture_output=True, check=True)
        except subprocess.CalledProcessError as error:  # pragma: no cover - depend de Piper
            raise TtsError(
                "Piper n'a pas pu générer la voix. "
                f"Détail : {(error.stderr or b'').decode('utf-8', 'replace')[:200]}"
            ) from error
        return out_wav


def concat_wavs(paths: list[str], out_wav: str, silence_s: float = 0.0) -> str:
    """Colle des WAV bout a bout, avec un silence entre eux.

    Passe par le module `wave` de Python plutot que par ffmpeg : ce sont des
    fichiers produits a l'instant, tous au meme format, et cela evite de rendre
    la pause entre phrases dependante de la presence de ffmpeg.
    """
    if not paths:
        raise TtsError("Aucun son à assembler.")
    with wave.open(paths[0], "rb") as first:
        params = first.getparams()
        frames = [first.readframes(first.getnframes())]
    silence = b"\x00" * int(params.framerate * max(0.0, silence_s)) * params.sampwidth * params.nchannels
    for path in paths[1:]:
        with wave.open(path, "rb") as source:
            if silence_s > 0:
                frames.append(silence)
            frames.append(source.readframes(source.getnframes()))
    with wave.open(out_wav, "wb") as target:
        target.setparams(params)
        target.writeframes(b"".join(frames))
    return out_wav


def to_mp3(wav_path: str, mp3_path: str) -> str:
    """Conversion WAV -> MP3 par le ffmpeg deja livre avec l'application.

    Sans ffmpeg, on le DIT : l'export WAV reste disponible, il n'y a pas de
    silence trompeur.
    """
    from video.ffmpeg_utils import FFMPEG_BIN

    binary = FFMPEG_BIN if Path(FFMPEG_BIN).exists() else shutil.which("ffmpeg")
    if not binary:
        raise TtsError("ffmpeg est introuvable : l'export MP3 n'est pas possible. "
                       "L'export WAV, lui, fonctionne.")
    try:
        subprocess.run([binary, "-y", "-loglevel", "error", "-i", wav_path,
                        "-codec:a", "libmp3lame", "-q:a", "2", mp3_path],
                       capture_output=True, check=True)
    except subprocess.CalledProcessError as error:
        raise TtsError(
            "La conversion en MP3 a échoué. "
            f"Détail : {(error.stderr or b'').decode('utf-8', 'replace')[:200]}"
        ) from error
    return mp3_path


def engines() -> list:
    """Moteurs presents sur cette machine, le systeme d'abord."""
    return [engine for engine in (SystemVoiceEngine(), PiperEngine()) if engine.available()]


def available_voices() -> list[Voice]:
    """Toutes les voix utilisables, francaises en tete.

    Liste vide = aucune voix sur cette machine. L'interface l'annonce et
    desactive la generation, au lieu d'offrir un bouton qui echouera.
    """
    found: list[Voice] = []
    for engine in engines():
        try:
            found.extend(engine.voices())
        except TtsError as error:                      # pragma: no cover - depend du systeme
            logger.warning(f"Moteur de voix {engine.name} indisponible : {error}")
    return sorted(found, key=lambda v: (not v.is_french, v.label.lower()))


def engine_for(voice: Voice | None):
    for engine in engines():
        if voice is None or engine.name == voice.engine:
            return engine
    raise TtsError("Aucun moteur de synthèse vocale n'est disponible sur cet ordinateur.")


def synthesize(text: str, out_wav: str, voice: Voice | None = None, rate: float = 1.0,
               volume: float = 1.0, sentence_pause_s: float = 0.0) -> str:
    """Genere le fichier et renvoie son chemin."""
    if not (text or "").strip():
        raise TtsError("Il n'y a pas de texte à lire.")
    engine = engine_for(voice)
    return engine.synthesize(text, out_wav, voice_id=voice.id if voice else "",
                             rate=rate, volume=volume, sentence_pause_s=sentence_pause_s)
