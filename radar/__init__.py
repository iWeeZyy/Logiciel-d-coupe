"""Radar : veille de contenu multi-plateforme (YouTube, Twitch).

Le Radar ne produit pas de clips. Il SELECTIONNE des opportunites et les
transmet au pipeline existant : transcription, scores, contexte, cadrage,
montage, sous-titres, titres, miniatures. Aucun de ces modules n'est
reimplemente ici.

Architecture volontairement generique des le depart : une seule notion de
createur, d'opportunite et de score, et des adaptateurs par plateforme sous
platforms/. Construire d'abord un Radar YouTube puis le "refactoriser" pour
Twitch aurait coute le refactor pour rien.

    models.py     Creator, Opportunity, Snapshot -- vocabulaire commun
    store.py      base SQLite locale
    scoring.py    Radar Score, transparent et ponderable
    trends.py     progression, a partir de NOS releves successifs
    creators.py   liste, priorites, actif/inactif
    engine.py     orchestration d'un scan, annulable
    platforms/    adaptateurs YouTube et Twitch

Ce qui exige Internet : resoudre une chaine, scanner, lire des statistiques.
Tout le reste -- scores, tendances, historique, favoris, apprentissage -- est
calcule localement a partir de ce qui a deja ete recupere.
"""
