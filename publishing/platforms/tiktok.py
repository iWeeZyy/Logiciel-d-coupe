"""Publication d'une video via l'API officielle TikTok (Content Posting API).

Contrairement a Instagram, TikTok ACCEPTE un fichier : on initialise un envoi,
la plateforme renvoie une adresse, on y depose les octets. Une application
locale peut donc publier sans rien heberger. C'est la difference decisive entre
les deux plateformes, et elle explique pourquoi ce module va plus loin que
celui d'Instagram.

Conditions cote TikTok, a remplir par l'utilisateur :
- une application sur TikTok for Developers ;
- l'autorisation video.publish pour publier directement, ou video.upload pour
  deposer un brouillon dans l'application ;
- un audit de l'application par TikTok. TANT QUE L'APPLICATION N'EST PAS
  AUDITEE, TikTok restreint les publications a une visibilite privee. Ce n'est
  pas une limite de ce code, et il vaut mieux le savoir avant de s'etonner de
  ne pas voir sa video publiquement.

AUCUN de ces appels n'a ete teste contre un vrai compte depuis ce projet : sans
application auditee, il n'y a rien a tester. Le code suit la documentation
officielle, ce n'est pas une preuve de fonctionnement.
"""
from __future__ import annotations

from pathlib import Path

from core.logging_setup import get_logger
from publishing import tokens
from publishing.models import PLATFORM_TIKTOK
from publishing.platforms.base import PlatformAdapter, PlatformStatus, PublishResult
from publishing.platforms.credentials import credentials_path, load

logger = get_logger()

API_BASE = "https://open.tiktokapis.com/v2"
REQUIRED_SCOPES = ("video.publish",)

SETUP_HINT = (
    "Publication directe TikTok : elle demande une application sur TikTok for "
    "Developers, l'autorisation video.publish, et un audit de l'application par "
    "TikTok. Tant que l'application n'est pas auditée, TikTok restreint les "
    "publications à une visibilité privée.\n\n"
    "Renseignez la clé et le secret de l'application dans :\n{path}\n\n"
    "Sans cela, l'export manuel prépare la vidéo, la légende et les hashtags."
)

# Taille d'un morceau d'envoi. TikTok impose un decoupage pour les gros
# fichiers ; en dessous de cette taille, un seul morceau suffit.
CHUNK_BYTES = 10 * 1024 * 1024


class TikTokAdapter(PlatformAdapter):
    platform = PLATFORM_TIKTOK

    def __init__(self, timeout: int = 60):
        self.timeout = timeout

    def status(self) -> PlatformStatus:
        credentials = load(self.platform)
        if not credentials.complete:
            return PlatformStatus(
                platform=self.platform, configured=False, connected=False,
                reason="Aucune application TikTok configurée.",
                setup_hint=SETUP_HINT.format(path=credentials_path(self.platform)),
            )
        if not tokens.has_token(self.platform):
            return PlatformStatus(
                platform=self.platform, configured=True, connected=False,
                reason="Aucun compte TikTok connecté.",
                setup_hint="Connectez un compte depuis Paramètres → Réseaux sociaux.",
            )
        return PlatformStatus(platform=self.platform, configured=True, connected=True,
                              account=self._account_label())

    def _account_label(self) -> str:
        from gui import settings_store

        try:
            return settings_store.get("tiktok_account") or ""
        except Exception:
            return ""

    def publish(self, draft, *, on_progress=None, cancel_token=None) -> PublishResult:
        state = self.status()
        if not state.can_publish:
            return PublishResult(ok=False, error=state.reason)

        video = Path(draft.clip_path)
        if not video.is_file():
            return PublishResult(ok=False, error="Le fichier vidéo est introuvable.")

        token = tokens.load_token(self.platform)
        size = video.stat().st_size

        try:
            import requests

            headers = {"Authorization": f"Bearer {token}",
                       "Content-Type": "application/json; charset=UTF-8"}
            if on_progress:
                on_progress(0.1)

            init = requests.post(
                f"{API_BASE}/post/publish/video/init/",
                headers=headers,
                json={
                    "post_info": {
                        "title": draft.full_text_for(self.platform),
                        "privacy_level": "SELF_ONLY",
                    },
                    "source_info": {
                        "source": "FILE_UPLOAD",
                        "video_size": size,
                        "chunk_size": min(size, CHUNK_BYTES),
                        "total_chunk_count": max(1, -(-size // CHUNK_BYTES)),
                    },
                },
                timeout=self.timeout,
            )
            payload = init.json()
            data = (payload or {}).get("data") or {}
            upload_url = data.get("upload_url")
            publish_id = data.get("publish_id", "")
            if init.status_code >= 400 or not upload_url:
                return PublishResult(ok=False, error=self._explain(payload))

            if cancel_token is not None:
                cancel_token.check()
            if on_progress:
                on_progress(0.4)

            with open(video, "rb") as handle:
                sent = requests.put(
                    upload_url,
                    data=handle,
                    headers={"Content-Type": "video/mp4",
                             "Content-Length": str(size),
                             "Content-Range": f"bytes 0-{size - 1}/{size}"},
                    timeout=self.timeout,
                )
            if sent.status_code >= 400:
                return PublishResult(ok=False, error="TikTok a refusé l'envoi du fichier.")
        except Exception as error:      # noqa: BLE001
            logger.exception("Publication TikTok : échec")
            return PublishResult(ok=False, error=f"TikTok est injoignable : {error}")

        if on_progress:
            on_progress(1.0)
        # TikTok ne renvoie pas l'adresse publique de la video ici : le
        # traitement est asynchrone. On ne fabrique pas un lien plausible.
        logger.info(f"Envoi TikTok terminé (publish_id={publish_id}).")
        return PublishResult(ok=True, url="")

    @staticmethod
    def _explain(payload: dict) -> str:
        error = (payload or {}).get("error") or {}
        message = error.get("message") or ""
        code = error.get("code") or ""
        if code in ("access_token_invalid", "token_expired"):
            return "La connexion TikTok a expiré. Reconnectez le compte."
        if message and message.lower() != "ok":
            return f"TikTok a refusé la publication : {message}"
        return "TikTok a refusé la publication."

    def disconnect(self) -> None:
        tokens.delete_token(self.platform)


def adapters() -> dict:
    """Les adaptateurs disponibles, par plateforme."""
    from publishing.platforms.instagram import InstagramAdapter

    return {PLATFORM_TIKTOK: TikTokAdapter(), "instagram": InstagramAdapter()}
