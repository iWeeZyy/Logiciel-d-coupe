"""Analyse approfondie d'un contenu deja repere par le Radar.

Ce paquet ne duplique RIEN du pipeline existant : il enchaine des briques deja
ecrites (extraction audio, Faster-Whisper, decoupage en phrases, extraction de
titres) et n'ajoute que ce qui manquait -- la lecture du contenu transcrit, la
designation du moment important, et la mise en forme d'un descriptif.

Tout est local. Aucun appel a une API d'intelligence artificielle distante.
"""
