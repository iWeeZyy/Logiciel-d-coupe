"""Compte rendu de fin de production (section 8).

Compte ce qui a REELLEMENT ete produit, en relisant les ClipResult, plutot que
de repeter le nombre de clips demande. Un module desactive, une miniature qui
n'a pas pu etre extraite ou un titre qu'aucune phrase ne permettait de
construire honnetement doivent se voir dans ce bilan -- annoncer "10 titres
generes" quand le module etait eteint serait un mensonge d'interface.

Module pur.
"""
from __future__ import annotations

from dataclasses import dataclass


def _format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours} h {minutes:02d} min"
    if minutes:
        return f"{minutes} min {secs:02d} s"
    return f"{secs} s"


@dataclass(frozen=True)
class ProductionReport:
    clips: int = 0
    thumbnails: int = 0
    titles: int = 0
    descriptions: int = 0
    subtitle_files: int = 0
    elapsed_s: float = 0.0

    def lines(self) -> list[str]:
        """Bilan lisible. Une categorie a zero est omise plutot qu'affichee :
        "0 miniature generee" occupe une ligne pour ne rien dire."""
        out = [f"{self.clips} clip(s) généré(s)"]
        for count, singular, plural in (
            (self.thumbnails, "miniature générée", "miniatures générées"),
            (self.titles, "titre généré", "titres générés"),
            (self.descriptions, "description générée", "descriptions générées"),
            (self.subtitle_files, "fichier de sous-titres", "fichiers de sous-titres"),
        ):
            if count:
                out.append(f"{count} {singular if count == 1 else plural}")
        if self.elapsed_s > 0:
            out.append(f"Temps total : {_format_duration(self.elapsed_s)}")
        return out


def build_report(clip_results, elapsed_s: float = 0.0) -> ProductionReport:
    """Bilan a partir des ClipResult reellement produits."""
    thumbnails = sum(len(c.thumbnails) for c in clip_results)
    subtitle_files = sum(len(c.subtitles) for c in clip_results)
    titles = sum(1 for c in clip_results if (c.metadata or {}).get("titles"))
    descriptions = sum(1 for c in clip_results if (c.metadata or {}).get("description"))
    return ProductionReport(
        clips=len(clip_results),
        thumbnails=thumbnails,
        titles=titles,
        descriptions=descriptions,
        subtitle_files=subtitle_files,
        elapsed_s=elapsed_s,
    )
