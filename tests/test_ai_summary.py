"""Texte sous le titre redige par Claude (news_story/ai_summary.py) : la
requete envoyee, la lecture de la reponse, les erreurs, le cache, et le
retour a l'extraction de phrases sans cle. Aucun appel reseau reel."""
import json

import pytest

from news_story import ai_summary

_PAGE = """<html><head><meta charset="utf-8"></head><body><article>
<p>Plus d'un an après la sortie du Superman de James Gunn, David Corenswet confirme que l'Homme d'Acier portera un costume retravaillé dans la suite, Man of Tomorrow.</p>
<h3>Un costume réingéniéré « taillé pour la bagarre »</h3>
<p>David Corenswet confirme que les équipes de James Gunn sont repassées par l'atelier design pour offrir au héros un équipement enfin adapté aux cascades d'envergure. Le costume a été réingéniéré pour être plus fonctionnel.</p>
<div class="wpd-comment"><p>Commentaire d'un lecteur qui ne doit pas partir chez Claude.</p></div>
</article></body></html>"""
_TITLE = "Superman changera déjà de costume dans la suite de 2027, et pour une bonne raison"


def _message(text, found=True, stop="end_turn", question="Tu iras le voir ?"):
    import json as _json

    return {"id": "msg_test", "type": "message", "role": "assistant",
            "model": "claude-sonnet-5-5", "stop_reason": stop, "stop_sequence": None,
            "content": [{"type": "text",
                         "text": _json.dumps({"texte": text, "reponse_trouvee": found,
                                              "question": question})}],
            "usage": {"input_tokens": 10, "output_tokens": 10}}


@pytest.fixture
def posted(monkeypatch):
    """Faux serveur de l'API : les requetes passent par le vrai SDK
    `anthropic` jusqu'a la couche HTTP. `response[0]` = (statut, corps)."""
    import httpx2

    calls = []
    response = [(200, _message(
        "Le costume a été réingéniéré pour être plus fonctionnel et adapté aux cascades."))]

    def handler(request):
        calls.append({"url": str(request.url), "headers": dict(request.headers),
                      "json": json.loads(request.content)})
        status, body = response[0]
        return httpx2.Response(status, json=body)

    monkeypatch.setattr(ai_summary, "_HTTP_CLIENT", httpx2.Client(transport=httpx2.MockTransport(handler)))
    return calls, response


class TestCle:
    def test_sans_cle_rien_n_est_configure(self):
        assert not ai_summary.is_configured()
        assert ai_summary.try_summarize(_TITLE, "", _PAGE) is None

    def test_cle_rangee_dans_le_coffre_jamais_dans_un_fichier(self):
        assert ai_summary.save_api_key("  sk-ant-abcdefghijklmnop1234 \n")
        assert ai_summary.load_api_key() == "sk-ant-abcdefghijklmnop1234"
        assert not ai_summary.KEY_FILE.exists()
        ai_summary.save_api_key("")
        assert not ai_summary.is_configured()

    def test_sans_coffre_la_cle_n_est_ecrite_nulle_part(self, monkeypatch):
        from publishing import tokens

        monkeypatch.setattr(tokens, "_keyring", lambda: None)
        assert ai_summary.save_api_key("sk-ant-abcdefghijklmnop1234") is False
        assert not ai_summary.KEY_FILE.exists()
        assert not ai_summary.is_configured()

    def test_variable_d_environnement_prioritaire(self, monkeypatch):
        ai_summary.save_api_key("sk-ant-coffre-xxxxxxxx")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env-yyyyyyyyyy")
        assert ai_summary.load_api_key() == "sk-ant-env-yyyyyyyyyy"

    def test_cle_masquee(self):
        masked = ai_summary.mask_key("sk-ant-api03-SECRETSECRETSECRET-wxyz")
        assert masked.startswith("sk-ant-") and masked.endswith("wxyz")
        assert "SECRET" not in masked

    def test_modele_inconnu_ignore(self):
        ai_summary.MODEL_FILE.write_text("gpt-quelquechose", encoding="utf-8")
        assert ai_summary.load_model() == ai_summary.DEFAULT_MODEL
        ai_summary.save_model("claude-sonnet-5-5")
        assert ai_summary.load_model() == "claude-sonnet-5-5"


class TestRequete:
    def test_requete_envoyee(self, posted):
        calls, _ = posted
        summary = ai_summary.summarize(_TITLE, "Un chapô.", _PAGE, url="https://x/1",
                                       api_key="sk-ant-test-1234567890")
        assert summary.text.startswith("Le costume a été réingéniéré")
        call = calls[0]
        assert call["url"].endswith("/v1/messages")
        assert call["headers"]["x-api-key"] == "sk-ant-test-1234567890"
        body = call["json"]
        assert body["model"] == ai_summary.DEFAULT_MODEL
        # Pas d'outil force : refuse (400) par Claude Sonnet 5.5.
        assert "tool_choice" not in body and "tools" not in body
        assert body["output_config"]["format"]["type"] == "json_schema"
        assert body["output_config"]["format"]["schema"]["additionalProperties"] is False
        user = body["messages"][0]["content"]
        assert _TITLE in user and "## Un costume réingéniéré" in user
        assert "Commentaire d'un lecteur" not in user
        assert "N'invente rien" in body["system"]
        assert "réponse explicite" in body["system"]

    def test_modele_choisi(self, posted):
        calls, _ = posted
        ai_summary.save_model("claude-sonnet-5-5")
        ai_summary.summarize(_TITLE, "", _PAGE, url="https://x/h", api_key="sk-ant-k-1234567890")
        assert calls[0]["json"]["model"] == "claude-sonnet-5-5"

    def test_cache_evite_un_second_appel(self, posted):
        calls, _ = posted
        for _ in range(2):
            ai_summary.summarize(_TITLE, "", _PAGE, url="https://x/2", api_key="sk-ant-k-1234567890")
        assert len(calls) == 1
        data = json.loads(ai_summary.CACHE_FILE.read_text(encoding="utf-8"))
        assert "sk-ant" not in json.dumps(data)          # jamais la cle dans le cache

    def test_page_vide_refusee_sans_appel(self, posted):
        calls, _ = posted
        with pytest.raises(ai_summary.AiSummaryError):
            ai_summary.summarize(_TITLE, "", "<html></html>", api_key="sk-ant-k-1234567890")
        assert calls == []


