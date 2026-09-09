"""Réécriture originale : analyse, garde-fous factuels, variantes, modèles.

Aucun modèle de plusieurs gigaoctets n'est nécessaire : le service reçoit un
fournisseur d'essai (EchoProvider) qui rend un texte prépare. Tout le reste --
analyse du transcript, consignes envoyées, vérification de ce qui revient,
variantes, cache, annulation, gestion des modèles -- est réellement exécuté.
"""
import json
import os

import pytest

from core.cancellation import CancelToken
from utils.errors import CancelledError
from voice_studio import downloads, llm_models
from voice_studio.rewriting import analysis as A
from voice_studio.rewriting import service, validation
from voice_studio.rewriting.models import (
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    HOOK_EXACT,
    HOOK_NEW,
    RewriteRequest,
    STRENGTH_CREATIVE,
)
from voice_studio.rewriting.providers.echo import EchoProvider
from voice_studio.rewriting.providers.llama_cpp_provider import LlamaCppProvider

SOURCE = (
    "Attendez de voir le résultat final de cette rénovation. "
    "Cette maison semblait inhabitable quand Marc l'a achetée en 2019. "
    "Le chantier a duré 8 mois et la rénovation a coûté 50 000 euros au total. "
    "Ils ont refait la cuisine, la salle de bain et l'isolation complète du bâtiment. "
    "Le carrelage a été posé en trois semaines par une équipe de deux plaquistes. "
    "Au final, le résultat dépasse tout ce qu'on imaginait au départ."
)

FAITHFUL = (
    "Ce que Marc a fait de cette maison mérite le détour. Quand il l'achète en 2019, "
    "le bâtiment n'est plus habitable du tout. Huit mois de chantier plus tard, et "
    "50 000 euros engagés, plus rien ne ressemble à l'état d'origine : cuisine "
    "entièrement reprise, salle de bain refaite, isolation complète. Le carrelage "
    "à lui seul aura mobilisé deux plaquistes pendant trois semaines. Restez "
    "jusqu'au bout, le résultat dépasse tout ce qui était imaginé au départ."
)

INVENTED = FAITHFUL.replace("50 000 euros", "35 000 euros") + \
    " Julien, l'architecte, confirme que le prix du marché était de 80 000 euros en 2021."


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """Cache et dossier de modeles isoles."""
    monkeypatch.setattr(service, "cache_dir", lambda: tmp_path / "cache")
    (tmp_path / "cache").mkdir()
    monkeypatch.setattr(llm_models, "models_dir", lambda: tmp_path / "llm")
    return tmp_path


def _request(**kwargs):
    base = {"source_text": SOURCE, "target_duration_s": 61, "variants": 1, "style": "naturel"}
    base.update(kwargs)
    return RewriteRequest(**base)


# --------------------------------------------------------------- analyse

def test_the_hook_is_the_opening_promise():
    assert A.detect_hook(SOURCE).startswith("Attendez de voir")


def test_the_payoff_is_looked_for_at_the_end_only():
    # "Attendez de voir le résultat" est une ANNONCE au début, pas le résultat.
    assert A.detect_payoff(SOURCE).startswith("Au final")


def test_nothing_is_invented_when_there_is_no_payoff():
    plat = "Je vous montre trois outils. Le premier sert à visser. Le deuxième à percer."

    assert A.detect_payoff(plat) == ""
    assert "résultat final" in A.analyse(plat).missing


def test_the_numbers_are_extracted_with_their_sentence():
    points = {p.text: p for p in A.key_points(SOURCE) if p.kind == "chiffre"}

    assert set(points) == {"2019", "8", "50000"}
    assert "50 000 euros" in points["50000"].sentence


def test_the_niche_is_detected_from_the_vocabulary():
    niche, confidence = A.detect_niche(SOURCE)

    assert niche == "Rénovation immobilière"
    assert confidence > 0


def test_an_unknown_subject_gets_no_niche_rather_than_a_wrong_one():
    assert A.detect_niche("Bonjour, ça va ? Oui et toi ? Très bien merci.") == ("", 0.0)


def test_repeated_sentences_are_spotted():
    texte = "Le chantier a duré huit mois. Autre chose. Le chantier a duré huit mois."

    assert A.repeated_sentences(texte)


# ----------------------------------------------------- garde-fous factuels

def test_an_invented_price_is_caught():
    warnings = validation.check(SOURCE, INVENTED)
    messages = " ".join(w.message for w in warnings)

    assert "35000" in messages
    assert any(w.kind == "chiffre_ajoute" for w in warnings)


