"""Enchainement de la reecriture : analyse, generation, verification.

Cette couche ne connait ni Qt, ni llama.cpp : elle recoit un fournisseur et
s'en sert. C'est ce qui permet de tout tester sans modele, et de changer de
moteur plus tard sans toucher au reste.

Ordre des operations, et il compte :
1. analyser le transcript (sans modele) ;
2. construire une consigne par variante ;
3. generer ;
4. VERIFIER ce qui est revenu -- chiffres perdus, informations ajoutees,
   longueur -- avant de le montrer.

La verification n'est pas une formalite : c'est la seule chose qui distingue
une adaptation d'une invention, et son resultat est affiche a l'utilisateur.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Callable, Optional

from core.cancellation import CancelToken
from core.logging_setup import get_logger
from core.paths import user_data_dir
from utils.errors import CancelledError
from voice_studio.rewriting import analysis as analysis_module
from voice_studio.rewriting import prompts, validation
from voice_studio.rewriting.models import (
    NARRATION_SPEEDS,
    RewriteRequest,
    RewriteResult,
    RewriteVariant,
    STYLE_LABELS,
)
from voice_studio.rewriting.providers.base import ProviderError

logger = get_logger()

MIN_SOURCE_WORDS = 40

# Trois variantes, trois styles differents : c'est ce qui les rend reellement
# distinctes. L'utilisateur peut imposer un style unique, auquel cas les trois
# partagent le meme et se distinguent par leur construction.
DEFAULT_VARIANT_STYLES = ["naturel", "dynamique", "storytelling"]


class RewriteError(Exception):
    """Echec de reecriture, formule pour l'utilisateur."""


