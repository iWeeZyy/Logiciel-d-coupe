"""Catalogue Chatterbox : ce que dit config/chatterbox.json, et rien d'autre.

Module PUR au sens ou il ne fait aucun reseau et n'ecrit rien : il lit le
catalogue, borne des valeurs, decoupe un texte. Tout ce qui touche au disque
est dans chatterbox_models.py, tout ce qui lance un processus est dans
chatterbox_runtime.py.

POURQUOI UN CATALOGUE PLUTOT QUE DES CONSTANTES. Le depot officiel bouge : le
commit epingle, la liste des fichiers de poids, les bornes des reglages ont
change au moins une fois entre deux versions. Les mettre dans un fichier de
configuration permet de les corriger sans reconstruire l'application -- et de
lire, en clair, ce sur quoi l'integration repose.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace

from core.logging_setup import get_logger

logger = get_logger()

CONFIG_FILE = "chatterbox.json"
ENGINE_NAME = "chatterbox"

# Repli si le fichier de configuration est absent ou illisible. Volontairement
# minimal : il ne permet PAS de telecharger quoi que ce soit (aucune URL), il
# permet seulement a l'application de demarrer et de dire ce qui manque.
_FALLBACK = {
    "model": {"key": "multilingual-v3", "label": "Chatterbox Multilingual V3",
              "files": [], "languages": ["fr"], "sample_rate": 24000},
    "runtime": {"python_min": [3, 10], "python_max": [3, 13], "packages": [], "source": ""},
    "defaults": {"language": "fr", "preset": "naturel", "seed": 0},
    "presets": {"naturel": {"label": "Naturel", "exaggeration": 0.5,
                            "cfg_weight": 0.5, "temperature": 0.8}},
    "limits": {"max_chars_per_chunk": 320, "exaggeration": [0.25, 2.0],
               "cfg_weight": [0.0, 1.0], "temperature": [0.05, 5.0]},
}


def config() -> dict:
    """Contenu de config/chatterbox.json, ou le repli minimal."""
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


def model_spec() -> dict:
    return config().get("model", {}) or {}


def runtime_spec() -> dict:
    return config().get("runtime", {}) or {}


def limits() -> dict:
    return {**_FALLBACK["limits"], **(config().get("limits", {}) or {})}


def defaults() -> dict:
    return {**_FALLBACK["defaults"], **(config().get("defaults", {}) or {})}


def languages() -> list[str]:
    return list(model_spec().get("languages") or ["fr"])


def supports_language(code: str) -> bool:
    return (code or "").lower()[:2] in languages()


def sample_rate() -> int:
    return int(model_spec().get("sample_rate") or 24000)


def file_urls() -> list[tuple[str, str]]:
    """(nom de fichier, adresse) pour chaque poids du modele.

    Liste vide si le catalogue n'a pas de gabarit d'adresse : mieux vaut ne
    rien proposer que de fabriquer une URL qui n'existe pas.
    """
    spec = model_spec()
    template = spec.get("url_template") or ""
    files = spec.get("files") or []
    if not template or not files:
        return []
    return [(name, template.format(repo_id=spec.get("repo_id", ""),
                                    revision=spec.get("revision", "main"), file=name))
            for name in files]


# ------------------------------------------------------------------ reglages

@dataclass(frozen=True)
class Preset:
    key: str
    label: str
    exaggeration: float
    cfg_weight: float
    temperature: float


@dataclass(frozen=True)
class Params:
    """Ce qui est envoye au modele. Les noms sont ceux du code officiel --
    renommer 'exaggeration' en 'expressivite' ici rendrait la correspondance
    invisible ; c'est l'INTERFACE qui traduit."""

    language: str = "fr"
    exaggeration: float = 0.5
    cfg_weight: float = 0.5
    temperature: float = 0.8
    seed: int = 0                    # 0 = aleatoire, comme l'application officielle
    reference: str = ""              # fichier audio de reference, vide = voix integree
    preset: str = ""

    def to_dict(self) -> dict:
        from dataclasses import asdict

        return asdict(self)


def presets() -> list[Preset]:
    raw = config().get("presets", {}) or {}
    out = []
    for key, value in raw.items():
        if key.startswith("_") or not isinstance(value, dict):
            continue
        out.append(Preset(
            key=key,
            label=str(value.get("label") or key),
            exaggeration=float(value.get("exaggeration", 0.5)),
            cfg_weight=float(value.get("cfg_weight", 0.5)),
            temperature=float(value.get("temperature", 0.8)),
        ))
    return out


def preset(key: str) -> Preset | None:
    return next((p for p in presets() if p.key == key), None)


def _clamp(value, bounds, fallback: float) -> float:
    low, high = float(bounds[0]), float(bounds[1])
    try:
        return max(low, min(high, float(value)))
    except (TypeError, ValueError):
        return fallback


def clamp(params: Params) -> Params:
    """Ramene chaque reglage dans les bornes du catalogue.

    Les bornes ne sont pas inventees : ce sont celles de l'application de
    reference du depot officiel (expressivite 0.25 a 2.0, temperature 0.05 a
    5.0). Une valeur hors bornes n'est pas refusee, elle est ramenee -- un
    curseur ne doit jamais faire echouer une generation.
    """
    bounds = limits()
    language = (params.language or "fr").lower()[:2]
    return replace(
        params,
        language=language if supports_language(language) else "fr",
        exaggeration=_clamp(params.exaggeration, bounds["exaggeration"], 0.5),
        cfg_weight=_clamp(params.cfg_weight, bounds["cfg_weight"], 0.5),
        temperature=_clamp(params.temperature, bounds["temperature"], 0.8),
        seed=max(0, int(params.seed or 0)),
    )


def params_for(preset_key: str = "", **overrides) -> Params:
    """Reglages d'un prereglage, eventuellement corriges a la main."""
    base = preset(preset_key) or preset(defaults().get("preset", "")) or None
    params = Params(language=defaults().get("language", "fr"), preset=preset_key)
    if base is not None:
        params = replace(params, exaggeration=base.exaggeration,
                         cfg_weight=base.cfg_weight, temperature=base.temperature,
                         preset=base.key)
    known = set(Params.__dataclass_fields__)
    params = replace(params, **{k: v for k, v in overrides.items()
                                if k in known and v is not None})
    return clamp(params)


