"""Marqueurs linguistiques de l'analyse, et leurs valeurs par defaut.

Les listes vivent dans config/clip_analysis.json pour etre ajustables sans
recompiler. Elles sont DOUBLEES ici pour une raison simple : une installation
Windows dont le fichier de config a ete supprime ou abime doit continuer a
analyser des clips. `load()` fusionne les deux, le fichier ayant le dernier mot
sur chaque cle qu'il definit.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from core.config_loader import load_clip_analysis_config
from core.text_utils import normalize

DEFAULT_EMOTIONS: dict[str, tuple[str, ...]] = {
    "rire": ("ahah", "haha", "hahaha", "mdr", "ptdr", "lol", "j'ai ri", "je rigole",
             "trop drole", "mort de rire", "explose de rire"),
    "surprise": ("quoi", "hein", "attends", "attendez", "serieux", "serieusement",
                 "c'est quoi ca", "nan mais", "pas possible", "j'y crois pas",
                 "incroyable", "wtf", "oh mon dieu"),
    "excitation": ("allez", "let's go", "on y va", "trop bien", "enorme", "magnifique",
                   "parfait", "genial", "c'est parti"),
    "colere": ("putain", "merde", "n'importe quoi", "ras le bol", "enerve",
               "insupportable", "c'est honteux"),
    "incomprehension": ("je comprends pas", "j'ai pas compris", "comment ca",
                        "pourquoi", "ca veut dire quoi", "attends quoi"),
    "tension": ("doucement", "fais attention", "attention", "on va perdre",
                "c'est chaud", "ca passe pas", "dernier", "plus qu'une"),
    "revelation": ("en fait", "en realite", "je viens de comprendre", "j'ai compris",
                   "c'etait ca", "voila pourquoi", "du coup c'est"),
}

EMOTION_LABELS = {
    "rire": "moment drôle",
    "surprise": "surprise",
    "excitation": "excitation",
    "colere": "colère",
    "incomprehension": "incompréhension",
    "tension": "moment tendu",
    "revelation": "révélation",
}

DEFAULT_SUPERLATIVES = (
    "jamais", "toujours", "meilleur", "meilleure", "pire", "plus grand", "plus gros",
    "incroyable", "enorme", "dingue", "fou", "folle", "record", "historique",
    "premier", "premiere", "unique",
)

DEFAULT_NEGATIONS = ("ne", "pas", "jamais", "rien", "aucun", "aucune", "personne",
                     "plus jamais", "impossible")

DEFAULT_STRONG_EXPRESSIONS = (
    "c'est pas possible", "je n'en reviens pas", "regardez ca", "attendez de voir",
    "vous allez voir", "il s'est passe", "ce qui vient de se passer", "je vous jure",
)

DEFAULT_THRESHOLDS = {
    "short_sentence_words": 5,
    "long_sentence_words": 18,
    "silence_gap_s": 1.0,
    "rhythm_change_ratio": 0.35,
    "repetition_min_occurrences": 3,
}

DEFAULT_TWITCH_HASHTAGS = ("#Twitch", "#Clip")
DEFAULT_MAX_HASHTAGS = 6


@dataclass(frozen=True)
class Lexicon:
    """Marqueurs prets a l'emploi : deja normalises (accents et casse retires).

    La normalisation se fait une seule fois, ici, avec la meme fonction que le
    reste du projet (core.text_utils.normalize) -- comparer du texte transcrit a
    des marqueurs accentues ne trouverait presque rien.
    """

    emotions: dict = field(default_factory=dict)
    superlatives: tuple = ()
    negations: tuple = ()
    strong_expressions: tuple = ()
    thresholds: dict = field(default_factory=dict)
    twitch_hashtags: tuple = ()
    max_hashtags: int = DEFAULT_MAX_HASHTAGS

    def threshold(self, name: str) -> float:
        return float(self.thresholds.get(name, DEFAULT_THRESHOLDS[name]))


def _norm_all(values) -> tuple[str, ...]:
    out = []
    for value in values or ():
        n = normalize(str(value))
        if n and n not in out:
            out.append(n)
    return tuple(out)


def load(config: dict | None = None) -> Lexicon:
    """Marqueurs du fichier de config, completes par les valeurs par defaut."""
    data = config if config is not None else load_clip_analysis_config()
    data = data if isinstance(data, dict) else {}

    raw_emotions = data.get("emotions") if isinstance(data.get("emotions"), dict) else {}
    emotions: dict[str, tuple[str, ...]] = {}
    for name, markers in {**{k: list(v) for k, v in DEFAULT_EMOTIONS.items()},
                          **{k: v for k, v in raw_emotions.items() if not k.startswith("_")}}.items():
        if isinstance(markers, (list, tuple)):
            normalized = _norm_all(markers)
            if normalized:
                emotions[name] = normalized

    thresholds = dict(DEFAULT_THRESHOLDS)
    raw_thresholds = data.get("thresholds") if isinstance(data.get("thresholds"), dict) else {}
    for key, value in raw_thresholds.items():
        if key in DEFAULT_THRESHOLDS and isinstance(value, (int, float)):
            thresholds[key] = value

    hashtags = data.get("hashtags") if isinstance(data.get("hashtags"), dict) else {}
    twitch = hashtags.get("twitch_context")
    max_total = hashtags.get("max_total")

    return Lexicon(
        emotions=emotions,
        superlatives=_norm_all(data.get("superlatives") or DEFAULT_SUPERLATIVES),
        negations=_norm_all(data.get("negations") or DEFAULT_NEGATIONS),
        strong_expressions=_norm_all(data.get("strong_expressions") or DEFAULT_STRONG_EXPRESSIONS),
        thresholds=thresholds,
        twitch_hashtags=tuple(twitch) if isinstance(twitch, (list, tuple)) and twitch else DEFAULT_TWITCH_HASHTAGS,
        max_hashtags=int(max_total) if isinstance(max_total, int) and max_total > 0 else DEFAULT_MAX_HASHTAGS,
    )
