"""Suivi des performances reelles des clips, et apprentissage progressif.

Decoupage volontaire, dans l'ordre de dependance :

    models.py   structures (caracteristiques d'un clip, performances saisies)
    store.py    persistance locale, JSON, sur la machine et nulle part ailleurs
    metrics.py  Performance Score normalise, calcul pur et documente
    tracker.py  enregistrement des caracteristiques a la production
    analyzer.py comparaison estime/reel, correlations, seuils de fiabilite
    learning.py propositions de ponderations, acceptation, retour arriere
    profile.py  profil de performance personnel

Aucun de ces modules ne parle a l'interface, et aucun n'envoie quoi que ce soit
sur le reseau : la contrainte du cahier des charges est que tout reste local.
"""
