"""Styles de montage : un seul choix qui positionne tous les autres.

LE PROBLEME. Un clip reussi n'est pas la somme de reglages independants. Des
sous-titres de deux mots tres grands appellent un cadrage serre et des zooms
francs ; un clip narratif appelle l'inverse. Or l'accueil demandait ces
reglages separement -- format, cadrage, style de sous-titres, zooms, delire --
et rien n'empechait d'assembler une combinaison que personne ne voudrait.

CE QU'UN STYLE FAIT, ET CE QU'IL NE FAIT PAS. Il pose une combinaison coherente
d'un coup. Il ne verrouille rien : chaque reglage reste visible et modifiable
apres coup, et toucher a l'un fait repasser le menu sur « Personnalise »
plutot que d'afficher le nom d'un style qui ne decrit plus ce qui va sortir.

UN CHAMP A None VEUT DIRE « N'Y TOUCHE PAS », pas « remets la valeur par
defaut ». C'est ce qui permet a « Sobre » de ne rien dire du format : un clip
sobre se justifie aussi bien en vertical qu'en horizontal, et decider a la
place de l'utilisateur serait une perte d'information, pas un service.

Module PUR : aucune dependance a Qt, au pipeline ni a la configuration. Il
decrit des intentions ; l'interface les applique, le pipeline les subit.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Le style « libre » n'applique rien : c'est l'etat de depart, et celui sur
# lequel on retombe des qu'un reglage est modifie a la main.
FREE_KEY = "libre"
DEFAULT_KEY = FREE_KEY

ASPECT_PORTRAIT = "9:16"
ASPECT_LANDSCAPE = "16:9"
FIT_CROP = "recadrer"
FIT_WHOLE = "entier"


@dataclass(frozen=True)
class Preset:
    """Une intention de montage.

    `delire` est le cran d'intensite, ou None pour couper le delire. Il n'y a
    pas de « n'y touche pas » ici : tout style a un avis sur le delire, c'est
    precisement ce qui les distingue le plus.

    `zoom` surcharge montage.dynamic_zoom ; les cles absentes gardent la valeur
    de config/editing.json, jamais une valeur inventee ici.
    """
    key: str
    label: str
    description: str
    subtitle_style: str | None = None
    aspect: str | None = None
    fit_mode: str | None = None
    delire: str | None = None
    zoom: dict = field(default_factory=dict)

    @property
    def is_free(self) -> bool:
        return self.key == FREE_KEY


PRESETS: list[Preset] = [
    Preset(
        key=FREE_KEY,
        label="Personnalisé",
        description="Chaque réglage reste celui que vous avez choisi. Aucun style n'est appliqué.",
    ),
    Preset(
        key="punchline",
        label="Punchline",
        description="Deux mots très grands, cadrage serré, zooms francs et un délire discret. "
                    "Le format des extraits qui tiennent sur une phrase.",
        subtitle_style="dynamic",
        aspect=ASPECT_PORTRAIT,
        fit_mode=FIT_CROP,
        delire="doux",
        # Des zooms plus marques et plus nombreux que le reglage courant : une
        # punchline vit de ces coups d'accent.
        zoom={"enabled": True, "max_zoom": 1.14, "max_events": 6},
    ),
    Preset(
        key="recit",
        label="Récit",
        description="Phrases lisibles longtemps, image entière conservée, zooms à peine perceptibles, "
                    "aucun délire. Pour un extrait qui se suit, pas qui se claque.",
        subtitle_style="podcast",
        aspect=ASPECT_PORTRAIT,
        # L'image ENTIERE : un recit perd son decor si on le rogne, et le decor
        # fait partie de ce qui se raconte.
        fit_mode=FIT_WHOLE,
        delire=None,
        zoom={"enabled": True, "max_zoom": 1.05, "max_events": 3},
    ),
    Preset(
        key="surligne",
        label="Surligné",
        description="Le mot-clé surligné au marqueur, comme les extraits les plus copiés. "
                    "Cadrage serré, zooms mesurés, aucun délire.",
        subtitle_style="marqueur",
        aspect=ASPECT_PORTRAIT,
        fit_mode=FIT_CROP,
        delire=None,
        zoom={"enabled": True},
    ),
    Preset(
        key="karaoke",
        label="Karaoké",
        description="La phrase entière reste lisible et le mot prononcé se colore au fil de la parole. "
                    "Cadrage serré, zooms mesurés, aucun délire.",
        subtitle_style="karaoke",
        aspect=ASPECT_PORTRAIT,
        fit_mode=FIT_CROP,
        delire=None,
        zoom={"enabled": True},
    ),
    Preset(
        key="neon",
        label="Néon",
        description="Sous-titres à la lueur floutée, cadrage serré, zooms appuyés et un délire discret. "
                    "Un look nocturne, plutôt jeu vidéo et musique.",
        subtitle_style="neon",
        aspect=ASPECT_PORTRAIT,
        fit_mode=FIT_CROP,
        delire="doux",
        zoom={"enabled": True, "max_zoom": 1.10, "max_events": 5},
    ),
    Preset(
        key="chaos",
        label="Chaos",
        description="Tout est poussé : mot qui tremble, zooms nombreux, délire au maximum. "
                    "À réserver aux extraits qui s'y prêtent — ailleurs c'est illisible.",
        subtitle_style="secousse",
        aspect=ASPECT_PORTRAIT,
        fit_mode=FIT_CROP,
        delire="maximum",
        # Le seul style qui descend l'ecart minimal entre deux zooms : ailleurs
        # ce serait de l'agitation, ici c'est le propos.
        zoom={"enabled": True, "max_zoom": 1.18, "max_events": 8, "min_gap_s": 2.5},
    ),
    Preset(
        key="sobre",
        label="Sobre",
        description="Sous-titres discrets en minuscules, aucun zoom, aucun délire, et le format "
                    "reste celui que vous avez choisi. Pour un extrait informatif.",
        subtitle_style="minimal",
        # Volontairement muet sur le format : voir l'en-tete du module.
        aspect=None,
        fit_mode=None,
        delire=None,
        zoom={"enabled": False},
    ),
]

_BY_KEY = {preset.key: preset for preset in PRESETS}


def keys() -> list[str]:
    return [preset.key for preset in PRESETS]


def get(key: str) -> Preset:
    """Le style demande, ou « Personnalise » si la cle est inconnue.

    Jamais une exception : une cle perimee dans un reglage enregistre ne doit
    pas empecher la page de s'ouvrir, elle doit seulement ne rien appliquer.
    """
    return _BY_KEY.get(str(key or ""), _BY_KEY[FREE_KEY])


def editing_overrides(key: str) -> dict:
    """Surcharges de config/editing.json portees par ce style.

    Seuls le delire et les zooms en font partie : un style de montage n'a pas
    a couper les sous-titres, le cadrage intelligent ou les miniatures, qui
    sont des mecanismes et non des partis pris esthetiques.
    """
    preset = get(key)
    if preset.is_free:
        return {}
    overrides: dict = {"delire": {"enabled": preset.delire is not None}}
    if preset.delire is not None:
        overrides["delire"]["level"] = preset.delire
    if preset.zoom:
        overrides["montage"] = {"dynamic_zoom": dict(preset.zoom)}
    return overrides
