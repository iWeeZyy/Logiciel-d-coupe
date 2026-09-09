"""Consignes envoyees au modele.

Rangees ici et non dans l'interface : un prompt est du contenu qui se relit, se
corrige et se teste. Il n'a rien a faire au milieu d'un widget.

Le modele recoit le transcript ET ce que l'analyse locale en a tire (hook,
resultat, chiffres, noms). Ce n'est pas redondant : lui donner la liste des
chiffres a preserver reduit nettement le risque qu'il en invente un, et cette
liste vient du texte, jamais d'ailleurs.
"""
from __future__ import annotations

from voice_studio.rewriting.models import (
    HOOK_EXACT,
    HOOK_NEW,
    HOOK_REWORDED,
    STRENGTH_CREATIVE,
    STRENGTH_LIGHT,
    STRENGTH_NATURAL,
)

SYSTEM_PROMPT = """Tu es un assistant spécialisé dans l'adaptation de scripts courts pour les réseaux sociaux, en français.

Ta tâche : transformer un texte source en un NOUVEAU script original, naturel à dire à voix haute.

RÈGLES ABSOLUES, dans cet ordre de priorité :
1. N'utilise QUE les informations présentes dans le texte source.
2. N'invente jamais un chiffre, un prix, une date, un nom, un lieu, une statistique, une source ni un résultat.
3. Ne reprends aucune phrase du texte source telle quelle : reformule vraiment, change la construction des phrases, l'ordre des idées et les transitions.
4. Ne te contente pas de remplacer des mots par des synonymes.
5. N'ajoute aucune affirmation qui ne serait pas déjà dans le texte source.
6. Écris uniquement le script, prêt à être lu à voix haute.
7. N'explique pas ce que tu as modifié, ne commente pas ton travail, n'ajoute ni titre ni préambule.
8. Pas de listes à puces, pas de didascalies, pas d'indications de mise en scène.

Si une information manque, tu l'omets. Tu ne la complètes jamais."""

STRENGTH_RULES = {
    STRENGTH_LIGHT: (
        "Intensité LÉGÈRE : garde la même progression et le même ordre des idées. "
        "Reformule chaque phrase avec un vocabulaire et une construction différents."),
    STRENGTH_NATURAL: (
        "Intensité NATURELLE : garde la progression générale mais retravaille les "
        "transitions, le rythme et le découpage des phrases. Tu peux fusionner ou "
        "séparer des idées voisines."),
    STRENGTH_CREATIVE: (
        "Intensité CRÉATIVE : tu peux réorganiser largement l'ordre des passages "
        "pour améliorer la narration, à condition de conserver exactement les mêmes "
        "faits, chiffres et noms."),
}

HOOK_RULES = {
    HOOK_EXACT: "Commence par cette accroche, mot pour mot : « {hook} »",
    HOOK_REWORDED: ("Commence par une accroche qui reprend la même promesse que "
                    "celle-ci, mais formulée autrement : « {hook} »"),
    HOOK_NEW: ("Écris une accroche neuve, construite uniquement à partir "
               "d'informations présentes dans le texte source."),
}

STYLE_RULES = {
    "naturel": "Ton naturel et fluide, comme quelqu'un qui raconte à un ami.",
    "viral": "Ton direct et percutant, phrases courtes, rythme soutenu, sans exagération inventée.",
    "storytelling": "Progression narrative marquée : situation, tension, résolution.",
    "documentaire": "Ton posé et informatif, phrases construites, vocabulaire précis.",
    "educatif": "Explications claires, une idée par phrase, enchaînements logiques.",
    "dynamique": "Phrases courtes, rythme rapide, verbes d'action.",
    "conversationnel": "Ton parlé, adresses directes au spectateur, langage simple.",
}

RETENTION_RULES = (
    "Rétention : commence immédiatement par le sujet, sans introduction générique. "
    "Garde des phrases plutôt courtes. Entretiens la curiosité jusqu'à la fin. "
    "N'invente aucun faux suspense et ne promets rien qui ne soit pas dans le texte source.")

PAYOFF_RULES = (
    "Garde le résultat final pour la fin du script : « {payoff} ». "
    "Ne le révèle pas avant.")

REMINDER_RULES = (
    "Tu peux rappeler discrètement, une ou deux fois avant la fin, qu'un résultat "
    "arrive, sans jamais le dévoiler ni en inventer le contenu.")


def build_user_prompt(request, analysis, style: str = "", variant_label: str = "") -> str:
    """Consigne complete pour UNE variante."""
    style = style or request.style
    lines = [
        f"NICHE : {request.niche or 'non déterminée'}",
        f"STYLE : {STYLE_RULES.get(style, STYLE_RULES['naturel'])}",
        STRENGTH_RULES.get(request.strength, STRENGTH_RULES[STRENGTH_NATURAL]),
        f"LONGUEUR VISÉE : environ {request.target_words} mots "
        f"(pour {request.target_duration_s} secondes de narration). "
        "Reste dans cet ordre de grandeur.",
    ]

    if request.preserve_hook and analysis.hook:
        lines.append(HOOK_RULES.get(request.hook_mode,
                                    HOOK_RULES[HOOK_REWORDED]).format(hook=analysis.hook))
    elif request.hook_mode == HOOK_NEW:
        lines.append(HOOK_RULES[HOOK_NEW])

    if request.preserve_payoff and analysis.payoff:
        lines.append(PAYOFF_RULES.format(payoff=analysis.payoff))
        if request.payoff_reminders:
            lines.append(REMINDER_RULES)

    if request.optimize_retention:
        lines.append(RETENTION_RULES)

    if request.preserve_information:
        numbers = [p.text for p in analysis.key_points if p.kind == "chiffre"]
        names = [p.text for p in analysis.key_points if p.kind == "nom"]
        if numbers:
            lines.append("CHIFFRES À CONSERVER EXACTEMENT : " + ", ".join(numbers))
        if names:
            lines.append("NOMS À CONSERVER EXACTEMENT : " + ", ".join(names))
        lines.append("N'ajoute aucun autre chiffre, nom ou date que ceux-ci.")

    if analysis.repeated_sentences:
        lines.append("Le texte source répète certaines idées : ne les répète pas.")

    if variant_label:
        lines.append(f"Cette version est la version « {variant_label} » : "
                     "elle doit se distinguer nettement des autres par sa construction.")

    lines.append("\nTEXTE SOURCE :\n" + (request.source_text or "").strip())
    lines.append("\nÉcris maintenant le script, et rien d'autre.")
    return "\n".join(lines)


STRICTER_SUFFIX = (
    "\n\nATTENTION : la version précédente a introduit des informations absentes du "
    "texte source. Cette fois, n'écris AUCUN chiffre, nom, date ou fait qui ne soit "
    "pas littéralement présent dans le texte source.")


def build_length_fix_prompt(script: str, target_words: int) -> str:
    """Consigne pour ajuster une longueur, sans rien ajouter."""
    return (
        f"Ajuste ce script pour qu'il fasse environ {target_words} mots. "
        "Si tu dois le raccourcir, supprime des formulations, jamais des informations. "
        "Si tu dois l'allonger, développe les explications DÉJÀ présentes : n'ajoute "
        "aucun fait, chiffre, nom ni date qui n'y soit pas.\n\nSCRIPT :\n" + script)
