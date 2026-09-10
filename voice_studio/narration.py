"""Le script de narration : le texte que la voix va lire.

Module PUR : il lit un fichier, nettoie des espaces, compte des mots et estime
une duree. Aucune synthese, aucun reseau, aucune ecriture.

Deux regles qui expliquent tout le module :

- LE TEXTE N'EST JAMAIS REECRIT. On enleve les espaces en trop et les lignes
  vides surnumeraires, rien d'autre : pas de correction, pas de ponctuation
  ajoutee, pas de reformulation. Le script est de l'utilisateur (ou vient du
  bloc de reecriture, qui a deja fait ce travail) ; le retoucher ici ferait dire
  a la voix autre chose que ce qu'il a ecrit.
- LA DUREE ESTIMEE EST ANNONCEE COMME UNE ESTIMATION. Elle sert a prevenir
  qu'un script de 900 mots ne tiendra pas sur une video de 30 secondes, avant
  de generer quoi que ce soit. La duree REELLE, elle, n'est jamais estimee :
  elle est mesuree sur le fichier audio produit (voir video_service.py).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# Extensions acceptees a l'import. Volontairement du texte seul : un .docx ou
# un .pdf demanderait une dependance de plus pour lire un fichier que
# l'utilisateur peut copier-coller en deux secondes.
SCRIPT_EXTENSIONS = (".txt", ".md", ".text")

# Encodages essayes dans l'ordre. UTF-8 d'abord (le cas normal), puis les deux
# encodages qu'un Bloc-notes Windows produit encore aujourd'hui : un script
# ecrit sous Windows et refuse pour un accent serait absurde.
_ENCODINGS = ("utf-8-sig", "utf-8", "cp1252", "latin-1")

_MULTI_SPACE = re.compile(r"[ \t ]+")
_MULTI_BLANK = re.compile(r"\n{3,}")


class ScriptError(Exception):
    """Message deja ecrit pour un humain."""


@dataclass(frozen=True)
class ScriptStats:
    words: int
    characters: int
    estimated_s: float

    @property
    def is_empty(self) -> bool:
        return self.words == 0


def clean_script(text: str) -> str:
    """Espaces normalises, rien de plus (voir l'entete du module)."""
    if not text:
        return ""
    lines = [_MULTI_SPACE.sub(" ", line).strip() for line in text.replace("\r\n", "\n").split("\n")]
    joined = "\n".join(lines)
    return _MULTI_BLANK.sub("\n\n", joined).strip()


def read_script_file(path: str) -> str:
    """Contenu d'un fichier de script, nettoye des espaces.

    Les erreurs sont traduites : un utilisateur qui a choisi un PDF doit lire
    pourquoi ca ne marche pas, pas une exception Python.
    """
    file = Path(path)
    if not file.is_file():
        raise ScriptError(f"Ce fichier n'existe pas : {path}")
    if file.suffix.lower() not in SCRIPT_EXTENSIONS:
        raise ScriptError(
            f"Format de fichier non géré : {file.suffix or 'sans extension'}. "
            "Importe un fichier texte (.txt, .md), ou colle directement ton script.")

    for encoding in _ENCODINGS:
        try:
            raw = file.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
        except OSError as error:
            raise ScriptError(f"Impossible de lire ce fichier : {error}") from error
        cleaned = clean_script(raw)
        if not cleaned:
            raise ScriptError("Ce fichier est vide : il n'y a rien à lire à voix haute.")
        return cleaned

    raise ScriptError(
        "Le contenu de ce fichier n'est pas du texte lisible (encodage non reconnu).")


def word_count(text: str) -> int:
    return len([w for w in re.split(r"\s+", (text or "").strip()) if w])


def estimate_duration_s(text: str, rate: float = 1.0) -> float:
    """Duree APPROXIMATIVE de la narration, en secondes.

    Le debit de reference est celui de voice_studio/tts.py
    (BASE_WORDS_PER_MINUTE) : une seule valeur dans tout le projet, pour que
    l'estimation affichee ici et celle affichee ailleurs ne se contredisent
    jamais. La vitesse choisie divise le temps, comme elle le fait vraiment
    dans les deux moteurs.
    """
    from voice_studio.tts import BASE_WORDS_PER_MINUTE, MAX_RATE, MIN_RATE, clamp

    words = word_count(text)
    if not words:
        return 0.0
    speed = clamp(float(rate or 1.0), MIN_RATE, MAX_RATE)
    return (words / float(BASE_WORDS_PER_MINUTE)) * 60.0 / speed


def stats(text: str, rate: float = 1.0) -> ScriptStats:
    cleaned = (text or "").strip()
    return ScriptStats(words=word_count(cleaned), characters=len(cleaned),
                       estimated_s=estimate_duration_s(cleaned, rate))