def signature(params: Params) -> str:
    """Empreinte des reglages pour la cle du cache audio.

    Tout ce qui change le son y figure, y compris la seed et le fichier de
    reference : deux generations qui ne different que par l'expressivite ne
    doivent jamais se renvoyer le meme fichier.
    """
    params = clamp(params)
    return "|".join([
        ENGINE_NAME,
        model_spec().get("key", ""),
        params.language,
        f"{params.exaggeration:.3f}",
        f"{params.cfg_weight:.3f}",
        f"{params.temperature:.3f}",
        str(params.seed),
        params.reference or "",
    ])


# ---------------------------------------------------------------- decoupage

_SPLIT_INSIDE = re.compile(r"(?<=[,;:])\s+")


def chunks(text: str, max_chars: int | None = None) -> list[str]:
    """Decoupe un script en morceaux generables d'un seul appel.

    LE MODELE A UNE LIMITE REELLE : `max_new_tokens=1000` est code en dur dans
    mtl_tts.py, soit environ 40 s de parole par appel. Au-dela, la fin du texte
    ne serait tout simplement pas prononcee -- silencieusement. D'ou ce
    decoupage, exprime en caracteres parce que c'est ce qu'on peut mesurer
    avant de generer.

    Regles, dans l'ordre : on coupe entre les PHRASES ; si une phrase depasse a
    elle seule, on coupe apres une virgule ou un point-virgule ; en dernier
    recours entre deux mots. JAMAIS a l'interieur d'un mot. Les morceaux remis
    bout a bout redonnent le texte, aux espaces de jointure pres.
    """
    from voice_studio.tts import split_sentences

    limit = int(max_chars or limits()["max_chars_per_chunk"])
    limit = max(40, limit)
    cleaned = " ".join((text or "").split())
    if not cleaned:
        return []

    pieces: list[str] = []
    for sentence in split_sentences(cleaned) or [cleaned]:
        pieces.extend(_split_long(sentence, limit))

    out: list[str] = []
    for piece in pieces:
        if out and len(out[-1]) + 1 + len(piece) <= limit:
            out[-1] = f"{out[-1]} {piece}"
        else:
            out.append(piece)
    return out


def _split_long(sentence: str, limit: int) -> list[str]:
    if len(sentence) <= limit:
        return [sentence]

    parts: list[str] = []
    for clause in _SPLIT_INSIDE.split(sentence):
        if len(clause) <= limit:
            parts.append(clause)
            continue
        # Toujours trop long : on coupe entre deux mots, jamais dedans.
        current = ""
        for word in clause.split():
            candidate = f"{current} {word}".strip()
            if current and len(candidate) > limit:
                parts.append(current)
                current = word
            else:
                current = candidate
        if current:
            parts.append(current)
    return parts