def cache_dir() -> Path:
    path = user_data_dir() / "voice_studio_data" / "rewrite_cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def cache_key(request: RewriteRequest, provider_name: str, model_name: str) -> str:
    """Empreinte de tout ce qui change le resultat."""
    payload = json.dumps({**request.to_dict(), "provider": provider_name,
                          "model": model_name}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def load_cached(key: str) -> dict | None:
    path = cache_dir() / f"{key}.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def save_cached(key: str, data: dict) -> None:
    try:
        (cache_dir() / f"{key}.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as error:                           # pragma: no cover - disque plein
        logger.warning(f"Reecriture non mise en cache : {error}")


def estimated_duration_s(word_count: int, speed: str) -> float:
    words_per_minute = NARRATION_SPEEDS.get(speed, NARRATION_SPEEDS["normale"])
    return round(word_count / words_per_minute * 60, 1)


def _clean_output(text: str) -> str:
    """Retire ce qu'un modele ajoute parfois malgre la consigne.

    Uniquement des enveloppes evidentes -- guillemets de code, un titre
    « Script : » en tete. Le texte lui-meme n'est jamais retouche : corriger
    silencieusement la sortie d'un modele reviendrait a cacher qu'il n'a pas
    suivi la consigne.
    """
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        parts = cleaned.split("```")
        cleaned = parts[1] if len(parts) > 1 else cleaned.strip("`")
        if "\n" in cleaned:
            first, rest = cleaned.split("\n", 1)
            if len(first) < 20 and not first.endswith((".", "!", "?")):
                cleaned = rest
    for prefix in ("script :", "script:", "voici le script :", "nouveau script :"):
        if cleaned.lower().startswith(prefix):
            cleaned = cleaned[len(prefix):].lstrip(" \n")
    return cleaned.strip()


def build_variant(text: str, label: str, style: str, request: RewriteRequest,
                  analysis) -> RewriteVariant:
    """Mesure et verifie une version produite."""
    cleaned = _clean_output(text)
    word_count = len(analysis_module.words(cleaned))
    warnings = validation.check(request.source_text, cleaned, request.target_words)
    index = validation.transformation_index(request.source_text, cleaned)

    source_numbers = list(dict.fromkeys(analysis_module.numbers_in(request.source_text)))
    kept_numbers = len(source_numbers) - len(validation.missing_numbers(
        request.source_text, cleaned))
    points = [p.text for p in analysis.key_points]
    kept_points = sum(1 for p in points
                      if analysis_module.fold(p) in analysis_module.fold(cleaned))

    return RewriteVariant(
        label=label,
        text=cleaned,
        style=style,
        word_count=word_count,
        estimated_duration_s=estimated_duration_s(word_count, request.narration_speed),
        transformation_index=index,
        preserved_numbers=max(0, kept_numbers),
        total_numbers=len(source_numbers),
        preserved_points=kept_points,
        total_points=len(points),
        warnings=warnings,
        confidence=validation.confidence(warnings, index, bool(analysis.hook),
                                         bool(analysis.payoff)),
    )


def rewrite(request: RewriteRequest, provider, cancel_token: Optional[CancelToken] = None,
            on_step: Optional[Callable[[int, int, str], None]] = None,
            model_name: str = "", use_cache: bool = True,
            stricter: bool = False,
            on_words: Optional[Callable[[int, int], None]] = None) -> RewriteResult:
    """Produit les variantes demandees.

    `stricter` renforce la consigne de fidelite : c'est ce qu'utilise le bouton
    « Régénérer sans ajout » quand une information a semble ajoutee.
    """
    source = (request.source_text or "").strip()
    if not source:
        raise RewriteError("Il n'y a pas de texte à réécrire : lance d'abord une transcription.")
    if len(analysis_module.words(source)) < MIN_SOURCE_WORDS:
        raise RewriteError(
            f"Le texte source est trop court pour être réécrit ({len(analysis_module.words(source))} "
            f"mots, il en faut au moins {MIN_SOURCE_WORDS}).")
    if provider is None or not provider.available():
        raise RewriteError(
            "Aucun modèle de réécriture n'est installé. Ouvre « Gérer les modèles » "
            "pour en télécharger un.")

    provider_name = getattr(provider, "name", "")
    key = cache_key(request, provider_name, model_name)
    if use_cache and not stricter:
        cached = load_cached(key)
        if cached:
            logger.info("Reecriture reprise du cache.")
            return _result_from_cache(cached, request)

    if on_step:
        on_step(1, 3, "Analyse du transcript")
    analysis = analysis_module.analyse(source)
    if not request.niche:
        request = RewriteRequest.from_dict({**request.to_dict(), "niche": analysis.niche})

    styles = DEFAULT_VARIANT_STYLES if request.style == "auto" else [request.style] * 3
    labels = ["Version A", "Version B", "Version C"]
    variants = []
    total = max(1, min(3, request.variants))

    for index in range(total):
        if cancel_token is not None:
            cancel_token.check()
        style = styles[index % len(styles)]
        label = f"{labels[index]} — {STYLE_LABELS.get(style, style)}"
        if on_step:
            # Le premier appel couvre aussi le chargement du modele, qui prend
            # plusieurs secondes : sans ce message, l'interface parait figee.
            on_step(2, 3, ("Chargement du modèle puis génération : " if index == 0
                           else "Génération : ") + label)
        user_prompt = prompts.build_user_prompt(request, analysis, style=style,
                                                variant_label=STYLE_LABELS.get(style, style))
        if stricter:
            user_prompt += prompts.STRICTER_SUFFIX
        try:
            text = provider.generate(prompts.SYSTEM_PROMPT, user_prompt,
                                     max_words=int(request.target_words * 1.4),
                                     cancel_token=cancel_token, on_progress=on_words)
        except CancelledError:
            raise
        except ProviderError as error:
            raise RewriteError(str(error)) from error
        variants.append(build_variant(text, label, style, request, analysis))

    if on_step:
        on_step(3, 3, "Vérification")
    result = RewriteResult(request=request, analysis=analysis, variants=variants,
                           provider=provider_name, model=model_name)
    if use_cache:
        save_cached(key, result.to_dict())
    return result


def _result_from_cache(data: dict, request: RewriteRequest) -> RewriteResult:
    from voice_studio.rewriting.models import KeyPoint, RewriteWarning, TranscriptAnalysis

    raw_analysis = data.get("analysis", {})
    analysis = TranscriptAnalysis(
        hook=raw_analysis.get("hook", ""), payoff=raw_analysis.get("payoff", ""),
        niche=raw_analysis.get("niche", ""),
        key_points=[KeyPoint(**p) for p in raw_analysis.get("key_points", [])],
        numbers=raw_analysis.get("numbers", []),
        word_count=raw_analysis.get("word_count", 0))
    variants = []
    for raw in data.get("variants", []):
        raw = dict(raw)
        raw["warnings"] = [RewriteWarning(**w) for w in raw.get("warnings", [])]
        variants.append(RewriteVariant(**raw))
    return RewriteResult(request=request, analysis=analysis, variants=variants,
                         created_at=data.get("created_at", ""),
                         provider=data.get("provider", ""), model=data.get("model", ""))


def adjust_length(variant: RewriteVariant, request: RewriteRequest, provider,
                  cancel_token: Optional[CancelToken] = None) -> RewriteVariant:
    """Rapproche une version de la duree visee, sans rien ajouter."""
    if provider is None or not provider.available():
        raise RewriteError("Aucun modèle de réécriture n'est disponible.")
    analysis = analysis_module.analyse(request.source_text)
    prompt = prompts.build_length_fix_prompt(variant.text, request.target_words)
    try:
        text = provider.generate(prompts.SYSTEM_PROMPT, prompt,
                                 max_words=int(request.target_words * 1.4),
                                 cancel_token=cancel_token)
    except ProviderError as error:
        raise RewriteError(str(error)) from error
    return build_variant(text, variant.label, variant.style, request, analysis)
