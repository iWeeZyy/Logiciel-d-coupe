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


class _Response:
    def __init__(self, status, payload):
        self.status_code, self._payload = status, payload

    def json(self):
        return self._payload


def _tool_payload(text, found=True):
    return {"content": [{"type": "tool_use", "name": "texte_sous_titre",
                         "input": {"texte": text, "reponse_trouvee": found}}]}


@pytest.fixture
def posted(monkeypatch):
    import requests

    calls = []

    def fake_post(url, json=None, timeout=None, headers=None):
        calls.append({"url": url, "json": json, "headers": headers})
        return calls_response[0]

    calls_response = [_Response(200, _tool_payload(
        "Le costume a été réingéniéré pour être plus fonctionnel et adapté aux cascades."))]
    monkeypatch.setattr(requests, "post", fake_post)
    return calls, calls_response


class TestCle:
    def test_sans_cle_rien_n_est_configure(self):
        assert not ai_summary.is_configured()
        assert ai_summary.try_summarize(_TITLE, "", _PAGE) is None

    def test_cle_enregistree_puis_supprimee(self):
        ai_summary.save_api_key("  sk-ant-abcdefghijklmnop1234 \n")
        assert ai_summary.load_api_key() == "sk-ant-abcdefghijklmnop1234"
        ai_summary.save_api_key("")
        assert not ai_summary.is_configured()

    def test_variable_d_environnement_prioritaire(self, monkeypatch):
        ai_summary.save_api_key("sk-ant-fichier-xxxxxxxx")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env-yyyyyyyyyy")
        assert ai_summary.load_api_key() == "sk-ant-env-yyyyyyyyyy"

    def test_cle_masquee(self):
        masked = ai_summary.mask_key("sk-ant-api03-SECRETSECRETSECRET-wxyz")
        assert masked.startswith("sk-ant-") and masked.endswith("wxyz")
        assert "SECRET" not in masked

    def test_modele_inconnu_ignore(self):
        ai_summary.MODEL_FILE.write_text("gpt-quelquechose", encoding="utf-8")
        assert ai_summary.load_model() == ai_summary.DEFAULT_MODEL
        ai_summary.save_model("claude-haiku-5-5")
        assert ai_summary.load_model() == "claude-haiku-5-5"


class TestRequete:
    def test_corps_envoye_sans_commentaires_et_avec_consignes(self, posted):
        calls, _ = posted
        summary = ai_summary.summarize(_TITLE, "Un chapô.", _PAGE, url="https://x/1",
                                       api_key="sk-ant-test-1234567890")
        assert summary.text.startswith("Le costume a été réingéniéré")
        call = calls[0]
        assert call["url"] == ai_summary.API_URL
        assert call["headers"]["x-api-key"] == "sk-ant-test-1234567890"
        body = call["json"]
        assert body["model"] == ai_summary.DEFAULT_MODEL
        assert body["tool_choice"] == {"type": "tool", "name": "texte_sous_titre"}
        user = body["messages"][0]["content"]
        assert _TITLE in user and "## Un costume réingéniéré" in user
        assert "Commentaire d'un lecteur" not in user
        assert "N'invente rien" in body["system"]
        assert "réponse explicite" in body["system"]

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


class TestErreurs:
    @pytest.mark.parametrize("status, payload, expected", [
        (401, {"error": {"message": "invalid x-api-key"}}, "refusée"),
        (429, {}, "Limite"),
        (400, {"error": {"message": "Your credit balance is too low"}}, "Crédit"),
        (500, {"error": {"message": "boom"}}, "500"),
    ])
    def test_messages_lisibles(self, posted, status, payload, expected):
        _, response = posted
        response[0] = _Response(status, payload)
        with pytest.raises(ai_summary.AiSummaryError, match=expected):
            ai_summary.summarize(_TITLE, "", _PAGE, url=f"https://x/{status}",
                                 api_key="sk-ant-k-1234567890")

    def test_reponse_sans_texte(self, posted):
        _, response = posted
        response[0] = _Response(200, {"content": [{"type": "text", "text": "bonjour"}]})
        with pytest.raises(ai_summary.AiSummaryError):
            ai_summary.summarize(_TITLE, "", _PAGE, url="https://x/t", api_key="sk-ant-k-1234567890")

    def test_reseau_coupe_sans_cle_dans_le_message(self, monkeypatch):
        import requests

        def boom(*a, **k):
            raise requests.exceptions.ConnectionError("x-api-key: sk-ant-secret-0000")

        monkeypatch.setattr(requests, "post", boom)
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
        response[0] = _Response(401, {})
        ai_summary.save_api_key("sk-ant-k-1234567890")
        monkeypatch.setattr("news_story.image_fetcher.fetch_article_html", lambda url: _PAGE)
        monkeypatch.setattr("news_story.image_fetcher.candidates_for_article", lambda *a: [])
        article = SimpleNamespace(title=_TITLE, url="https://x/top2", feed_image_url="",
                                  summary="Un chapô assez long pour servir de texte sous le titre.",
                                  source_label="Numerama")
        item = daily_top.prepare_item(article)
        assert "refusée" in item.ai_error
        assert item.subtitle                              # texte tire de l'article
