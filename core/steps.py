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
STEP_CLIP_ANALYSIS = "Analyse du clip"
STEP_SELECTION = "Sélection des meilleurs passages"
STEP_CONTEXT = "Détection du contexte"
STEP_RENDER = "Génération des clips"
STEP_METADATA = "Titres et miniatures"


def build_step_labels(context_detection: bool = False, metadata: bool = False,
                      whole_source: bool = False) -> list[str]:
    """Etapes reellement executees pour ce run.

    Une etape desactivee n'apparait pas du tout, plutot que d'apparaitre et de
    se cocher instantanement sans avoir rien fait -- l'utilisateur doit pouvoir
    lire dans cette liste ce qui se passe vraiment.

    `whole_source` : la source EST deja le clip (un clip Twitch recupere par le
    Radar). Il n'y a alors rien a chercher, rien a comparer et rien a recadrer
    dans le temps -- ces trois etapes disparaissent au lieu de se cocher sans
    avoir rien decide.
    """
    labels = [STEP_AUDIO, STEP_TRANSCRIPTION]
    if whole_source:
        labels.append(STEP_CLIP_ANALYSIS)
        labels.append(STEP_RENDER)
        if metadata:
            labels.append(STEP_METADATA)
        return labels

    labels += [STEP_ANALYSIS, STEP_SELECTION]
    if context_detection:
        labels.append(STEP_CONTEXT)
    labels.append(STEP_RENDER)
    if metadata:
        labels.append(STEP_METADATA)
    return labels
