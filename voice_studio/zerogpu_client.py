"""Parler a un Space Hugging Face qui execute Chatterbox V3 sur ZeroGPU.

SEUL MODULE DE L'APPLICATION QUI TOUCHE A HUGGING FACE. `gradio_client` y est
importe PARESSEUSEMENT, dans les fonctions : son absence n'empeche donc jamais
l'application de demarrer, exactement comme l'absence de Chatterbox local.

TROIS CHOIX QUI MERITENT UNE EXPLICATION.

1. L'API EST DECOUVERTE, PAS DEVINEE. Le depot officiel de Chatterbox n'attache
   aucun `api_name` a son bouton, et le fichier reellement deploye sur le Space
   n'est pas lisible depuis un depot public. Ecrire un nom d'endpoint en dur
   serait une supposition, et une supposition fausse enverrait le texte dans le
   champ « temperature ». On lit donc `view_api()` a la connexion, on associe
   nos valeurs aux parametres PAR LEUR NOM, et on n'appelle que si
   l'association est complete.

2. L'ATTENTE ET LA GENERATION SONT MESUREES SEPAREMENT. `Job.status()` passe
   par IN_QUEUE puis PROCESSING : la bascule donne la frontiere exacte. Les
   confondre rendrait toute comparaison avec le Chatterbox local trompeuse,
   puisque le local n'a pas de file d'attente.

3. RIEN N'EST PROMIS SUR LES LIMITES. La duree maximale d'un appel GPU n'est
   pas chiffree par la documentation officielle. Le module ne la teste donc
   pas : il decoupe court, et traduit un refus du serveur en une phrase
   lisible.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from core.cancellation import CancelToken
from core.logging_setup import get_logger
from utils.errors import CancelledError, ClipFarmingError
from voice_studio import zerogpu_catalogue as catalogue
from voice_studio import zerogpu_token

logger = get_logger()

POLL_S = 0.5


class ZeroGpuError(ClipFarmingError):
    """Toute panne du cote distant, deja traduite en francais lisible."""


@dataclass
class ChunkResult:
    index: int
    total: int
    text: str
    path: str = ""
    queue_s: float = 0.0
    gpu_s: float = 0.0

    @property
    def total_s(self) -> float:
        return self.queue_s + self.gpu_s


@dataclass
class Connection:
    """Ce qu'on sait du Space apres l'avoir contacte."""

    space: str
    api_name: str
    parameter_names: list = field(default_factory=list)
    authenticated: bool = False
    token_source: str = ""

    @property
    def label(self) -> str:
        who = self.token_source or "quota anonyme"
        return f"{self.space} · {self.api_name} · {who}"


def _import_client():
    try:
        from gradio_client import Client            # noqa: PLC0415
        return Client
    except ImportError as error:
        raise ZeroGpuError(
            "Le paquet gradio_client n'est pas installé : le banc d'essai "
            "ZeroGPU ne peut pas contacter Hugging Face.\n\n"
            f"Détail technique : {error}") from error


def _pick_endpoint(info: dict, wanted: Optional[str]) -> tuple[str, list]:
    """Choisit l'endpoint et rend la liste ordonnee de ses parametres.

    On privilegie un endpoint nomme ; a defaut on prend le premier endpoint
    anonyme, que Gradio expose sous la forme d'un entier. Un Space qui n'expose
    rien du tout est une erreur, pas un cas a contourner.
    """
    named = info.get("named_endpoints") or {}
    unnamed = info.get("unnamed_endpoints") or {}

    if wanted:
        if wanted in named:
            return wanted, named[wanted].get("parameters") or []
        raise ZeroGpuError(
            f"Le Space ne propose pas l'endpoint « {wanted} ».\n"
            f"Endpoints disponibles : {', '.join(named) or 'aucun nommé'}.\n\n"
            "Corrige « api_name » dans config/zerogpu.json, ou laisse-le à null "
            "pour que l'application le trouve elle-même.")

    # Le plus de parametres l'emporte : un Space expose souvent des fonctions
    # annexes (changer de langue, charger un exemple) qui n'en prennent qu'un.
    candidates = [(name, spec.get("parameters") or []) for name, spec in named.items()]
    if not candidates:
        candidates = [(key, spec.get("parameters") or []) for key, spec in unnamed.items()]
    if not candidates:
        raise ZeroGpuError(
            "Le Space ne publie aucune API appelable. Il est peut-être en cours "
            "de démarrage, en erreur, ou construit sans interface Gradio.")
    candidates.sort(key=lambda item: len(item[1]), reverse=True)
    return candidates[0]


def _parameter_names(parameters: list) -> list:
    """Nom utilisable de chaque parametre, dans l'ordre de l'appel."""
    names = []
    for position, parameter in enumerate(parameters):
        name = (parameter.get("parameter_name")
                or parameter.get("label")
                or f"#{position}")
        names.append(str(name))
    return names


def _arguments(names: list, params: catalogue.Params, text: str,
               configured: list) -> list:
    """Place nos valeurs en face des parametres du Space.

    Association PAR LE NOM quand le Space en donne un ; sinon par le libelle
    affiche ; sinon, repli sur l'ordre declare dans config/zerogpu.json,
    lui-meme lu dans le depot officiel. Un parametre inconnu recoit None, ce
    que Gradio traite comme « valeur par defaut » -- jamais une valeur
    inventee.

    NE VERIFIE PAS que le texte a trouve sa place : c'est le role de
    `check_arguments`, appele juste apres, parce qu'un appel sans texte est un
    appel arbitraire et doit etre refuse plutot qu'envoye.
    """
    known = {
        "text_input": text,
        "language_id": params.language,
        "audio_prompt_path_input": params.reference or None,
        "exaggeration_input": params.exaggeration,
        "temperature_input": params.temperature,
        "seed_num_input": params.seed,
        "cfgw_input": params.cfg_weight,
    }
    # Les libelles de l'interface, quand le Space ne publie pas de nom de
    # parametre. Lus dans multilingual_app.py du depot officiel.
    by_label = {
        "text to synthesize": "text_input",
        "language id": "language_id",
        "reference audio file": "audio_prompt_path_input",
        "exaggeration": "exaggeration_input",
        "temperature": "temperature_input",
        "random seed": "seed_num_input",
        "cfg/pace": "cfgw_input",
    }

    usable = list(names)
    unnamed = all(name.startswith("#") for name in usable)
    if unnamed and configured and len(configured) == len(usable):
        usable = list(configured)

    arguments = []
    for name in usable:
        key = name if name in known else ""
        if not key:
            lowered = name.lower()
            for fragment, target in by_label.items():
                if fragment in lowered:
                    key = target
                    break
        arguments.append(known.get(key))
    return arguments


def check_arguments(names: list, arguments: list, text: str) -> None:
    """Refuse un appel dont on ne sait pas ou mettre le script.

    LA REGLE POSEE : si la decouverte de l'API echoue, on affiche une erreur
    claire plutot que de tenter un appel arbitraire. Or `_arguments` met None
    partout ou il ne reconnait rien -- ce qui, pour le parametre du texte,
    produirait un appel qui part, consomme du quota GPU, et revient avec la
    voix par defaut du Space lisant son propre exemple. Un echec silencieux,
    donc, et le plus coûteux des trois.
    """
    if text and text in arguments:
        return
    raise ZeroGpuError(
        "L'API du Space ne correspond pas à ce que l'application sait "
        "envoyer : aucun de ses paramètres n'a pu être reconnu comme le "
        "champ de texte.\n\n"
        f"Paramètres publiés par le Space : {', '.join(str(name) for name in names) or 'aucun'}.\n\n"
        "Aucun appel n'a été envoyé, pour ne pas dépenser de quota GPU sur "
        "une requête incomplète. Corrige « space.parameters » dans "
        "config/zerogpu.json pour refléter la signature réelle, ou vérifie "
        "que « space.id » désigne bien un Space Chatterbox.")


def connect(cancel_token: Optional[CancelToken] = None) -> tuple[object, Connection]:
    """Ouvre la connexion et decrit ce qu'on a trouve en face."""
    space = catalogue.space_id()
    if not space:
        raise ZeroGpuError(
            "Aucun Space n'est configuré : renseigne « space.id » dans "
            "config/zerogpu.json (par exemple ta propre copie du Space "
            "Chatterbox Multilingual).")

    Client = _import_client()
    token = zerogpu_token.load_token()
    if cancel_token is not None:
        cancel_token.check()

    from voice_studio import store

    # download_files : gradio_client ecrit par defaut sous /tmp/gradio, ce qui
    # n'a pas de sens sur Windows. On le pose dans le dossier de travail de
    # Voice Studio, avec le reste des fichiers audio.
    landing = Path(store.audio_dir()) / "zerogpu"
    landing.mkdir(parents=True, exist_ok=True)

    try:
        client = Client(space, token=token or None, verbose=False,
                        download_files=str(landing))
    except Exception as error:                        # le client leve large
        raise ZeroGpuError(_explain(error, space)) from error

    try:
        info = client.view_api(return_format="dict", print_info=False) or {}
    except Exception as error:
        raise ZeroGpuError(_explain(error, space)) from error

    api_name, parameters = _pick_endpoint(info, catalogue.space_spec().get("api_name"))
    connection = Connection(
        space=space,
        api_name=str(api_name),
        parameter_names=_parameter_names(parameters),
        authenticated=bool(token),
        token_source=zerogpu_token.describe(),
    )
    logger.info(f"ZeroGPU : connecte a {connection.label}")
    return client, connection


