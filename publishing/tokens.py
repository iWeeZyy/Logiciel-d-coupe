"""Rangement des jetons OAuth (section 12).

Un jeton d'acces vaut un mot de passe : il permet de publier au nom du compte.
Il ne doit donc jamais finir dans un fichier de configuration, dans un journal,
ni a l'ecran.

Windows fournit un coffre pour cela, le Gestionnaire d'identifiants, accessible
par la bibliotheque `keyring`. Elle est optionnelle : sans elle, ce module
REFUSE d'enregistrer plutot que d'ecrire un jeton en clair quelque part. Un
refus explique se corrige ; un fichier en clair ne se remarque que le jour ou
il fuit.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.logging_setup import get_logger

logger = get_logger()

SERVICE = "ClipFarming"

UNAVAILABLE_MESSAGE = (
    "Le coffre de mots de passe de Windows n'est pas accessible. ClipFarming "
    "refuse d'enregistrer un jeton de connexion ailleurs, car un jeton permet de "
    "publier au nom du compte.\n\n"
    "Installez la bibliothèque « keyring » (pip install keyring) pour connecter "
    "un compte, ou utilisez l'export manuel, qui ne demande aucune connexion."
)


@dataclass(frozen=True)
class StoredAccount:
    """Ce qu'on affiche d'un compte connecte : jamais le jeton."""

    platform: str
    username: str = ""
    account_id: str = ""
    expires_at: str = ""

    @property
    def label(self) -> str:
        return f"@{self.username}" if self.username else (self.account_id or "compte connecté")


def _keyring():
    try:
        import keyring

        return keyring
    except Exception:
        return None


def available() -> bool:
    """Le coffre est-il utilisable ?

    On tente une lecture reelle : `keyring` s'importe parfois sans backend
    utilisable, et decouvrir le probleme au moment d'enregistrer un jeton
    laisserait croire que la connexion a reussi.
    """
    ring = _keyring()
    if ring is None:
        return False
    try:
        ring.get_password(SERVICE, "__verification__")
        return True
    except Exception as error:
        logger.warning(f"Coffre de mots de passe indisponible : {error}")
        return False


def save_token(platform: str, token: str) -> bool:
    """Range un jeton. Renvoie False si le coffre n'est pas disponible.

    Le jeton n'apparait dans aucun journal : seule l'operation est tracee.
    """
    ring = _keyring()
    if ring is None:
        return False
    try:
        ring.set_password(SERVICE, f"{platform}_token", token)
    except Exception as error:
        logger.warning(f"Enregistrement du jeton {platform} impossible : {error}")
        return False
    logger.info(f"Jeton {platform} enregistré dans le coffre de Windows.")
    return True


def load_token(platform: str) -> str:
    ring = _keyring()
    if ring is None:
        return ""
    try:
        return ring.get_password(SERVICE, f"{platform}_token") or ""
    except Exception as error:
        logger.warning(f"Lecture du jeton {platform} impossible : {error}")
        return ""


def delete_token(platform: str) -> None:
    ring = _keyring()
    if ring is None:
        return
    try:
        ring.delete_password(SERVICE, f"{platform}_token")
        logger.info(f"Jeton {platform} supprimé du coffre.")
    except Exception:
        # Absent : il n'y a rien a supprimer, ce n'est pas une erreur.
        pass


def has_token(platform: str) -> bool:
    return bool(load_token(platform))
