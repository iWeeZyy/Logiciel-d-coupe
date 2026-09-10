"""Ou en est la generation de la voix, et combien de temps il reste.

MODULE PUR : pas de Qt, pas de disque, pas de reseau. L'horloge entre par
parametre. Tout est donc verifiable sans lancer l'application, comme
production.py, staff.py ou align.py.

CE QU'IL RESOUT. Chatterbox lit le texte par morceaux, sur le processeur.
Une narration d'une minute demande des dizaines de secondes, parfois
davantage, et l'ecran n'affichait qu'une ligne de texte : « Génération de la
narration… 3/12 ». On ne savait ni si la barre avancait, ni quand cela
finirait.

LA REGLE, LA MEME QUE PARTOUT ICI : rien n'est invente. Une duree restante
n'est affichee que si elle repose sur une mesure -- celle de la generation en
cours des qu'un morceau est termine, ou celle de la generation precedente sur
CETTE machine, relue des reglages. Tant qu'aucune des deux n'existe (premiere
generation apres l'installation), la barre reste indeterminee et l'ecran
affiche le temps ecoule, qui est un fait, plutot qu'une estimation qui n'en
serait pas une.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# Au-dela, la barre attend l'evenement de fin : afficher 100 % alors que le
# fichier n'est pas encore ecrit ferait mentir la barre sur sa derniere seconde.
MAX_FRACTION = 0.99


@dataclass(frozen=True)
class Report:
    """Ce que l'ecran doit montrer maintenant."""

    fraction: Optional[float]   # None -> barre indeterminee (aucune mesure)
    label: str
    phase: str                  # "attente" | "chargement" | "narration" | "fini"


def format_duration(seconds: float) -> str:
    """« 12 s », « 1 min 20 », « 3 min ». Jamais « 0 min 07 s »."""
    seconds = max(0, int(round(seconds)))
    if seconds < 60:
        return f"{seconds} s"
    minutes, rest = divmod(seconds, 60)
    if rest == 0:
        return f"{minutes} min"
    return f"{minutes} min {rest:02d}"


class GenerationProgress:
    """Traduit les evenements du moteur en (fraction, libelle).

    `previous_load_s` et `previous_rate` viennent de la generation precedente
    sur la meme machine. Ce sont des mesures, pas des constantes ecrites en
    dur : sur un processeur lent elles seront grandes, et l'estimation le sera
    aussi. Zero signifie « jamais mesure », et l'estimation se tait.
    """

    def __init__(self, previous_load_s: float = 0.0, previous_rate: float = 0.0):
        self.previous_load_s = max(0.0, float(previous_load_s or 0.0))
        self.previous_rate = max(0.0, float(previous_rate or 0.0))
        self._t0: Optional[float] = None
        self._phase = "attente"
        self._label = "Démarrage de Chatterbox…"
        self._total_chars = 0
        self._total_chunks = 0
        self._chunk_index = 0
        self._load_s = 0.0            # mesure de CETTE generation
        self._chars_done = 0
        self._gen_seconds = 0.0       # temps passe sur les morceaux TERMINES
        self._floor = 0.0             # la barre ne recule jamais
        self._final: Optional[str] = None

    # ------------------------------------------------------------ mesures
    @property
    def measured_load_s(self) -> float:
        """A retenir pour la prochaine generation. 0 si non mesure."""
        return self._load_s

    @property
    def measured_rate(self) -> float:
        """Secondes par caractere, mesurees sur les morceaux termines."""
        if self._chars_done <= 0 or self._gen_seconds <= 0:
            return 0.0
        return self._gen_seconds / self._chars_done

    # ------------------------------------------------------------ entrees
    def start(self, now: float) -> Report:
        self._t0 = now
        return self._report(now)

    def event(self, payload: dict, now: float) -> Report:
        kind = (payload or {}).get("event") or ""
        if self._t0 is None:
            self._t0 = now

        if kind == "plan":
            self._total_chunks = int(payload.get("chunks") or 0)
            self._total_chars = int(payload.get("chars") or 0)
        elif kind == "loading":
            self._phase = "chargement"
        elif kind == "loaded":
            self._load_s = float(payload.get("seconds") or 0.0)
            self._phase = "narration"
        elif kind == "chunk":
            self._phase = "narration"
            self._chunk_index = int(payload.get("index") or 0)
            self._total_chunks = int(payload.get("total") or self._total_chunks)
        elif kind == "chunk_done":
            self._chars_done += int(payload.get("chars") or 0)
            self._gen_seconds += float(payload.get("seconds") or 0.0)
        elif kind == "done":
            self._phase = "fini"
            self._final = (f"Voix générée en {format_duration(payload.get('seconds') or 0)} "
                           f"({payload.get('duration', '?')} s d'audio).")
        return self._report(now)

    def tick(self, now: float) -> Report:
        """Appele par l'interface entre deux evenements : c'est ce qui fait
        avancer la barre pendant un morceau long, sans rien inventer -- le
        temps ecoule, lui, avance vraiment."""
        return self._report(now)

    # ------------------------------------------------------------- sortie
    def _elapsed(self, now: float) -> float:
        return max(0.0, now - self._t0) if self._t0 is not None else 0.0

    def _estimate_total(self) -> float:
        """Duree totale attendue, ou 0 si aucune mesure ne la soutient."""
        load = self._load_s or self.previous_load_s
        rate = self.measured_rate or self.previous_rate
        if rate <= 0 or self._total_chars <= 0:
            return 0.0
        return load + self._total_chars * rate

    def _report(self, now: float) -> Report:
        elapsed = self._elapsed(now)

        if self._phase == "fini":
            self._floor = 1.0
            return Report(1.0, self._final or "Terminé.", "fini")

        total = self._estimate_total()
        fraction: Optional[float] = None
        remaining_text = ""
        if total > 0:
            fraction = min(MAX_FRACTION, elapsed / total)
            fraction = max(self._floor, fraction)
            self._floor = fraction
            left = total - elapsed
            remaining_text = (f" · encore {format_duration(left)}" if left >= 1
                              else " · bientôt terminé")

        if self._phase == "chargement":
            label = f"Chargement du modèle Chatterbox… {format_duration(elapsed)}"
        elif self._phase == "narration":
            if self._chunk_index and self._total_chunks:
                label = (f"Narration : morceau {self._chunk_index}/{self._total_chunks}"
                         f" · {format_duration(elapsed)}")
            else:
                label = f"Narration en cours… {format_duration(elapsed)}"
        else:
            label = "Démarrage de Chatterbox…"

        return Report(fraction, label + remaining_text, self._phase)
