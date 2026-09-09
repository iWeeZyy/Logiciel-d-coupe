"""Adaptateur Twitch Helix (API officielle).

Authentification : flux "client credentials" -- l'application echange un
Client ID et un Client Secret contre un jeton applicatif. C'est le flux prevu
par Twitch pour lire des donnees PUBLIQUES sans agir au nom d'un utilisateur,
et c'est exactement notre cas : on lit des chaines, des streams, des VOD et
des clips publics.

Les identifiants se creent sur dev.twitch.tv/console/apps. Ils ne sont pas
compiles dans l'application et ne transitent par aucun de nos fichiers de
donnees : ils sont lus depuis l'environnement ou depuis un fichier texte du
dossier utilisateur, comme la cle YouTube.

Ce que ce module NE fait pas, volontairement :
- il ne telecharge aucun stream, aucune VOD, aucun clip ;
- il ne simule pas un navigateur et ne contourne aucune limitation ;
- il ne presente jamais un contenu comme reutilisable : Twitch expose des
  metadonnees publiques, ce qui n'est pas une cession de droits.

Endpoints utilises, tous documentes et publics :
    GET /users    resoudre une chaine (login ou id)
    GET /streams  savoir qui est en direct, et avec combien de spectateurs
    GET /videos   VOD recentes d'une chaine
    GET /clips    clips d'une chaine sur une periode
    GET /games    nom d'un jeu a partir de son identifiant
"""
from __future__ import annotations

import os
import re
import time
from datetime import datetime, timezone

import requests

from core.logging_setup import get_logger
from core.paths import user_data_dir
from radar.models import KIND_CLIP, KIND_LIVE, KIND_VOD, Creator, Opportunity
from radar.platforms.base import PlatformAdapter, PlatformStatus
from utils.errors import TwitchApiError, TwitchConfigError

logger = get_logger()

PLATFORM = "twitch"
_HELIX = "https://api.twitch.tv/helix"
_OAUTH = "https://id.twitch.tv/oauth2/token"

CREDENTIALS_FILE = "twitch_credentials.txt"

# Types de contenu recherches par defaut.
#
# Les CLIPS sont le coeur du Radar Twitch : ils sont deja le decoupage d'un
# moment fort, fait par le public au moment ou il s'est produit. C'est
# exactement le travail de selection que le reste du logiciel cherche a
# automatiser, et le public l'a fait gratuitement.
#
# Les DIRECTS sont conserves parce qu'ils ne sont pas du contenu a recuperer
# mais un signal de surveillance : savoir qui est en train de streamer, et
# devant combien de monde, est ce que la section "Live Radar" demande.
#
# Les VOD sont ECARTEES par defaut : une rediffusion de quatre heures n'a rien
# qui designe le moment interessant, et Twitch n'offre aucun moyen officiel de
# la recuperer. Elles restent activables pour qui veut surveiller la simple
# activite d'une chaine.
DEFAULT_CONTENT_KINDS = (KIND_CLIP, KIND_LIVE)

