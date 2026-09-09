"""Donnees de la reecriture."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone

# Intensites de reecriture.
STRENGTH_LIGHT = "legere"
STRENGTH_NATURAL = "naturelle"
STRENGTH_CREATIVE = "creative"
STRENGTH_LABELS = {
    STRENGTH_LIGHT: "🟢 Légère — structure conservée, phrases reformulées",
    STRENGTH_NATURAL: "🟠 Naturelle — transitions et rythme retravaillés",
    STRENGTH_CREATIVE: "🔴 Créative — structure réorganisée, mêmes faits",
}

# Styles : ils changent le TON, jamais les faits.
STYLES = ["naturel", "viral", "storytelling", "documentaire", "educatif",
          "dynamique", "conversationnel"]
STYLE_LABELS = {
    "naturel": "Naturel", "viral": "Viral", "storytelling": "Storytelling",
    "documentaire": "Documentaire", "educatif": "Éducatif",
    "dynamique": "Dynamique", "conversationnel": "Conversationnel",
}

# Traitement du hook.
HOOK_EXACT = "exact"
HOOK_REWORDED = "reformule"
HOOK_NEW = "nouveau"
HOOK_LABELS = {
    HOOK_EXACT: "Exact — la phrase d'origine",
    HOOK_REWORDED: "Reformulé — même promesse, autres mots",
    HOOK_NEW: "Nouveau — construit à partir du contenu existant",
}

# Debits de narration, en mots par minute. Modifiables : ce sont des reperes,
# pas des constantes physiques.
NARRATION_SPEEDS = {"lente": 115, "normale": 145, "rapide": 175}
DEFAULT_SPEED = "normale"

CONFIDENCE_HIGH = "elevee"
CONFIDENCE_MEDIUM = "moyenne"
CONFIDENCE_LOW = "faible"
CONFIDENCE_LABELS = {
    CONFIDENCE_HIGH: "🟢 Confiance élevée",
    CONFIDENCE_MEDIUM: "🟠 Confiance moyenne",
    CONFIDENCE_LOW: "🔴 Confiance faible",
}

DISCLAIMER = ("Cette réécriture transforme la formulation du texte. Elle ne "
              "garantit ni l'absence de droits d'auteur, ni l'absence de "
              "similarité avec le contenu source.")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class KeyPoint:
    """Une information reperee dans le transcript, avec sa phrase d'origine.

    La phrase est conservee : c'est elle qui permet de verifier ensuite que
    l'information n'a pas ete deformee, et de la montrer a l'utilisateur.
    """

    text: str
    kind: str = "fait"          # fait, chiffre, date, nom, duree
    sentence: str = ""


@dataclass
class TranscriptAnalysis:
    """Ce que le transcript contient VRAIMENT.

    Chaque champ peut etre vide : un transcript n'a pas forcement de hook, de
    resultat final ni de chiffres. Un element absent est signale comme absent,
    jamais invente.
    """

    hook: str = ""
    payoff: str = ""
    niche: str = ""
    niche_confidence: float = 0.0
    key_points: list = field(default_factory=list)     # KeyPoint
    numbers: list = field(default_factory=list)
    proper_nouns: list = field(default_factory=list)
    repeated_sentences: list = field(default_factory=list)
    sections: dict = field(default_factory=dict)       # nom -> extrait
    word_count: int = 0
    spoken_duration_s: float = 0.0

    @property
    def missing(self) -> list:
        absent = []
        if not self.hook:
            absent.append("hook")
        if not self.payoff:
            absent.append("résultat final")
        if not self.key_points:
            absent.append("informations importantes")
        return absent


@dataclass
class RewriteRequest:
    """La demande, telle que l'utilisateur l'a formulee."""

    source_text: str = ""
    niche: str = ""
    style: str = "naturel"
    strength: str = STRENGTH_NATURAL
    target_duration_s: int = 61
    narration_speed: str = DEFAULT_SPEED
    hook_mode: str = HOOK_REWORDED
    preserve_hook: bool = True
    preserve_information: bool = True
    preserve_structure: bool = True
    preserve_payoff: bool = True
    payoff_reminders: bool = True
    optimize_retention: bool = True
    variants: int = 3

    @property
    def target_words(self) -> int:
        """Nombre de mots vise, deduit du debit choisi.

        Jamais une constante du type "une minute = 150 mots" : le debit est un
        reglage, et la duree estimee se recalcule avec le meme.
        """
        words_per_minute = NARRATION_SPEEDS.get(self.narration_speed,
                                                NARRATION_SPEEDS[DEFAULT_SPEED])
        return max(20, int(round(self.target_duration_s / 60 * words_per_minute)))

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict | None) -> "RewriteRequest":
        data = data or {}
        known = {f.name for f in fields(RewriteRequest)}
        return RewriteRequest(**{k: v for k, v in data.items() if k in known})


@dataclass
class RewriteWarning:
    """Un point a verifier, jamais une accusation."""

    kind: str                    # info_ajoutee, chiffre_perdu, trop_court...
    message: str
    excerpt: str = ""


@dataclass
class RewriteVariant:
    """Une version produite."""

    label: str
    text: str
    style: str = ""
    word_count: int = 0
    estimated_duration_s: float = 0.0
    transformation_index: float = 0.0     # 0 = identique, 1 = tout reformule
    preserved_numbers: int = 0
    total_numbers: int = 0
    preserved_points: int = 0
    total_points: int = 0
    warnings: list = field(default_factory=list)
    confidence: str = CONFIDENCE_MEDIUM

    @property
    def duration_gap_s(self) -> float:
        return 0.0


@dataclass
class RewriteResult:
    """Le resultat complet d'une demande."""

    request: RewriteRequest = field(default_factory=RewriteRequest)
    analysis: TranscriptAnalysis = field(default_factory=TranscriptAnalysis)
    variants: list = field(default_factory=list)       # RewriteVariant
    created_at: str = field(default_factory=utc_now_iso)
    provider: str = ""
    model: str = ""

    @property
    def ok(self) -> bool:
        return any(v.text.strip() for v in self.variants)

    def to_dict(self) -> dict:
        return {
            "request": self.request.to_dict(),
            "analysis": {
                "hook": self.analysis.hook,
                "payoff": self.analysis.payoff,
                "niche": self.analysis.niche,
                "key_points": [asdict(p) for p in self.analysis.key_points],
                "numbers": list(self.analysis.numbers),
                "word_count": self.analysis.word_count,
            },
            "variants": [asdict(v) for v in self.variants],
            "created_at": self.created_at,
            "provider": self.provider,
            "model": self.model,
        }
