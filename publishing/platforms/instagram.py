"""Publication d'un Reel via l'API officielle de Meta.

CE QU'IL FAUT SAVOIR AVANT DE LIRE LE CODE, parce que cela decide de tout :

l'API de publication de contenu d'Instagram NE PREND PAS de fichier. On lui
donne une ADRESSE HTTPS PUBLIQUE, et ce sont les serveurs de Meta qui vont
telecharger la video. Pour une application qui tourne sur un ordinateur
personnel avec des fichiers locaux, c'est un obstacle de fond : il faudrait
heberger la video quelque part de public avant de publier.

Ce module ne fait donc PAS semblant. Il implemente l'appel officiel en deux
temps -- creation du conteneur, puis publication -- et il refuse clairement,
avec la raison, quand il n'a pas d'adresse publique a donner. L'export manuel
reste le chemin qui marche, et il n'a rien d'un pis-aller.

Conditions cote Meta, a remplir par l'utilisateur :
- un compte Instagram professionnel (Business ou Creator), relie a une Page ;
- une application Meta pour developpeurs ;
- les autorisations instagram_basic et instagram_content_publish ;
- une validation de l'application par Meta pour publier hors comptes de test.

AUCUN de ces appels n'a ete teste contre un vrai compte depuis ce projet : sans
application validee, il n'y a rien a tester. Le code suit la documentation
officielle, ce n'est pas une preuve de fonctionnement.
"""
from __future__ import annotations

from core.logging_setup import get_logger
from publishing import tokens
from publishing.models import PLATFORM_INSTAGRAM
from publishing.platforms.base import PlatformAdapter, PlatformStatus, PublishResult
from publishing.platforms.credentials import credentials_path, load

logger = get_logger()

GRAPH_BASE = "https://graph.facebook.com/v21.0"
REQUIRED_SCOPES = ("instagram_basic", "instagram_content_publish",
                   "pages_show_list", "pages_read_engagement")

SETUP_HINT = (
    "Publication directe Instagram : elle demande un compte professionnel "
    "(Business ou Creator) relié à une Page, une application Meta pour "
    "développeurs, et sa validation par Meta.\n\n"
    "Renseignez l'identifiant et le secret de l'application dans :\n{path}\n\n"
    "Sans cela, l'export manuel prépare la vidéo, la légende et les hashtags."
)

NO_PUBLIC_URL = (
    "Instagram ne reçoit pas de fichier : ses serveurs vont chercher la vidéo à "
    "une adresse publique. ClipFarming n'héberge rien, la publication directe "
    "n'est donc pas possible depuis un fichier local.\n\n"
    "Utilisez l'export : la vidéo, la légende et les hashtags sont préparés, il "
    "ne reste qu'à les déposer dans l'application Instagram."
)


class InstagramAdapter(PlatformAdapter):
    platform = PLATFORM_INSTAGRAM

    def __init__(self, timeout: int = 30):
        self.timeout = timeout

    def status(self) -> PlatformStatus:
        credentials = load(self.platform)
        if not credentials.complete:
            return PlatformStatus(
                platform=self.platform, configured=False, connected=False,
                reason="Aucune application Meta configurée.",
                setup_hint=SETUP_HINT.format(path=credentials_path(self.platform)),
            )
        if not tokens.has_token(self.platform):
            return PlatformStatus(
                platform=self.platform, configured=True, connected=False,
                reason="Aucun compte Instagram connecté.",
                setup_hint="Connectez un compte depuis Paramètres → Réseaux sociaux.",
            )
        return PlatformStatus(platform=self.platform, configured=True, connected=True,
                              account=self._account_label())

    def _account_label(self) -> str:
        from gui import settings_store

        try:
            return settings_store.get("instagram_account") or ""
        except Exception:
            return ""

    def publish(self, draft, *, on_progress=None, cancel_token=None) -> PublishResult:
        """Publie un Reel, si une adresse publique est disponible.

        `draft.public_url` n'existe pas aujourd'hui : c'est volontaire. Tant que
        rien n'heberge la video, ce chemin renvoie une explication plutot qu'un
        echec obscur.
        """
        state = self.status()
        if not state.can_publish:
            return PublishResult(ok=False, error=state.reason)

        public_url = getattr(draft, "public_url", "")
        if not public_url:
            return PublishResult(ok=False, error=NO_PUBLIC_URL)

        token = tokens.load_token(self.platform)
        account_id = self._account_id()
        if not account_id:
            return PublishResult(ok=False, error="Identifiant du compte Instagram inconnu.")

        try:
            import requests

            if on_progress:
                on_progress(0.3)
            creation = requests.post(
                f"{GRAPH_BASE}/{account_id}/media",
                data={"media_type": "REELS", "video_url": public_url,
                      "caption": draft.full_text_for(self.platform),
                      "access_token": token},
                timeout=self.timeout,
            )
            payload = creation.json()
            if creation.status_code >= 400 or "id" not in payload:
                return PublishResult(ok=False, error=self._explain(payload))

            if on_progress:
                on_progress(0.7)
            published = requests.post(
                f"{GRAPH_BASE}/{account_id}/media_publish",
                data={"creation_id": payload["id"], "access_token": token},
                timeout=self.timeout,
            )
            result = published.json()
            if published.status_code >= 400 or "id" not in result:
                return PublishResult(ok=False, error=self._explain(result))
        except Exception as error:      # noqa: BLE001
            logger.exception("Publication Instagram : échec")
            return PublishResult(ok=False, error=f"Instagram est injoignable : {error}")

        if on_progress:
            on_progress(1.0)
        # Meta ne renvoie pas d'adresse publique du Reel : on ne l'invente pas.
        return PublishResult(ok=True, url="")

    def _account_id(self) -> str:
        from gui import settings_store

        try:
            return settings_store.get("instagram_account_id") or ""
        except Exception:
            return ""

    @staticmethod
    def _explain(payload: dict) -> str:
        error = (payload or {}).get("error") or {}
        message = error.get("error_user_msg") or error.get("message") or ""
        if not message:
            return "Instagram a refusé la publication."
        return f"Instagram a refusé la publication : {message}"

    def disconnect(self) -> None:
        tokens.delete_token(self.platform)
