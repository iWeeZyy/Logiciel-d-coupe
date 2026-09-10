"""Faux paquet chatterbox, pour eprouver le worker SANS le vrai modele.

Il ne simule pas une voix : il rend un signal previsible dont on peut verifier
la duree et l'amplitude. Ce que ce faux permet de tester, c'est le CONTRAT --
appels recus, decoupage, concatenation, ecriture du WAV -- pas la qualite
audio, qui ne se teste qu'avec le vrai modele.
"""
__version__ = "0.0.0-faux"
