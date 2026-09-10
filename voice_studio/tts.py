"""Synthese vocale LOCALE : deux moteurs, aucune API.

Rien ne sort de la machine : ni le texte, ni l'audio produit. Aucun compte,
aucune cle d'API, aucun abonnement.

DEUX MOTEURS, ET ILS NE SERVENT PAS A LA MEME CHOSE :

- `SystemVoiceEngine` : les voix DEJA installees dans le systeme (SAPI5 sous
  Windows -- Hortense et compagnie --, espeak-ng sous Linux), via pyttsx3.
  Aucun telechargement, disponible immediatement, qualite de voix systeme.
- `PiperEngine` : des voix neuronales nettement plus naturelles, executees
  localement a partir d'un modele .onnx que l'utilisateur installe depuis le
  gestionnaire de voix (voice_studio/piper_models.py). Rien n'est telecharge
  sans clic, et une fois le modele la, plus aucune connexion n'est necessaire.

Les deux exposent la meme interface -- `available()`, `voices()`,
`synthesize()` -- et c'est tout ce que le reste du logiciel connait. Ajouter un
troisieme moteur demain, c'est ajouter une classe a `engines()`, sans toucher
ni a l'interface ni au cache.

Le clonage de voix n'existe pas ici, volontairement : reproduire la voix d'une
personne reelle a partir de sa video demande son autorisation, et rien dans ce
logiciel ne peut la verifier.
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
    """Une voix disponible sur CETTE machine.

    `engine` dit d'ou elle vient : c'est ce qui permet a l'interface de
    proposer un choix de moteur sans que la liste des voix ne mente -- une voix
    affichee correspond toujours a un moteur present et a un modele installe.
    """

    id: str
    label: str
    language: str = ""
    engine: str = "system"
    quality: str = ""
    gender: str = ""

    @property
    def is_french(self) -> bool:
        return (self.language or "").lower().startswith("fr")

    @property
    def display_label(self) -> str:
        flag = "🇫🇷 " if self.is_french else ""
        return f"{flag}{self.label}"


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
                   volume: float = 1.0, sentence_pause_s: float = 0.0,
                   params=None, on_progress=None, cancel_token=None) -> str:
        # `params`, `on_progress` et `cancel_token` existent pour Chatterbox,
        # dont une generation dure et se regle. Ici il n'y a rien a regler et
        # rien a suivre : une voix du systeme rend la main en quelques
        # secondes. Les accepter sans s'en servir garde UNE seule signature
        # pour les trois moteurs.
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
    """Voix neuronales Piper, executees LOCALEMENT.

    Deux facons d'executer Piper, cherchees dans cet ordre :

    1. la bibliotheque Python `piper` quand elle est installee -- generation
       dans le processus, sans binaire externe ni fichier temporaire ;
    2. l'executable `piper` s'il est present (variable PIPER_BIN, PATH, ou a
       cote des modeles) -- lance comme un programme separe.

    Aucune des deux n'est obligatoire : sans l'une ni l'autre, le moteur se
    declare indisponible et l'interface propose de l'installer, plutot que
    d'afficher des voix qui echoueraient au moment de generer.

    UN PIEGE REEL, CORRIGE ICI. Les configurations des voix officielles
    declarent la langue espeak "fr-fr", que la donnee espeak livree avec la
    bibliotheque Python REFUSE ("Failed to set voice: fr-fr") ; elle attend
    "fr". Verifie sur machine. `_espeak_voice()` retombe donc sur le code de
    langue de base quand le code complet est refuse.
    """

    name = "piper"

    def __init__(self, binary: str | None = None, models_dir=None):
        self._binary = binary if binary is not None else self._find_binary()
        self._models_dir = Path(models_dir) if models_dir else None
        self._loaded = {}

    # ------------------------------------------------------------ presence
    @staticmethod
    def _find_binary() -> str:
        """Programme piper : variable d'environnement, PATH, puis installation
        locale.

        DEFAUT REEL CORRIGE : la recherche locale ne regardait QUE la racine du
        dossier d'installation. L'archive officielle range son contenu dans un
        sous-dossier "piper/", donc le programme s'y trouvait et n'etait jamais
        vu -- l'application proposait encore d'installer un moteur deja
        installe, et les voix telechargees restaient inutilisables. La
        recherche recursive de piper_models.engine_binary() est desormais la
        seule, partagee avec le gestionnaire de voix : deux recherches
        differentes, c'est exactement ce qui a produit le desaccord.
        """
        candidate = os.environ.get("PIPER_BIN") or shutil.which("piper") or ""
        if candidate and Path(candidate).exists():
            return candidate
        from voice_studio import piper_models

        found = piper_models.engine_binary()
        return str(found) if found else ""

    @staticmethod
    def library_available() -> bool:
        try:
            import piper  # noqa: F401
        except Exception:
            return False
        return True

    def runtime(self) -> str:
        """« library », « binary » ou « » : ce qui fera reellement tourner Piper."""
        if self.library_available():
            return "library"
        if self._binary and Path(self._binary).exists():
            return "binary"
        return ""

    def available(self) -> bool:
        return bool(self.runtime()) and bool(self.voices())

    def models_dir(self) -> Path:
        from voice_studio import piper_models

        return self._models_dir or piper_models.models_dir()

    # -------------------------------------------------------------- voix
    def voices(self) -> list[Voice]:
        """Uniquement les modeles REELLEMENT installes.

        Le catalogue de telechargement n'apparait pas ici : une voix listee
        dans le choix de voix est une voix utilisable tout de suite.
        """
        from voice_studio import piper_models

        found = []
        directory = self.models_dir()
        for model in sorted(directory.glob("*.onnx")) if directory.is_dir() else []:
            if model.name.endswith(".onnx.json"):
                continue
            key = model.name[: -len(".onnx")]
            if not piper_models.is_installed(key):
                continue
            described = piper_models.describe(key)
            found.append(Voice(id=str(model), label=described.label or key,
                               language=(described.language or key.split("-")[0]).replace("_", "-"),
                               engine=self.name, quality=described.quality,
                               gender=described.gender))
        return found

    def metadata(self) -> dict:
        """De quoi expliquer l'etat du moteur dans l'interface, sans deviner."""
        return {
            "engine": self.name,
            "runtime": self.runtime(),
            "models_dir": str(self.models_dir()),
            "installed": len(self.voices()),
        }

    # ---------------------------------------------------------- synthese
    @staticmethod
    def _espeak_voice(configured: str) -> str:
        """Code de langue espeak reellement accepte par la donnee installee.

        "fr-fr" est refuse par la donnee espeak livree avec piper-tts, "fr"
        passe. On essaie donc le code du modele, puis sa langue de base.
        """
        candidates = [configured]
        if "-" in (configured or ""):
            candidates.append(configured.split("-")[0])
        try:
            from piper.phonemize_espeak import EspeakPhonemizer

            phonemizer = EspeakPhonemizer()
        except Exception:                              # pragma: no cover - piper absent
            return configured
        for candidate in candidates:
            if not candidate:
                continue
            try:
                phonemizer.phonemize(candidate, "test")
                return candidate
            except Exception:
                continue
        return configured

    def _voice_object(self, model_path: str):
        if model_path in self._loaded:
            return self._loaded[model_path]
        from piper import PiperVoice

        if not Path(model_path).is_file():
            raise TtsError("Cette voix n'est plus installée sur cet ordinateur.")
        try:
            voice = PiperVoice.load(model_path)
        except Exception as error:
            raise TtsError(
                "Cette voix n'a pas pu être chargée : le fichier est peut-être "
                "incomplet ou abîmé. Supprime-la puis réinstalle-la depuis "
                f"« Gérer les voix ». Détail : {type(error).__name__}"
            ) from error
        voice.config.espeak_voice = self._espeak_voice(voice.config.espeak_voice)
        self._loaded[model_path] = voice
        return voice

    def synthesize(self, text: str, out_wav: str, voice_id: str = "", rate: float = 1.0,
                   volume: float = 1.0, sentence_pause_s: float = 0.0,
                   params=None, on_progress=None, cancel_token=None) -> str:
        # Voir SystemVoiceEngine.synthesize : signature commune aux trois
        # moteurs, extras ignores ici.
        runtime = self.runtime()
        if not runtime:
            raise TtsError(
                "Le moteur Piper n'est pas installé sur cet ordinateur. "
                "Ouvre « Gérer les voix » pour l'installer."
            )
        model = voice_id or (self.voices()[0].id if self.voices() else "")
        if not model:
            raise TtsError("Aucune voix Piper n'est installée. "
                           "Ouvre « Gérer les voix » pour en télécharger une.")
        Path(out_wav).parent.mkdir(parents=True, exist_ok=True)

        if runtime == "library":
            self._synthesize_library(text, out_wav, model, rate, volume, sentence_pause_s)
        else:
            self._synthesize_binary(text, out_wav, model, rate, sentence_pause_s)

        if not Path(out_wav).is_file() or Path(out_wav).stat().st_size < 128:
            raise TtsError("La voix n'a produit aucun son. Réinstalle la voix, "
                           "ou essaie une autre voix.")
        return out_wav

    def _synthesize_library(self, text, out_wav, model, rate, volume, sentence_pause_s) -> None:
        import wave

        from piper import SynthesisConfig

        voice = self._voice_object(model)
        # Vitesse et volume sont geres PAR LE MOTEUR : length_scale allonge ou
        # raccourcit la parole a la synthese, sans reechantillonnage ni passage
        # par ffmpeg, donc sans artefact. normalize_audio evite la saturation.
        config = SynthesisConfig(
            length_scale=1.0 / clamp(rate, MIN_RATE, MAX_RATE),
            volume=clamp(volume, MIN_VOLUME, MAX_VOLUME),
            normalize_audio=True,
        )
        pause = clamp(sentence_pause_s, 0.0, MAX_PAUSE_S)
        if pause <= 0:
            with wave.open(out_wav, "wb") as handle:
                voice.synthesize_wav(text, handle, syn_config=config)
            return

        pieces = []
        for index, sentence in enumerate(split_sentences(text)):
            piece = f"{out_wav}.part{index}.wav"
            with wave.open(piece, "wb") as handle:
                voice.synthesize_wav(sentence, handle, syn_config=config)
            pieces.append(piece)
        if not pieces:
            raise TtsError("Il n'y a pas de texte à lire.")
        concat_wavs(pieces, out_wav, silence_s=pause)
        for piece in pieces:
            Path(piece).unlink(missing_ok=True)

    def _synthesize_binary(self, text, out_wav, model, rate, sentence_pause_s) -> None:
        command = [self._binary, "--model", model, "--output_file", out_wav]
        if rate and rate != 1.0:
            command += ["--length_scale", f"{1.0 / clamp(rate, MIN_RATE, MAX_RATE):.3f}"]
        if sentence_pause_s:
            command += ["--sentence_silence",
                        f"{clamp(sentence_pause_s, 0.0, MAX_PAUSE_S):.2f}"]
        try:
            subprocess.run(command, input=(text or "").encode("utf-8"),
                           capture_output=True, check=True)
        except subprocess.CalledProcessError as error:  # pragma: no cover - depend de Piper
            raise TtsError(
                "Piper n'a pas pu générer la voix. "
                f"Détail : {(error.stderr or b'').decode('utf-8', 'replace')[:200]}"
            ) from error


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
    """Moteurs presents sur cette machine, le systeme d'abord.

    Le systeme d'abord parce qu'il est toujours la : sur une installation
    neuve, la generation fonctionne immediatement avec les voix de Windows, et
    Piper vient s'ajouter quand l'utilisateur telecharge une voix.
    """
    return [engine for engine in _known_engines() if engine.available()]


def all_engines() -> list:
    """Tous les moteurs connus, disponibles ou non.

    Sert a l'interface : un moteur indisponible doit pouvoir etre montre AVEC
    la raison, plutot que disparaitre sans explication.
    """
    return list(_known_engines())


def _known_engines() -> tuple:
    """Les trois moteurs, dans l'ordre d'apparition.

    Le systeme d'abord parce qu'il est toujours la ; Chatterbox en dernier
    parce qu'il demande une installation. L'import est fait ici et non en tete
    de fichier : chatterbox_engine importe ce module a l'execution, l'importer
    au chargement ferait un cycle.
    """
    from voice_studio.chatterbox_engine import ChatterboxEngine

    return (SystemVoiceEngine(), PiperEngine(), ChatterboxEngine())


def chatterbox_name() -> str:
    """Nom du moteur Chatterbox, pour l'interface.

    Une fonction plutot qu'une chaine recopiee dans les ecrans : le jour ou le
    nom change, il ne change qu'ici.
    """
    from voice_studio import chatterbox_catalogue

    return chatterbox_catalogue.ENGINE_NAME


ENGINE_LABELS = {
    "system": "Voix du système (Windows)",
    "piper": "Piper — voix locales",
    "chatterbox": "Chatterbox — voix expressive",
}


def available_voices(engine_name: str = "") -> list[Voice]:
    """Voix utilisables, francaises en tete.

    `engine_name` restreint a un moteur. Liste vide = rien d'installe pour ce
    moteur : l'interface l'annonce et desactive la generation, au lieu
    d'offrir un bouton qui echouera.
    """
    found: list[Voice] = []
    for engine in engines():
        if engine_name and engine.name != engine_name:
            continue
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


def cache_dir() -> Path:
    from core.paths import user_data_dir

    path = user_data_dir() / "voice_studio_data" / "tts_cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def params_signature(params) -> str:
    """Empreinte des reglages propres a un moteur, vide s'il n'en a pas.

    Chatterbox en a (expressivite, guidage, temperature, graine, voix de
    reference) et ils changent le son : deux generations qui ne different que
    par l'expressivite ne doivent jamais se renvoyer le meme fichier.
    """
    if not params:
        return ""
    try:
        from voice_studio import chatterbox_catalogue as chatterbox

        if isinstance(params, chatterbox.Params):
            return chatterbox.signature(params)
        if isinstance(params, dict):
            return chatterbox.signature(chatterbox.params_for(**params))
    except Exception as error:                         # pragma: no cover - garde-fou
        logger.warning(f"Réglages de moteur non signés ({error}).")
    return repr(params)


def cache_key(text: str, voice: Voice | None, rate: float, volume: float,
              sentence_pause_s: float, params=None) -> str:
    """Empreinte de TOUT ce qui change le son produit.

    Le moteur et la voix en font partie : le meme texte lu par Hortense et par
    une voix Piper ne donne evidemment pas le meme fichier. Les reglages du
    moteur aussi, depuis Chatterbox.
    """
    import hashlib

    parts = [text or "", voice.engine if voice else "", voice.id if voice else "",
             f"{clamp(rate, MIN_RATE, MAX_RATE):.3f}",
             f"{clamp(volume, MIN_VOLUME, MAX_VOLUME):.3f}",
             f"{clamp(sentence_pause_s, 0.0, MAX_PAUSE_S):.2f}",
             params_signature(params)]
    return hashlib.sha256("\u0000".join(parts).encode("utf-8")).hexdigest()[:32]


def synthesize(text: str, out_wav: str, voice: Voice | None = None, rate: float = 1.0,
               volume: float = 1.0, sentence_pause_s: float = 0.0,
               use_cache: bool = True, params=None, on_progress=None,
               cancel_token=None) -> str:
    """Genere le fichier et renvoie son chemin.

    Le cache est garde a part et RECOPIE vers la destination demandee : deux
    demandes identiques ne relancent pas la synthese, mais l'appelant recoit
    toujours le fichier a l'endroit qu'il a choisi.
    """
    if not (text or "").strip():
        raise TtsError("Il n'y a pas de texte à lire.")
    engine = engine_for(voice)

    cached = cache_dir() / f"{cache_key(text, voice, rate, volume, sentence_pause_s, params)}.wav"
    if use_cache and cached.is_file() and cached.stat().st_size > 128:
        Path(out_wav).parent.mkdir(parents=True, exist_ok=True)
        if str(cached) != str(out_wav):
            shutil.copyfile(cached, out_wav)
        return out_wav

    engine.synthesize(text, out_wav, voice_id=voice.id if voice else "",
                      rate=rate, volume=volume, sentence_pause_s=sentence_pause_s,
                      params=params, on_progress=on_progress, cancel_token=cancel_token)
    if use_cache:
        try:
            shutil.copyfile(out_wav, cached)
        except OSError as error:                       # pragma: no cover - disque plein
            logger.warning(f"Voix non mise en cache : {error}")
    return out_wav


PREVIEW_MAX_CHARS = 240


def preview_text(text: str, max_chars: int = PREVIEW_MAX_CHARS) -> str:
    """Extrait servant d'apercu.

    On coupe a la fin d'une phrase quand c'est possible : un apercu qui
    s'arrete au milieu d'un mot ne dit rien de la voix.
    """
    cleaned = (text or "").strip()
    if len(cleaned) <= max_chars:
        return cleaned
    head = cleaned[:max_chars]
    for mark in (". ", "! ", "? ", ", "):
        cut = head.rfind(mark)
        if cut > max_chars // 3:
            return head[: cut + 1].strip()
    return head.rsplit(" ", 1)[0].strip() + "…"


def clear_cache() -> int:
    """Vide le cache des voix generees. Renvoie le nombre de fichiers effaces."""
    removed = 0
    for path in cache_dir().glob("*.wav"):
        path.unlink()
        removed += 1
    return removed