def test_an_invented_name_is_caught():
    assert "Julien" in " ".join(w.message for w in validation.check(SOURCE, INVENTED))


def test_an_invented_date_is_caught():
    assert any(w.kind == "date_ajoutee" for w in validation.check(SOURCE, INVENTED))


def test_a_faithful_rewrite_raises_nothing():
    assert validation.check(SOURCE, FAITHFUL) == []


def test_a_number_written_in_letters_is_not_a_lost_number():
    # "Huit mois" reprend bien le 8 du transcript.
    assert "8" not in validation.missing_numbers(SOURCE, FAITHFUL)


def test_the_transformation_index_says_what_it_measures():
    assert validation.transformation_index(SOURCE, SOURCE) == 0.0
    assert validation.transformation_index(SOURCE, FAITHFUL) > 0.8


def test_the_confidence_falls_when_something_looks_invented():
    warnings = validation.check(SOURCE, INVENTED)
    index = validation.transformation_index(SOURCE, INVENTED)

    assert validation.confidence(warnings, index, True, True) == CONFIDENCE_LOW


def test_the_confidence_is_high_on_a_clean_rewrite():
    warnings = validation.check(SOURCE, FAITHFUL)
    index = validation.transformation_index(SOURCE, FAITHFUL)

    assert validation.confidence(warnings, index, True, True) == CONFIDENCE_HIGH


# ------------------------------------------------------------- duree

def test_the_word_target_follows_the_narration_speed():
    fast = _request(narration_speed="rapide").target_words
    slow = _request(narration_speed="lente").target_words

    assert slow < _request().target_words < fast


def test_the_estimated_duration_uses_the_same_rule():
    words = _request().target_words

    assert service.estimated_duration_s(words, "normale") == pytest.approx(61, abs=2)


def test_a_script_much_too_short_is_signalled():
    warnings = validation.check(SOURCE, "Trois mots seulement.", target_words=147)

    assert any(w.kind == "trop_court" for w in warnings)


# ----------------------------------------------------------- generation

def test_a_rewrite_produces_a_measured_variant(isolated):
    # 30 secondes visees : c'est l'ordre de grandeur du texte d'exemple, sinon
    # l'avertissement de longueur se declenche a juste titre.
    provider = EchoProvider(replies=[FAITHFUL])

    result = service.rewrite(_request(target_duration_s=30), provider, use_cache=False)

    assert result.ok
    variant = result.variants[0]
    assert variant.word_count > 50
    assert variant.total_numbers == 3 and variant.preserved_numbers == 3
    assert variant.warnings == []


def test_three_variants_use_three_different_styles(isolated):
    provider = EchoProvider(replies=[FAITHFUL] * 3)

    result = service.rewrite(_request(variants=3, style="auto"), provider, use_cache=False)

    assert len(result.variants) == 3
    assert len({v.style for v in result.variants}) == 3


def test_the_prompt_carries_the_numbers_to_preserve(isolated):
    provider = EchoProvider(replies=[FAITHFUL])

    service.rewrite(_request(), provider, use_cache=False)

    prompt = provider.calls[0]["user"]
    assert "50000" in prompt.replace(" ", "") or "50 000" in prompt
    assert "N'ajoute aucun autre chiffre" in prompt
    assert SOURCE in prompt


def test_the_exact_hook_mode_asks_for_the_exact_sentence(isolated):
    provider = EchoProvider(replies=[FAITHFUL])

    service.rewrite(_request(hook_mode=HOOK_EXACT), provider, use_cache=False)

    assert "mot pour mot" in provider.calls[0]["user"]


def test_the_new_hook_mode_forbids_inventing(isolated):
    provider = EchoProvider(replies=[FAITHFUL])

    service.rewrite(_request(hook_mode=HOOK_NEW, preserve_hook=False), provider,
                    use_cache=False)

    assert "uniquement à partir" in provider.calls[0]["user"]


def test_the_creative_strength_still_forbids_inventing(isolated):
    provider = EchoProvider(replies=[FAITHFUL])

    service.rewrite(_request(strength=STRENGTH_CREATIVE), provider, use_cache=False)

    prompt = provider.calls[0]["user"]
    assert "CRÉATIVE" in prompt
    assert "mêmes faits" in prompt


def test_the_niche_is_filled_in_when_not_chosen(isolated):
    provider = EchoProvider(replies=[FAITHFUL])

    result = service.rewrite(_request(niche=""), provider, use_cache=False)

    assert result.request.niche == "Rénovation immobilière"