def _error(kind, message):
    return {"type": "error", "error": {"type": kind, "message": message}}


class TestErreurs:
    @pytest.mark.parametrize("status, payload, expected", [
        (401, _error("authentication_error", "invalid x-api-key"), "refusée"),
        (404, _error("not_found_error", "model not found"), "indisponible"),
        (400, _error("invalid_request_error", "Your credit balance is too low"), "Crédit"),
        (400, _error("invalid_request_error", "output_config.effort: invalid value"),
         r"\(400\) : output_config\.effort: invalid value"),
    ])
    def test_messages_lisibles(self, posted, status, payload, expected):
        _, response = posted
        response[0] = (status, payload)
        with pytest.raises(ai_summary.AiSummaryError, match=expected):
            ai_summary.summarize(_TITLE, "", _PAGE, url=f"https://x/{status}{expected}",
                                 api_key="sk-ant-k-1234567890")

    @pytest.mark.parametrize("stop", ["refusal", "max_tokens"])
    def test_reponse_inutilisable(self, posted, stop):
        _, response = posted
        response[0] = (200, _message("x", stop=stop))
        with pytest.raises(ai_summary.AiSummaryError):
            ai_summary.summarize(_TITLE, "", _PAGE, url=f"https://x/{stop}",
                                 api_key="sk-ant-k-1234567890")

    def test_reseau_coupe_sans_cle_dans_le_message(self, monkeypatch):
        import httpx2

        def boom(request):
            raise httpx2.ConnectError("x-api-key: sk-ant-secret-0000")

        monkeypatch.setattr(ai_summary, "_HTTP_CLIENT", httpx2.Client(transport=httpx2.MockTransport(boom)))
        monkeypatch.setattr(ai_summary, "MAX_RETRIES", 0)
        with pytest.raises(ai_summary.AiSummaryError) as info:
            ai_summary.summarize(_TITLE, "", _PAGE, url="https://x/n", api_key="sk-ant-secret-0000")
        assert "sk-ant" not in str(info.value)


class TestTopDuJour:
    def test_texte_de_claude_utilise(self, posted, monkeypatch):
        from types import SimpleNamespace

        from news_story import daily_top

        ai_summary.save_api_key("sk-ant-k-1234567890")
        monkeypatch.setattr("news_story.image_fetcher.fetch_article_html", lambda url: _PAGE)
        monkeypatch.setattr("news_story.image_fetcher.candidates_for_article", lambda *a: [])
        article = SimpleNamespace(title=_TITLE, url="https://x/top", feed_image_url="",
                                  summary="Un chapô.", source_label="Numerama")
        item = daily_top.prepare_item(article)
        assert item.subtitle.startswith("Le costume a été réingéniéré")
        assert item.ai_error == ""

    def test_erreur_claude_retour_a_l_extraction(self, posted, monkeypatch):
        from types import SimpleNamespace

        from news_story import daily_top

        _, response = posted
        response[0] = (401, _error("authentication_error", "invalid x-api-key"))
        ai_summary.save_api_key("sk-ant-k-1234567890")
        monkeypatch.setattr("news_story.image_fetcher.fetch_article_html", lambda url: _PAGE)
        monkeypatch.setattr("news_story.image_fetcher.candidates_for_article", lambda *a: [])
        article = SimpleNamespace(title=_TITLE, url="https://x/top2", feed_image_url="",
                                  summary="Un chapô assez long pour servir de texte sous le titre.",
                                  source_label="Numerama")
        item = daily_top.prepare_item(article)
        assert "refusée" in item.ai_error
        assert item.subtitle                              # texte tire de l'article


def test_question_pour_la_legende(posted):
    calls, _ = posted
    summary = ai_summary.summarize(_TITLE, "", _PAGE, url="https://x/q", api_key="sk-ant-k-1234567890")
    assert summary.question == "Tu iras le voir ?"
    assert "question" in calls[0]["json"]["output_config"]["format"]["schema"]["required"]
    again = ai_summary.summarize(_TITLE, "", _PAGE, url="https://x/q", api_key="sk-ant-k-1234567890")
    assert again.question == "Tu iras le voir ?" and len(calls) == 1      # gardee en cache


def test_question_en_fin_de_legende():
    from news_story.caption import build_caption

    caption = build_caption("Un titre", "Un résumé.", "AlloCiné", "cinema",
                            question="Tu iras le voir ?")
    assert caption.endswith("Tu iras le voir ? 👇\n\nSource : AlloCiné")
