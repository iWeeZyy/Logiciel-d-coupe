"""Enchainement d'une publication sur une ou plusieurs plateformes.

DEUX REGLES GOUVERNENT CE FICHIER.

1. Les plateformes sont INDEPENDANTES. Un echec TikTok ne doit pas annuler une
   reussite Instagram, ni l'inverse. Chaque plateforme a sa tentative, son
   enregistrement et son message ; il n'existe nulle part de "publication
   globale" qui echouerait d'un bloc (section 13).

2. Il y a TOUJOURS une issue. Quand la publication directe n'est pas disponible
   -- ce qui est le cas par defaut, faute d'application developpeur validee --
   le clip est EXPORTE, avec sa legende et ses hashtags. L'operation est alors
   enregistree comme un export, pas comme un echec : rien n'a rate, la video
   est prete a etre deposee a la main.
"""
from __future__ import annotations

from core.logging_setup import get_logger
from publishing import export as export_module
from publishing.models import (
    STATUS_EXPORTED,
    STATUS_FAILED,
    STATUS_PUBLISHED,
    STATUS_RUNNING,
    PublicationRecord,
)

logger = get_logger()


def default_adapters() -> dict:
    from publishing.platforms.tiktok import adapters

    return adapters()


def publish(draft, platforms, *, history, export_root, adapters=None,
            on_progress=None, cancel_token=None) -> list:
    """Publie ou exporte, plateforme par plateforme, et renvoie les traces.

    `on_progress(platform, fraction)` sert a l'interface. `fraction` peut valoir
    None quand l'avancement n'est pas connu -- une barre qui avance au hasard
    vaut moins qu'une barre qui l'assume.
    """
    adapters = adapters if adapters is not None else default_adapters()
    records: list[PublicationRecord] = []

    for platform in platforms:
        if cancel_token is not None and cancel_token.is_cancelled:
            break

        record = history.record(PublicationRecord(
            clip_path=draft.clip_path,
            content_id=draft.content_id,
            platform=platform,
            caption=draft.full_text_for(platform),
            hashtags=list(draft.hashtags),
            status=STATUS_RUNNING,
        ))
        adapter = adapters.get(platform)
        state = adapter.status() if adapter is not None else None
        account = getattr(state, "account", "") if state else ""
        record.account = account

        if adapter is None or state is None or not state.can_publish:
            # Pas d'echec : l'export est un resultat, pas un lot de consolation.
            reason = getattr(state, "reason", "Plateforme non disponible.")
            try:
                result = export_module.export(draft, platform, export_root)
            except OSError as error:
                logger.exception("Export impossible")
                records.append(history.finish(record, STATUS_FAILED,
                                              error=f"Export impossible : {error}"))
                continue
            logger.info(f"{platform} : publication directe indisponible ({reason}), "
                        f"clip exporté dans {result.directory}")
            records.append(history.finish(record, STATUS_EXPORTED,
                                          export_dir=result.directory, error=reason))
            continue

        try:
            outcome = adapter.publish(
                draft,
                on_progress=(lambda f, p=platform: on_progress(p, f)) if on_progress else None,
                cancel_token=cancel_token,
            )
        except Exception as error:      # noqa: BLE001
            logger.exception(f"Publication {platform} : échec inattendu")
            records.append(history.finish(record, STATUS_FAILED,
                                          error=f"Erreur inattendue : {error}"))
            continue

        if outcome.ok:
            records.append(history.finish(record, STATUS_PUBLISHED, url=outcome.url))
        else:
            records.append(history.finish(record, STATUS_FAILED, error=outcome.error))

    return records


def performance_features(record, draft, analysis=None) -> dict:
    """Ce qu'une publication apporte a l'apprentissage existant (section 15).

    Aucun nouveau systeme : ce dictionnaire parle le vocabulaire de
    performance/models.py, et les valeurs inconnues sont ABSENTES plutot que
    mises a zero -- un zero invente passerait pour une mesure.

    Les vues, likes et commentaires ne sont pas ici : ils n'existent pas encore
    au moment de la publication. Ils se saisissent plus tard, dans l'ecran
    d'apprentissage deja present.
    """
    features = {
        "clip_id": record.content_id or record.clip_path,
        "platform": record.platform,
        "published_at": record.finished_at or record.created_at,
        "status": record.status,
        "caption": record.caption,
        "hashtags": list(record.hashtags),
    }
    if draft is not None:
        if draft.clip_title:
            features["title"] = draft.clip_title
        if draft.creator_label:
            features["creator"] = draft.creator_label
        if draft.duration_s is not None:
            features["duration"] = draft.duration_s
    if record.url:
        features["url"] = record.url
    if analysis is not None:
        from radar.analysis.handoff import to_performance_features

        for key, value in to_performance_features(analysis).items():
            features.setdefault(key, value)
    return features