def test_a_model_that_adds_information_is_flagged_not_hidden(isolated):
    provider = EchoProvider(replies=[INVENTED])

    result = service.rewrite(_request(), provider, use_cache=False)

    variant = result.variants[0]
    assert any(w.kind.endswith(("_ajoute", "_ajoutee")) for w in variant.warnings)
    assert variant.confidence == CONFIDENCE_LOW
    # Le texte est quand meme rendu : c'est a l'utilisateur de decider.
    assert variant.text


def test_a_wrapped_answer_is_unwrapped(isolated):
    provider = EchoProvider(replies=["```\nScript :\n" + FAITHFUL + "\n```"])

    result = service.rewrite(_request(), provider, use_cache=False)

    assert result.variants[0].text.startswith("Ce que Marc")


# ------------------------------------------------------------- refus

def test_an_empty_transcript_is_refused(isolated):
    with pytest.raises(service.RewriteError) as excinfo:
        service.rewrite(_request(source_text="  "), EchoProvider(replies=["x"]))

    assert "transcription" in str(excinfo.value)


def test_a_transcript_too_short_is_refused(isolated):
    with pytest.raises(service.RewriteError) as excinfo:
        service.rewrite(_request(source_text="Trois petits mots."), EchoProvider(replies=["x"]))

    assert "trop court" in str(excinfo.value)


def test_without_a_provider_the_user_is_told_what_to_do(isolated):
    with pytest.raises(service.RewriteError) as excinfo:
        service.rewrite(_request(), None)

    assert "Gérer les modèles" in str(excinfo.value)


def test_a_provider_error_becomes_a_readable_message(isolated):
    from voice_studio.rewriting.providers.base import ProviderError

    provider = EchoProvider(fail_with=ProviderError("Mémoire insuffisante pour ce modèle."))

    with pytest.raises(service.RewriteError) as excinfo:
        service.rewrite(_request(), provider, use_cache=False)

    assert "Mémoire insuffisante" in str(excinfo.value)


def test_cancelling_stops_before_generating(isolated):
    token = CancelToken()
    token.cancel()
    provider = EchoProvider(replies=[FAITHFUL])

    with pytest.raises(CancelledError):
        service.rewrite(_request(), provider, cancel_token=token, use_cache=False)

    assert provider.calls == []


# -------------------------------------------------------------- cache

def test_the_same_request_is_not_regenerated(isolated):
    provider = EchoProvider(replies=[FAITHFUL])
    first = service.rewrite(_request(), provider, model_name="essai")

    second = service.rewrite(_request(), EchoProvider(replies=[]), model_name="essai")

    assert second.variants[0].text == first.variants[0].text


def test_changing_a_setting_regenerates(isolated):
    service.rewrite(_request(), EchoProvider(replies=[FAITHFUL]), model_name="essai")

    other = service.rewrite(_request(target_duration_s=30),
                            EchoProvider(replies=["Un texte plus court mais complet."]),
                            model_name="essai")

    assert other.variants[0].text.startswith("Un texte plus court")


def test_regenerating_without_additions_bypasses_the_cache(isolated):
    service.rewrite(_request(target_duration_s=30), EchoProvider(replies=[INVENTED]),
                    model_name="essai")
    provider = EchoProvider(replies=[FAITHFUL])

    result = service.rewrite(_request(target_duration_s=30), provider, model_name="essai",
                             stricter=True)

    assert result.variants[0].warnings == []
    assert "n'écris AUCUN chiffre" in provider.calls[0]["user"]


# ------------------------------------------------------- gestion modeles

def test_no_model_installed_is_a_normal_state(isolated):
    assert llm_models.installed_keys() == []
    assert LlamaCppProvider(model_path="").available() is False


def test_the_catalogue_describes_what_it_offers():
    models = llm_models.catalogue()

    assert len(models) >= 2
    assert all(m.url and m.licence and m.ram_gb for m in models)
    assert {m.parameters for m in models} == {"7B", "12B"}


def test_a_truncated_file_is_not_seen_as_installed(isolated):
    (isolated / "llm").mkdir()
    (isolated / "llm" / "faux.gguf").write_bytes(b"0" * 1024)

    assert llm_models.installed_keys() == []


