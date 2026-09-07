"""Modules d'edition automatique (contexte, montage, cadrage, sous-titres,
metadonnees, miniatures).

Meme convention que analysis/ : ce sont des fonctions PURES, sans acces disque,
sans ffmpeg et sans reseau, pour rester testables sans video reelle. Les modules
qui doivent parler a ffmpeg/OpenCV vivent dans video/, jamais ici.

Chaque module renvoie un resultat portant une confiance explicite : c'est
l'orchestrateur (pipeline.py) qui decide d'appliquer ou non la proposition,
jamais le module lui-meme -- principe "mieux vaut ne rien modifier que mal
modifier" (section 13 du cahier des charges).
"""
