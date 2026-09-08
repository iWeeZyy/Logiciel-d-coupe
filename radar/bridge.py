"""Pont entre le Radar et le pipeline existant (sections 12, 23 ; 14 du Radar
Twitch).

Ce module est deliberement mince, et c'est son interet : le Radar SELECTIONNE
des opportunites, il ne produit rien. Transcription, scores, contexte, cadrage,
montage, sous-titres, titres et miniatures restent le pipeline existant, appele
tel quel.

LA BARRIERE LEGALE N'EST PAS REECRITE ICI. youtube/downloader.py refuse deja
tout telechargement sans confirmation explicite des droits
(RightsNotConfirmedError) : ce module s'appuie dessus au lieu d'ajouter une
seconde regle, qui finirait par diverger. Voir la constante RIGHTS_WARNING de ce
module-la, deja affichee par la recherche YouTube.

Aucune VOD ni aucun stream Twitch n'est telecharge : Twitch ne fournit pas de
mecanisme officiel pour cela, et le faire par un autre moyen contournerait ses
conditions. Pour Twitch, le pont attend donc un FICHIER LOCAL dont
l'utilisateur dispose legalement.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from radar.models import PLATFORM_TWITCH, PLATFORM_YOUTUBE

# Message affiche avant tout envoi vers le pipeline. Il dit ce qui est vrai :
# trouver un contenu ne donne aucun droit dessus.
RIGHTS_NOTICE = (
    "Trouver un contenu sur YouTube ou Twitch ne signifie pas disposer des droits "
    "nécessaires pour le republier ou le monétiser.\n\n"
    "Vous devez être l'auteur du contenu, avoir l'autorisation de son auteur, ou "
    "disposer d'un autre fondement légal. Cette vérification vous incombe."
)


@dataclass(frozen=True)
class PipelineRequest:
    """Ce qu'il faut au pipeline pour traiter une opportunite."""

    project_name: str
    source_kind: str            # "youtube" | "local"
    source: str                 # URL YouTube ou chemin local
    opportunity_keys: tuple = ()

    @property
    def needs_local_file(self) -> bool:
        return self.source_kind == "local"


class SourceNotAvailable(Exception):
    """Le contenu ne peut pas etre transmis au pipeline en l'etat."""


def _safe_project_name(label: str) -> str:
    cleaned = "".join(c if c.isalnum() or c in " -_" else " " for c in label).strip()
    return " ".join(cleaned.split())[:60] or "Radar"


def build_request(opportunities: list, local_files: dict | None = None) -> PipelineRequest:
    """Prepare l'envoi au Content Factory.

    `local_files` : {cle d'opportunite: chemin} pour les contenus dont
    l'utilisateur possede deja le fichier. Obligatoire pour Twitch.

    Un seul contenu par projet : le pipeline decoupe UNE video longue en
    plusieurs clips. Regrouper plusieurs sources dans un projet melangerait des
    transcriptions sans rapport et rendrait les scores incomparables.
    """
    if not opportunities:
        raise SourceNotAvailable("Aucune opportunité sélectionnée.")
    if len(opportunities) > 1:
        raise SourceNotAvailable(
            "Le pipeline traite une source à la fois : il découpe UNE vidéo longue en "
            "plusieurs clips. Sélectionnez un contenu, ou lancez-les l'un après l'autre."
        )

    opportunity = opportunities[0]
    local_files = local_files or {}
    local = local_files.get(opportunity.key)

    if local:
        path = Path(local)
        if not path.is_file():
            raise SourceNotAvailable(f"Fichier introuvable : {path}")
        return PipelineRequest(
            project_name=_safe_project_name(opportunity.title or opportunity.content_id),
            source_kind="local", source=str(path), opportunity_keys=(opportunity.key,),
        )

    if opportunity.platform == PLATFORM_YOUTUBE:
        # Le telechargement lui-meme reste soumis a la confirmation des droits
        # de youtube/downloader.py -- ce module ne fait que preparer la demande.
        return PipelineRequest(
            project_name=_safe_project_name(opportunity.title or opportunity.content_id),
            source_kind="youtube", source=opportunity.url,
            opportunity_keys=(opportunity.key,),
        )

    if opportunity.platform == PLATFORM_TWITCH:
        raise SourceNotAvailable(
            "Twitch ne fournit aucun moyen officiel de télécharger un stream, une VOD "
            "ou un clip, et le faire autrement contournerait ses conditions.\n\n"
            "Si vous disposez légalement du fichier vidéo (votre propre contenu, ou "
            "une autorisation de son auteur), sélectionnez-le : il sera traité par le "
            "pipeline local comme n'importe quelle autre vidéo."
        )

    raise SourceNotAvailable(f"Plateforme non prise en charge : {opportunity.platform}")