def generate_chunk(client, connection: Connection, text: str,
                   params: catalogue.Params,
                   on_status: Optional[Callable] = None,
                   cancel_token: Optional[CancelToken] = None) -> ChunkResult:
    """Un morceau, un appel, deux durees mesurees.

    La bascule IN_QUEUE -> PROCESSING est ce qui separe l'attente de la
    generation. Tant qu'elle n'a pas eu lieu, `gpu_s` reste a zero : on ne
    devine pas le moment ou le GPU a demarre.
    """
    from gradio_client.utils import Status           # noqa: PLC0415

    configured = catalogue.space_spec().get("parameters") or []
    arguments = _arguments(connection.parameter_names, params, text, configured)
    check_arguments(connection.parameter_names, arguments, text)

    started = time.monotonic()
    processing_at: Optional[float] = None
    limits = catalogue.limits()

    try:
        job = client.submit(*arguments, api_name=connection.api_name)
    except Exception as error:
        raise ZeroGpuError(_explain(error, connection.space)) from error

    while True:
        # `is_cancelled` est une PROPRIETE de CancelToken, pas une methode :
        # l'appeler comme une fonction levait un AttributeError des le premier
        # tour de boucle, donc a chaque generation. Defaut reel, vu en usage.
        if cancel_token is not None and cancel_token.is_cancelled:
            job.cancel()
            raise CancelledError()

        status = job.status()
        code = getattr(status, "code", None)
        if code == Status.PROCESSING and processing_at is None:
            processing_at = time.monotonic()
        if on_status is not None:
            on_status(_describe_status(status, started, processing_at))

        if job.done():
            break

        waited = time.monotonic() - started
        if processing_at is None and waited > float(limits["queue_timeout_s"]):
            job.cancel()
            raise ZeroGpuError(
                "Le Space n'a pas démarré la génération dans le temps imparti "
                f"({int(waited)} s d'attente).\n\n"
                "Un Space gratuit s'endort après une période d'inactivité et met "
                "plusieurs dizaines de secondes à se réveiller ; la file "
                "d'attente donne aussi la priorité aux comptes PRO. Réessaie "
                "dans quelques minutes.")
        time.sleep(POLL_S)

    finished = time.monotonic()
    try:
        outcome = job.result()
    except Exception as error:
        raise ZeroGpuError(_explain(error, connection.space)) from error

    path = _audio_path(outcome)
    if not path:
        raise ZeroGpuError(
            "Le Space a répondu sans fichier audio exploitable.\n\n"
            f"Réponse reçue : {type(outcome).__name__}. Le Space a peut-être "
            "changé de sortie ; le détail est dans le journal de l'application.")

    if processing_at is None:
        # Le travail est passe entre deux sondages : tout compter en attente
        # serait faux, tout compter en generation aussi. On attribue la duree
        # a la generation, et on le dit dans le journal.
        logger.info("ZeroGPU : bascule file/GPU non observée, durée comptée en génération.")
        processing_at = started

    return ChunkResult(
        index=0, total=0, text=text, path=str(path),
        queue_s=max(0.0, processing_at - started),
        gpu_s=max(0.0, finished - processing_at),
    )