_DURATION_RE = re.compile(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?")


def credentials_path():
    return user_data_dir() / CREDENTIALS_FILE


def load_credentials() -> tuple[str, str]:
    """(client_id, client_secret) depuis l'environnement ou le fichier local.

    Meme forme que la cle YouTube : variables d'environnement d'abord, puis un
    fichier texte a un emplacement precis -- que le message d'erreur NOMME,
    parce que deviner cet emplacement a deja fait perdre du temps.
    """
    client_id = os.environ.get("TWITCH_CLIENT_ID", "").strip()
    client_secret = os.environ.get("TWITCH_CLIENT_SECRET", "").strip()
    if client_id and client_secret:
        return client_id, client_secret

    path = credentials_path()
    if path.exists():
        try:
            # utf-8-sig : PowerShell 5.1 ecrit un BOM avec -Encoding utf8, et
            # ce caractere invisible n'est pas retire par strip(). Meme piege
            # que pour la cle YouTube, deja rencontre en usage reel.
            lines = [line.strip() for line in path.read_text(encoding="utf-8-sig").splitlines()
                     if line.strip() and not line.strip().startswith("#")]
        except OSError as error:
            raise TwitchConfigError(f"Impossible de lire {path} : {error}") from error
        if len(lines) >= 2:
            return lines[0], lines[1]

    raise TwitchConfigError(
        "Identifiants Twitch absents. Crée une application sur "
        "https://dev.twitch.tv/console/apps (gratuit), puis définis les variables "
        "d'environnement TWITCH_CLIENT_ID et TWITCH_CLIENT_SECRET, ou place le "
        "Client ID sur la première ligne et le Client Secret sur la seconde de ce "
        f"fichier :\n{path}"
    )


def parse_twitch_duration(value: str) -> int | None:
    """'4h32m10s' -> secondes. None si illisible."""
    if not value:
        return None
    match = _DURATION_RE.fullmatch(value.strip())
    if not match or not any(match.groups()):
        return None
    hours, minutes, seconds = (int(g) if g else 0 for g in match.groups())
    return hours * 3600 + minutes * 60 + seconds


def parse_query(query: str) -> str:
    """Nom, @username ou URL de chaine -> login Twitch."""
    text = (query or "").strip()
    if not text:
        return ""
    if "twitch.tv" in text.lower():
        path = text.split("twitch.tv", 1)[-1].split("?", 1)[0].strip("/")
        segments = [s for s in path.split("/") if s]
        return segments[0].lower() if segments else ""
    return text.lstrip("@").strip().lower()


def _clip_duration(value) -> int | None:
    """Duree d'un clip en secondes entieres, ou None si elle est inconnue.

    Twitch la donne en decimales (30,4 s) : on ARRONDIT au lieu de tronquer,
    sinon un clip de 29,8 s s'affiche 29 s. Une valeur illisible ne doit jamais
    interrompre un scan -- elle vaut simplement "duree inconnue".
    """
    try:
        seconds = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    return seconds or None


def _thumbnail(url: str, width: int = 640, height: int = 360) -> str:
    """Twitch renvoie des gabarits avec {width}/{height} a substituer."""
    return (url or "").replace("{width}", str(width)).replace("{height}", str(height))


class TwitchAdapter(PlatformAdapter):
    platform = PLATFORM

    def __init__(self, client_id: str | None = None, client_secret: str | None = None,
                 timeout: int = 15, content_kinds=None):
        self._client_id = client_id
        self._client_secret = client_secret
        self._token = ""
        self._token_expires_at = 0.0
        self.timeout = timeout
        # Noms de jeux deja resolus, gardes le temps du scan : les clips d'une
        # meme chaine partagent presque toujours la meme categorie.
        self._games: dict[str, str] = {}
        # Types de contenu recherches. Les VOD sont ECARTEES par defaut : une
        # rediffusion de quatre heures n'est pas une opportunite exploitable
        # (rien n'y designe le moment fort, et Twitch n'offre aucun moyen
        # officiel de la recuperer). Les clips, eux, sont deja le decoupage
        # d'un moment fort fait par le public -- c'est le travail de selection
        # le plus difficile, deja fait. Reglable dans config/radar.json.
        self.content_kinds = tuple(content_kinds) if content_kinds is not None \
            else DEFAULT_CONTENT_KINDS

    # ------------------------------------------------------------- etat
    def _credentials(self) -> tuple[str, str]:
        if self._client_id and self._client_secret:
            return self._client_id, self._client_secret
        self._client_id, self._client_secret = load_credentials()
        return self._client_id, self._client_secret

    def status(self) -> PlatformStatus:
        try:
            self._credentials()
        except TwitchConfigError as error:
            return PlatformStatus(
                platform=PLATFORM, available=False, reason=str(error),
                setup_hint="Client ID et Client Secret Twitch requis (gratuits, dev.twitch.tv).",
            )
        return PlatformStatus(platform=PLATFORM, available=True)

    # ------------------------------------------------------------ jeton
    def _access_token(self) -> str:
        """Jeton applicatif, renouvele avant expiration.

        Twitch renvoie une duree de validite (environ 60 jours) ; on renouvelle
        une minute avant l'echeance plutot que d'attendre un 401, ce qui ferait
        echouer un scan pour rien.
        """
        if self._token and time.time() < self._token_expires_at - 60:
            return self._token

        client_id, client_secret = self._credentials()
        try:
            response = requests.post(_OAUTH, params={
                "client_id": client_id, "client_secret": client_secret,
                "grant_type": "client_credentials",
            }, timeout=self.timeout)
        except requests.RequestException as error:
            raise TwitchApiError(
                f"Impossible de joindre Twitch : {error}. Le Radar a besoin d'une "
                "connexion internet pour scanner ; les données déjà collectées "
                "restent consultables hors ligne."
            ) from error

        if response.status_code in (400, 401, 403):
            raise TwitchConfigError(
                "Identifiants Twitch refusés. Vérifie le Client ID et le Client Secret "
                "sur https://dev.twitch.tv/console/apps — un secret ne s'affiche qu'une "
                "fois à sa création et doit être régénéré s'il a été perdu."
            )
        if not response.ok:
            raise TwitchApiError(f"Twitch a répondu {response.status_code} à la demande de jeton.")

        payload = response.json()
        self._token = payload.get("access_token", "")
        self._token_expires_at = time.time() + float(payload.get("expires_in", 3600))
        if not self._token:
            raise TwitchApiError("Twitch n'a renvoyé aucun jeton d'accès.")
        return self._token

    def _get(self, endpoint: str, params: dict) -> dict:
        client_id, _ = self._credentials()
        headers = {"Client-Id": client_id, "Authorization": f"Bearer {self._access_token()}"}
        try:
            response = requests.get(f"{_HELIX}/{endpoint}", params=params,
                                    headers=headers, timeout=self.timeout)
        except requests.RequestException as error:
            raise TwitchApiError(f"Impossible de joindre l'API Twitch : {error}") from error

        if response.status_code == 401:
            # Jeton revoque avant son echeance : on en redemande un, une fois.
            self._token = ""
            headers["Authorization"] = f"Bearer {self._access_token()}"
            response = requests.get(f"{_HELIX}/{endpoint}", params=params,
                                    headers=headers, timeout=self.timeout)
        if response.status_code == 429:
            raise TwitchApiError(
                "Limite de requêtes Twitch atteinte. Réessaie dans quelques minutes — "
                "les données déjà collectées restent disponibles."
            )
        if not response.ok:
            raise TwitchApiError(
                f"L'API Twitch a répondu {response.status_code} : {response.text[:200]}"
            )
        return response.json()

    # -------------------------------------------------------- resolution
    def resolve_creator(self, query: str) -> Creator | None:
        login = parse_query(query)
        if not login:
            return None
        data = self._get("users", {"login": login})
        items = data.get("data") or []
        if not items:
            return None
        user = items[0]
        creator = Creator(
            platform=PLATFORM,
            platform_id=user.get("id", ""),
            username=user.get("login", ""),
            display_name=user.get("display_name", ""),
            url=f"https://www.twitch.tv/{user.get('login', '')}",
            avatar_url=user.get("profile_image_url", ""),
        )
        # Le nombre de followers demande un endpoint distinct et un scope
        # utilisateur que le flux applicatif n'a pas : on le laisse inconnu
        # plutot que d'afficher un chiffre faux ou d'exiger une autorisation
        # que la surveillance ne justifie pas.
        return creator

    # -------------------------------------------------------------- scan
    def live_streams(self, creators: list) -> dict:
        """Streams en direct parmi les createurs donnes (Live Radar, section 6).

        Un seul appel pour cent chaines : interroger chaine par chaine
        multiplierait les requetes sans rien apporter.
        """
        by_key = {}
        logins = [c.username for c in creators if c.username]
        for start in range(0, len(logins), 100):
            batch = logins[start:start + 100]
            data = self._get("streams", {"user_login": batch, "first": 100})
            for item in data.get("data") or []:
                by_key[f"{PLATFORM}:{item.get('user_id', '')}"] = item
        return by_key

    def scan(self, creator: Creator, since_iso: str, max_results: int = 50) -> list[Opportunity]:
        """Contenus recents de ce createur, selon les types demandes.

        Un type desactive n'est pas seulement filtre apres coup : la requete
        correspondante n'est PAS envoyee. Chercher des VOD dont on ne veut pas
        consommerait une requete par createur et par scan pour rien.
        """
        out: list[Opportunity] = []
        if KIND_LIVE in self.content_kinds:
            out.extend(self._live(creator))
        if KIND_CLIP in self.content_kinds:
            out.extend(self._clips(creator, since_iso, max_results))
        if KIND_VOD in self.content_kinds:
            out.extend(self._videos(creator, since_iso, max_results))
        return out

    def _live(self, creator: Creator) -> list[Opportunity]:
        data = self._get("streams", {"user_id": creator.platform_id})
        items = data.get("data") or []
        if not items:
            return []
        stream = items[0]
        started = stream.get("started_at", "")
        return [Opportunity(
            platform=PLATFORM,
            content_id=f"live-{stream.get('id', creator.platform_id)}",
            kind=KIND_LIVE,
            creator_key=creator.key,
            title=stream.get("title", ""),
            url=f"https://www.twitch.tv/{creator.username}",
            thumbnail_url=_thumbnail(stream.get("thumbnail_url", "")),
            published_at=started,
            category=stream.get("game_name", ""),
            viewer_count=stream.get("viewer_count"),
            is_live=True,
            # Un direct n'a pas de duree finale : ce qu'on connait est son
            # anciennete, calculee a partir de published_at. Mettre une duree
            # ici laisserait croire a un contenu termine.
            extra={"language": stream.get("language", "")},
        )]

    def _game_names(self, game_ids) -> dict[str, str]:
        """Identifiants de jeu -> noms, en une requete pour toute la liste.

        /clips ne renvoie qu'un `game_id` numerique. L'afficher tel quel montre
        un nombre qui ne veut rien dire (c'est ce que faisait l'application) et
        en fait meme un hashtag. En cas d'echec de la resolution, on ne renvoie
        RIEN pour cet identifiant : un clip sans categorie est plus honnete
        qu'un nombre. Le nom manquant est mis en cache lui aussi, sinon chaque
        scan redemanderait la meme chose.
        """
        wanted = {str(g) for g in game_ids if g}
        missing = sorted(wanted - set(self._games))
        for start in range(0, len(missing), 100):        # /games accepte 100 id max
            chunk = missing[start:start + 100]
            try:
                payload = self._get("games", {"id": chunk})
            except TwitchApiError as error:
                logger.warning(f"Noms de jeux Twitch indisponibles : {error}")
                break
            for game in payload.get("data") or []:
                self._games[str(game.get("id", ""))] = game.get("name", "") or ""
            for game_id in chunk:
                self._games.setdefault(game_id, "")
        return {game_id: self._games.get(game_id, "") for game_id in wanted}

    def _clips(self, creator: Creator, since_iso: str, max_results: int) -> list[Opportunity]:
        data = self._get("clips", {
            "broadcaster_id": creator.platform_id,
            "started_at": since_iso,
            "first": min(100, max(1, max_results)),
        })
        clips = data.get("data") or []
        games = self._game_names([clip.get("game_id") for clip in clips])
        out = []
        for clip in clips:
            out.append(Opportunity(
                platform=PLATFORM,
                content_id=clip.get("id", ""),
                kind=KIND_CLIP,
                creator_key=creator.key,
                title=clip.get("title", ""),
                url=clip.get("url", ""),
                thumbnail_url=clip.get("thumbnail_url", ""),
                published_at=clip.get("created_at", ""),
                duration_s=_clip_duration(clip.get("duration")),
                category=games.get(str(clip.get("game_id") or ""), ""),
                view_count=clip.get("view_count"),
                extra={
                    # Qui a CREE le clip, qui n'est pas forcement le streamer :
                    # l'information compte pour le credit.
                    "clip_creator": clip.get("creator_name", ""),
                    "source_video_id": clip.get("video_id", ""),
                },
            ))
        return out

    def _videos(self, creator: Creator, since_iso: str, max_results: int) -> list[Opportunity]:
        data = self._get("videos", {
            "user_id": creator.platform_id, "type": "archive",
            "first": min(100, max(1, max_results)),
        })
        out = []
        for video in data.get("data") or []:
            published = video.get("published_at") or video.get("created_at") or ""
            # /videos n'accepte pas de date de debut : on filtre nous-memes.
            if published and published < since_iso:
                continue
            out.append(Opportunity(
                platform=PLATFORM,
                content_id=video.get("id", ""),
                kind=KIND_VOD,
                creator_key=creator.key,
                title=video.get("title", ""),
                url=video.get("url", ""),
                thumbnail_url=_thumbnail(video.get("thumbnail_url", "")),
                published_at=published,
                duration_s=parse_twitch_duration(video.get("duration", "")),
                view_count=video.get("view_count"),
                extra={"language": video.get("language", "")},
            ))
        return out


def stream_potential(stream_viewers: int | None, usual_viewers: float | None,
                     priority_factor: float = 1.0) -> tuple[float, float | None]:
    """Stream Potential Score (section 9 du Radar Twitch).

    Compare l'audience actuelle a l'audience habituelle du streamer. Sans
    historique, on ne peut PAS dire si 12 000 spectateurs sont beaucoup pour
    lui : le score se rabat alors sur l'audience brute, et le rapport renvoye
    est None -- l'interface ne doit pas afficher un pourcentage inexistant.
    """
    if stream_viewers is None:
        return 0.0, None
    if not usual_viewers or usual_viewers <= 0:
        base = min(100.0, 100.0 * stream_viewers / 20000.0)
        return round(min(100.0, base * priority_factor), 1), None
    ratio = stream_viewers / usual_viewers
    base = min(100.0, 50.0 + 50.0 * (ratio - 1.0) / 1.0)
    return round(max(0.0, min(100.0, base * priority_factor)), 1), round(ratio, 2)
