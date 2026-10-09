"""Reponse a la question d'un titre, extraite de l'article : phrases entieres,
jamais reformulees, choisies parce qu'elles repondent au titre."""
from news_story.article_text import (
    answer_from_article, extract_paragraphs, is_question, split_sentences)

_PAGE = """<html><body><header><p>Menu du site, abonnez-vous à notre newsletter pour ne rien rater.</p></header>
<article>
<p>Disponible depuis ce jeudi, le nouveau décodeur Home Cinema d’Orange promet du Dolby Atmos sans équipement supplémentaire.</p>
<p>Orange propose un boîtier qui fait à la fois décodeur et enceinte, posé sous le téléviseur du salon.</p>
<h2>Que vaut le Dolby Atmos du Décodeur Home Cinema sans caisson ?</h2>
<p>Orange a équipé son Décodeur Home Cinema d’un woofer et de deux transducteurs latéraux pour élargir la scène. L’ensemble est aussi compatible Dolby Atmos pour projeter le son au-dessus du spectateur. Cependant, aucun des 4 haut-parleurs n’est dédié aux effets verticaux, qui sont confiés à une virtualisation numérique comme sur certaines barres de son.</p>
<h2>Quel est le vrai prix du Décodeur Home Cinema d’Orange ?</h2>
<p>Il n’y a aucune étiquette sur le boîtier, puisqu’il accompagne la Livebox Max à 57,99 € par mois avec engagement.</p>
</article>
<footer><p>Tous droits réservés, reproduction interdite sans autorisation écrite.</p></footer>
</body></html>"""

_TITLE = "Décodeur Home Cinema d'Orange : que vaut vraiment le Dolby Atmos sans barre de son ?"
_CHAPO = ("Disponible depuis ce jeudi, le nouveau décodeur Home Cinema d'Orange promet du Dolby "
          "Atmos sans équipement supplémentaire.")


class TestCorps:
    def test_seul_le_corps_de_l_article_est_lu(self):
        texts = [t for _, t in extract_paragraphs(_PAGE)]
        assert not any("newsletter" in t or "droits réservés" in t for t in texts)
        assert any(t.startswith("Que vaut le Dolby Atmos") for t in texts)

    def test_jamais_de_coupe_dans_une_citation(self):
        parts = split_sentences('"Je pense que oui. Nous avons tous un peu de lui", dit-il. Fin.')
        assert parts[0] == '"Je pense que oui. Nous avons tous un peu de lui", dit-il.'


class TestReponse:
    def test_la_reponse_est_tiree_de_la_section_qui_repond(self):
        answer = answer_from_article(_TITLE, _PAGE, chapo=_CHAPO)
        assert "aucun des 4 haut-parleurs" in answer
        # « Cependant » s'appuie sur la phrase d'avant : elle est gardee.
        assert answer.startswith("L’ensemble est aussi compatible Dolby Atmos")

    def test_rien_n_est_invente(self):
        answer = answer_from_article(_TITLE, _PAGE, chapo=_CHAPO)
        body = " ".join(t for _, t in extract_paragraphs(_PAGE))
        for sentence in split_sentences(answer):
            assert sentence in body

    def test_le_chapo_n_est_pas_repete(self):
        assert "Disponible depuis ce jeudi" not in answer_from_article(_TITLE, _PAGE, chapo=_CHAPO)

    def test_budget_respecte_sans_couper_de_phrase(self):
        answer = answer_from_article(_TITLE, _PAGE, chapo=_CHAPO, max_chars=200)
        assert answer.endswith(".") and len(split_sentences(answer)) >= 1

    def test_page_vide(self):
        assert answer_from_article(_TITLE, "") == ""

    def test_question(self):
        assert is_question(_TITLE) and not is_question("Un film sort au cinéma")
