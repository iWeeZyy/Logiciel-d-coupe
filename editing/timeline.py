"""EditList (EDL) : la liste des segments de la video SOURCE reellement
conserves dans un clip, et la conversion temps source <-> temps de sortie.

C'est la piece centrale du montage automatique. Des qu'un silence ou une
hesitation est coupe (phase C), la timeline de sortie n'est plus celle de la
source : les sous-titres, la trajectoire de cadrage et les zooms doivent tous
etre recales. Faire ce recalage a plusieurs endroits differents serait le moyen
le plus sur de desynchroniser les sous-titres -- d'ou un seul objet, ici, dont
tous les modules aval consomment le temps de sortie.

Un clip sans montage est simplement une EditList a un seul segment
(`EditList.identity(...)`, `is_identity == True`) : le meme code marche dans
les deux cas, et ffmpeg n'a pas besoin de trim/concat dans ce cas-la.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.models import Word

_EPS = 1e-6


@dataclass(frozen=True)
class Cut:
    """Un intervalle de la video source qui est conserve."""

    source_start: float
    source_end: float

    @property
    def duration(self) -> float:
        return max(0.0, self.source_end - self.source_start)

    def to_dict(self) -> dict:
        return {"source_start": round(self.source_start, 4), "source_end": round(self.source_end, 4)}

    @staticmethod
    def from_dict(d: dict) -> "Cut":
        return Cut(source_start=float(d["source_start"]), source_end=float(d["source_end"]))


@dataclass(frozen=True)
class EditList:
    """Segments conserves, tries et disjoints. Le temps de sortie est la
    concatenation de leurs durees, dans l'ordre."""

    cuts: tuple[Cut, ...]

    # ---------- Construction ----------

    @staticmethod
    def identity(start: float, end: float) -> "EditList":
        """Aucun montage : un seul segment, de start a end."""
        return EditList.from_ranges([(start, end)])

    @staticmethod
    def from_ranges(ranges) -> "EditList":
        """Normalise n'importe quelle liste de (start, end) : les intervalles
        vides sont ignores, les autres tries et fusionnes s'ils se chevauchent
        ou se touchent. Aucun appelant n'a donc a se soucier de l'ordre ou des
        doublons."""
        clean = [(float(a), float(b)) for a, b in ranges if float(b) - float(a) > _EPS]
        clean.sort(key=lambda r: r[0])

        merged: list[list[float]] = []
        for start, end in clean:
            if merged and start <= merged[-1][1] + _EPS:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])

        return EditList(cuts=tuple(Cut(a, b) for a, b in merged))

    @staticmethod
    def keeping(source_start: float, source_end: float, removed) -> "EditList":
        """[source_start, source_end] prive des intervalles `removed`.
        C'est la forme utilisee par la suppression des silences/hesitations."""
        kept: list[tuple[float, float]] = []
        cursor = float(source_start)
        for r_start, r_end in sorted((float(a), float(b)) for a, b in removed):
            r_start = max(r_start, source_start)
            r_end = min(r_end, source_end)
            if r_end - r_start <= _EPS:
                continue
            if r_start - cursor > _EPS:
                kept.append((cursor, r_start))
            cursor = max(cursor, r_end)
        if source_end - cursor > _EPS:
            kept.append((cursor, float(source_end)))
        return EditList.from_ranges(kept)

    # ---------- Proprietes ----------

    @property
    def is_empty(self) -> bool:
        return not self.cuts

    @property
    def is_identity(self) -> bool:
        """True quand rien n'a ete coupe -- ffmpeg peut alors se passer de
        trim/concat, ce qui evite un filtre inutile sur la majorite des clips."""
        return len(self.cuts) == 1

    @property
    def source_start(self) -> float:
        return self.cuts[0].source_start if self.cuts else 0.0

    @property
    def source_end(self) -> float:
        return self.cuts[-1].source_end if self.cuts else 0.0

    @property
    def source_span(self) -> float:
        return max(0.0, self.source_end - self.source_start)

    @property
    def output_duration(self) -> float:
        return sum(c.duration for c in self.cuts)

    @property
    def removed_duration(self) -> float:
        return max(0.0, self.source_span - self.output_duration)

    # ---------- Conversion temporelle ----------

    def contains_source_time(self, t: float) -> bool:
        return any(c.source_start - _EPS <= t <= c.source_end + _EPS for c in self.cuts)

    def to_output_time(self, source_t: float) -> float | None:
        """Temps de sortie correspondant a `source_t`, ou None si cet instant a
        ete coupe (l'appelant decide quoi faire : ignorer un mot, rabattre une
        borne...)."""
        elapsed = 0.0
        for c in self.cuts:
            if source_t < c.source_start - _EPS:
                return None
            if source_t <= c.source_end + _EPS:
                return elapsed + max(0.0, source_t - c.source_start)
            elapsed += c.duration
        return None

    def to_output_time_clamped(self, source_t: float) -> float:
        """Comme to_output_time, mais rabat sur la borne conservee la plus
        proche au lieu de renvoyer None -- utile pour une borne de sous-titre
        qui tombe pile dans un silence supprime."""
        elapsed = 0.0
        for c in self.cuts:
            if source_t < c.source_start:
                return elapsed
            if source_t <= c.source_end:
                return elapsed + (source_t - c.source_start)
            elapsed += c.duration
        return elapsed

    def to_source_time(self, output_t: float) -> float:
        """Inverse de to_output_time. Rabat aux bornes si output_t deborde."""
        if not self.cuts:
            return 0.0
        remaining = max(0.0, output_t)
        for c in self.cuts:
            if remaining <= c.duration:
                return c.source_start + remaining
            remaining -= c.duration
        return self.cuts[-1].source_end

    # ---------- Mots ----------

    def remap_words(self, words: list[Word]) -> list[Word]:
        """Mots reecrits en temps de sortie (voir remap_words_with_indices)."""
        return [w for _, w in self.remap_words_with_indices(words)]

    def remap_words_with_indices(self, words: list[Word]) -> list[tuple[int, Word]]:
        """Reecrit les mots en temps de sortie, en supprimant ceux entierement
        coupes et en rognant ceux qui debordent d'un segment conserve.

        Renvoie aussi l'index d'ORIGINE de chaque mot conserve : tout ce qui est
        indexe sur la liste de depart (les scores de mise en evidence, par
        exemple) doit pouvoir suivre quand le montage retire des mots.

        Un mot n'est jamais coupe en deux : s'il chevauche une coupe, il garde
        la partie conservee la plus longue -- un sous-titre qui apparait a
        moitie serait pire que pas de sous-titre du tout.
        """
        out: list[tuple[int, Word]] = []
        for index, w in enumerate(words):
            best: tuple[float, float] | None = None
            for c in self.cuts:
                overlap_start = max(w.start, c.source_start)
                overlap_end = min(w.end, c.source_end)
                if overlap_end - overlap_start <= _EPS:
                    continue
                if best is None or (overlap_end - overlap_start) > (best[1] - best[0]):
                    best = (overlap_start, overlap_end)
            if best is None:
                continue
            out.append((
                index,
                Word(
                    text=w.text,
                    start=self.to_output_time_clamped(best[0]),
                    end=self.to_output_time_clamped(best[1]),
                    probability=w.probability,
                ),
            ))
        return out

    # ---------- Serialisation ----------

    def to_dict(self) -> dict:
        return {
            "cuts": [c.to_dict() for c in self.cuts],
            "source_duration": round(self.source_span, 3),
            "output_duration": round(self.output_duration, 3),
            "removed_duration": round(self.removed_duration, 3),
        }

    @staticmethod
    def from_dict(d: dict) -> "EditList":
        return EditList.from_ranges(
            [(c["source_start"], c["source_end"]) for c in d.get("cuts", [])]
        )
