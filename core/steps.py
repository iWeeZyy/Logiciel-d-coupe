"""Liste des etapes du pipeline -- source unique, partagee par le moteur et
l'interface.

Avant l'edition automatique, la CLI et le widget d'etapes de la GUI portaient
chacun leur propre copie de la liste ; ajouter une etape au pipeline laissait
silencieusement l'interface afficher l'ancienne. Les etapes varient desormais
selon les modules actives, donc la liste est construite ici, transportee dans
ProgressEvent.step_labels, et l'interface se contente de l'afficher.
"""
from __future__ import annotations

STEP_AUDIO = "Extraction audio"
STEP_TRANSCRIPTION = "Transcription"
STEP_ANALYSIS = "Analyse des hooks"
STEP_SELECTION = "Sélection des meilleurs passages"
STEP_CONTEXT = "Détection du contexte"
STEP_RENDER = "Génération des clips"


def build_step_labels(context_detection: bool = False) -> list[str]:
    """Etapes reellement executees pour ce run.

    Une etape desactivee n'apparait pas du tout, plutot que d'apparaitre et de
    se cocher instantanement sans avoir rien fait -- l'utilisateur doit pouvoir
    lire dans cette liste ce qui se passe vraiment.
    """
    labels = [STEP_AUDIO, STEP_TRANSCRIPTION, STEP_ANALYSIS, STEP_SELECTION]
    if context_detection:
        labels.append(STEP_CONTEXT)
    labels.append(STEP_RENDER)
    return labels
