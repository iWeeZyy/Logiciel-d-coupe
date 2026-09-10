"""Catalogue et arithmetique du banc d'essai ZeroGPU.

MODULE PUR : aucun reseau, aucune ecriture, aucune interface. Il lit
config/zerogpu.json, borne des reglages, decoupe un script et calcule les
mesures d'un essai. Tout ce qui parle a Hugging Face est dans
zerogpu_client.py ; tout ce qui s'affiche est dans gui/voice_studio/.

CE QU'IL N'EST PAS. Ce n'est pas un quatrieme moteur TTS. Il n'est importe par
aucun module du Voice Studio existant -- ni tts.py, ni les moteurs, ni
video_service.py -- et tests/test_zerogpu.py verifie cette absence
d'importation dans les deux sens. Supprimer l'onglet ZeroGPU se reduit donc a
effacer ces fichiers.

LE DECOUPAGE EST EMPRUNTE, PAS RECRIT. chatterbox_catalogue.chunks() accepte
deja une limite en parametre et applique exactement les regles voulues : on
coupe entre phrases, sinon apres une virgule, en dernier recours entre deux
mots, jamais a l'interieur d'un mot. Seule la limite change (voir
max_chars_per_chunk).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from core.logging_setup import get_logger

logger = get_logger()

CONFIG_FILE = "zerogpu.json"

# Repli si le fichier est absent ou abime. Il ne permet PAS d'appeler quoi que
# ce soit d'invente : l'identifiant du Space y est vide, et l'ecran dira qu'il
# manque, plutot que d'appeler une adresse au hasard.
_FALLBACK = {
    "space": {"id": "", "api_name": None, "parameters": []},
    "limits": {"max_chars_per_chunk": 300, "queue_timeout_s": 600,
               "gpu_duration_hint_s": 120, "max_retries": 2, "retry_pause_s": 20},
    "defaults": {"language": "fr", "exaggeration": 0.5, "cfg_weight": 0.5,
                 "temperature": 0.8, "seed": 0},
    "quota": {},
}


def config() -> dict:
    """Contenu de config/zerogpu.json, ou le repli minimal."""
    from core.config_loader import CONFIG_DIR

    path = CONFIG_DIR / CONFIG_FILE
    if not path.is_file():
        return _FALLBACK
    try:
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else _FALLBACK
    except (ValueError, OSError) as error:             # pragma: no cover - fichier abime
        logger.warning(f"config/{CONFIG_FILE} illisible ({error}) : valeurs de repli.")
        return _FALLBACK


def space_spec() -> dict:
    return config().get("space", {}) or {}


def limits() -> dict:
    merged = dict(_FALLBACK["limits"])
    merged.update(config().get("limits", {}) or {})
    return merged


def defaults() -> dict:
    merged = dict(_FALLBACK["defaults"])
    merged.update(config().get("defaults", {}) or {})
    return merged


def quota_note() -> dict:
    return config().get("quota", {}) or {}


def space_id() -> str:
    return str(space_spec().get("id") or "").strip()


# ------------------------------------------------------------------ reglages
@dataclass(frozen=True)
class Params:
    """Les memes reglages que le Chatterbox local, volontairement.

    Comparer un GPU distant et un processeur local n'a de sens qu'a reglages
    identiques : une voix plus expressive d'un cote fausserait la comparaison
    de qualite autant que de vitesse.
    """

    language: str = "fr"
    exaggeration: float = 0.5
    cfg_weight: float = 0.5
    temperature: float = 0.8
    seed: int = 0
    reference: str = ""


def params_for(**values) -> Params:
    base = defaults()
    return clamp(Params(
        language=str(values.get("language") or base["language"]),
        exaggeration=float(values.get("exaggeration", base["exaggeration"])),
        cfg_weight=float(values.get("cfg_weight", base["cfg_weight"])),
        temperature=float(values.get("temperature", base["temperature"])),
        seed=int(values.get("seed", base["seed"]) or 0),
        reference=str(values.get("reference") or ""),
    ))


def clamp(params: Params) -> Params:
    """Bornes reprises du Chatterbox local : meme modele, memes limites."""
    from voice_studio import chatterbox_catalogue

    bounds = chatterbox_catalogue.limits()

    def _fit(value: float, key: str, low: float, high: float) -> float:
        span = bounds.get(key) or [low, high]
        return max(float(span[0]), min(float(span[1]), float(value)))

    from dataclasses import replace
    return replace(
        params,
        exaggeration=_fit(params.exaggeration, "exaggeration", 0.25, 2.0),
        cfg_weight=_fit(params.cfg_weight, "cfg_weight", 0.0, 1.0),
        temperature=_fit(params.temperature, "temperature", 0.05, 5.0),
        seed=max(0, int(params.seed)),
    )


# ------------------------------------------------------------- decoupage
def chunks(text: str) -> list[str]:
    """Decoupe pour ZeroGPU, avec la limite de CE Space.

    300 caracteres, et ce n'est pas de la prudence : multilingual_app.py, dans
    le depot officiel, fait `text_input[:300]` sans le dire. Un morceau plus
    long perdrait sa fin en silence -- exactement la faute que le decoupage
    existe pour eviter.
    """
    from voice_studio import chatterbox_catalogue

    return chatterbox_catalogue.chunks(text, max_chars=limits()["max_chars_per_chunk"])


def word_count(text: str) -> int:
    """Mots au sens ou un humain les compte, pas au sens de str.split() seul :
    un tiret ou une apostrophe ne cree pas un mot de plus."""
    return len([token for token in re.split(r"\s+", (text or "").strip()) if token])


# --------------------------------------------------------------- mesures
@dataclass(frozen=True)
class Measure:
    """Un essai, chiffre. Tout est mesure ; rien n'est estime.

    `queue_s` et `gpu_s` sont separes parce qu'ils ne disent pas la meme chose :
    l'attente depend de la charge de Hugging Face a cet instant et du niveau de
    compte, la generation depend du modele et du texte. Les confondre rendrait
    la comparaison avec le Chatterbox local trompeuse -- le local n'a pas de
    file d'attente.
    """

    chars: int = 0
    words: int = 0
    chunks: int = 0
    audio_s: float = 0.0
    queue_s: float = 0.0
    gpu_s: float = 0.0
    total_s: float = 0.0

    @property
    def rtf(self) -> float | None:
        """Temps de generation divise par la duree d'audio. Plus petit vaut
        mieux : 0,26 signifie qu'une minute de parole coute seize secondes.

        None quand la duree d'audio est inconnue ou nulle : diviser par zero
        pour afficher un nombre serait une invention."""
        if self.audio_s <= 0 or self.gpu_s <= 0:
            return None
        return self.gpu_s / self.audio_s

    @property
    def rtf_total(self) -> float | None:
        """Le meme rapport, attente comprise. C'est celui-la que l'utilisateur
        subit reellement."""
        if self.audio_s <= 0 or self.total_s <= 0:
            return None
        return self.total_s / self.audio_s


def summarize(measure: Measure) -> str:
    """La phrase que l'utilisateur lit, dans la forme qu'il a demandee :
    « 500 mots -> 3 min 20 d'audio -> 52 s de génération -> RTF 0,26 »."""
    from voice_studio.voice_progress import format_duration

    parts = [f"{measure.words} mots", f"{format_duration(measure.audio_s)} d'audio",
             f"{format_duration(measure.gpu_s)} de génération"]
    rtf = measure.rtf
    if rtf is not None:
        parts.append(f"RTF {rtf:.2f}".replace(".", ","))
    return " → ".join(parts)