def _audio_path(outcome) -> str:
    """Le chemin du WAV, quelle que soit la forme rendue par le Space.

    Un composant Audio de Gradio rend un chemin ; certains Spaces rendent un
    tuple ou un dictionnaire. On accepte les trois plutot que de casser sur une
    forme legitime."""
    candidate = outcome
    if isinstance(candidate, (list, tuple)) and candidate:
        candidate = candidate[0]
    if isinstance(candidate, dict):
        candidate = candidate.get("path") or candidate.get("name") or ""
    if isinstance(candidate, (str, Path)) and str(candidate):
        return str(candidate) if Path(candidate).is_file() else ""
    return ""


def _describe_status(status, started: float, processing_at: Optional[float]) -> dict:
    """Etat brut -> dictionnaire simple, que l'interface affiche telle quelle."""
    from gradio_client.utils import Status           # noqa: PLC0415

    code = getattr(status, "code", None)
    now = time.monotonic()
    return {
        "phase": "gpu" if processing_at is not None else "file",
        "code": getattr(code, "name", str(code)),
        "rank": getattr(status, "rank", None),
        "queue_size": getattr(status, "queue_size", None),
        "eta": getattr(status, "eta", None),
        "queue_s": (processing_at - started) if processing_at else (now - started),
        "gpu_s": (now - processing_at) if processing_at else 0.0,
        "in_queue": code == Status.IN_QUEUE,
    }