def test_a_model_can_be_installed_and_removed(isolated, monkeypatch):
    # Telechargement reel, contre un fichier local servi par le module de
    # telechargement partage.
    source = isolated / "depot"
    source.mkdir()
    fake_model = source / "modele.gguf"
    fake_model.write_bytes(b"G" * (60 * 1024 * 1024))
    monkeypatch.setattr(llm_models, "describe", lambda key: llm_models.CatalogueModel(
        key=key, label="Essai", url=fake_model.as_uri(), size_gb=0.06, ram_gb=4))

    llm_models.install("essai")

    assert llm_models.installed_keys() == ["essai"]
    assert llm_models.installed_size_bytes() > 50 * 1024 * 1024
    assert llm_models.remove("essai") is True
    assert llm_models.installed_keys() == []


def test_a_model_without_an_address_is_refused(isolated, monkeypatch):
    monkeypatch.setattr(llm_models, "describe", lambda key: llm_models.CatalogueModel(
        key=key, label="Sans adresse"))

    with pytest.raises(llm_models.LlmModelError) as excinfo:
        llm_models.install("sans-adresse")

    assert "catalogue" in str(excinfo.value).lower()


def test_a_download_that_returns_junk_is_refused(isolated, monkeypatch):
    source = isolated / "depot"
    source.mkdir()
    tiny = source / "tiny.gguf"
    tiny.write_bytes(b"pas un modele")
    monkeypatch.setattr(llm_models, "describe", lambda key: llm_models.CatalogueModel(
        key=key, label="Trop petit", url=tiny.as_uri()))

    with pytest.raises(llm_models.LlmModelError):
        llm_models.install("trop-petit")

    assert llm_models.installed_keys() == []


# ------------------------------------------------------------ ressources

def test_the_resources_are_read_or_declared_unknown():
    machine = llm_models.resources()

    assert machine.cpu_count and machine.cpu_count > 0
    # RAM lue sur Windows et Linux ; ailleurs, inconnue -- jamais devinee.
    assert machine.total_ram_gb is None or machine.total_ram_gb > 0


def test_without_a_memory_reading_no_recommendation_is_made():
    from voice_studio.llm_models import CatalogueModel, Resources, UNKNOWN, verdict

    unknown = Resources()

    assert verdict(CatalogueModel(key="x", label="x", ram_gb=8), unknown) == UNKNOWN


def test_the_verdict_follows_the_available_memory():
    from voice_studio.llm_models import (
        CatalogueModel,
        INSUFFICIENT,
        POSSIBLE,
        RECOMMENDED,
        Resources,
        verdict,
    )

    model = CatalogueModel(key="x", label="x", ram_gb=12)

    assert verdict(model, Resources(total_ram_gb=32, available_ram_gb=16)) == RECOMMENDED
    assert verdict(model, Resources(total_ram_gb=16, available_ram_gb=10)) == POSSIBLE
    assert verdict(model, Resources(total_ram_gb=8, available_ram_gb=4)) == INSUFFICIENT


# ------------------------------------------------------- moteur llama.cpp

def test_the_engine_reports_its_state_without_a_model():
    provider = LlamaCppProvider(model_path="")
    description = provider.describe()

    assert description["provider"] == "llama.cpp"
    assert "library" in description


def test_asking_a_missing_model_says_what_to_do(tmp_path):
    provider = LlamaCppProvider(model_path=str(tmp_path / "absent.gguf"))

    with pytest.raises(Exception) as excinfo:
        provider.generate("système", "consigne")

    assert "Gérer les modèles" in str(excinfo.value)


@pytest.mark.skipif(
    not os.environ.get("CLIPFARMING_TEST_LLAMA"),
    reason="charge reellement llama.cpp : mettre CLIPFARMING_TEST_LLAMA=1 pour l'activer. "
           "Le chargement execute du code natif, et un processeur sans les instructions "
           "attendues arrete le processus -- ce qui est le cas de la machine de "
           "developpement.")
def test_a_damaged_model_gives_a_readable_message(tmp_path):
    if not LlamaCppProvider.library_available():
        pytest.skip("llama-cpp-python n'est pas installé")
    broken = tmp_path / "casse.gguf"
    broken.write_bytes(b"ceci n'est pas un GGUF" * 1000)

    with pytest.raises(Exception) as excinfo:
        LlamaCppProvider(model_path=str(broken)).generate("s", "u")

    assert "réinstalle" in str(excinfo.value).lower()


def test_only_one_model_stays_in_memory(tmp_path):
    # Deux fournisseurs successifs ne doivent pas laisser deux modeles charges.
    LlamaCppProvider._shared_model = object()
    LlamaCppProvider._shared_path = "ancien"

    LlamaCppProvider(model_path=str(tmp_path / "autre.gguf")).unload()

    assert LlamaCppProvider._shared_model is None
