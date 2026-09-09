"""Reecriture originale d'un script a partir d'une transcription.

Ce paquet ADAPTE un texte : il reformule, reorganise et resserre. Il ne
supprime aucun droit et ne rend rien "libre" -- une reformulation reste une
oeuvre derivee de ce qu'elle reformule, et l'interface le dit.

Deux moities, separees a dessein :

- ce qui se calcule SANS modele de langue (analyse du transcript, reperage du
  hook, des chiffres, du resultat final, estimation de duree, verification de
  ce qui a ete ajoute) : pur, teste, toujours disponible ;
- ce qui demande un modele (la generation elle-meme), derriere une interface
  de fournisseur. Sans modele installe, l'analyse s'affiche quand meme et la
  generation est desactivee, jamais simulee.
"""