def _explain(error: Exception, space: str) -> str:
    """Une panne distante, dite en francais, sans trace Python."""
    detail = str(error)
    lowered = detail.lower()

    if "quota" in lowered or "exceeded" in lowered:
        return ("Quota GPU épuisé sur Hugging Face.\n\n"
                "Le quota est journalier et se recharge 24 h après la première "
                "utilisation. Un compte gratuit dispose de 5 minutes de GPU par "
                "jour, contre 2 minutes sans jeton.\n\n"
                f"Message du serveur : {detail}")
    if "401" in detail or "unauthorized" in lowered or "invalid" in lowered and "token" in lowered:
        return ("Jeton Hugging Face refusé.\n\n"
                + zerogpu_token.missing_token_help())
    if "404" in detail or "not found" in lowered or "could not find" in lowered:
        return (f"Le Space « {space} » est introuvable.\n\n"
                "Vérifie son identifiant dans config/zerogpu.json. Un Space privé "
                "exige en plus un jeton ayant accès à ce dépôt.")
    if "429" in detail or "rate limit" in lowered:
        return ("Trop de requêtes envoyées à Hugging Face en peu de temps. "
                "Attends une minute avant de relancer.")
    if "timed out" in lowered or "timeout" in lowered:
        return ("Hugging Face n'a pas répondu à temps. Le Space est peut-être en "
                "train de démarrer : réessaie dans une minute.")
    if "connection" in lowered or "network" in lowered or "resolve" in lowered:
        return ("Impossible de joindre Hugging Face. Vérifie ta connexion "
                "Internet, puis réessaie.")
    return (f"Le Space « {space} » a renvoyé une erreur.\n\n"
            f"Détail technique : {detail}")
