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
        # « Cependant » s'appuie sur la phrase d'avant : elle est gardee, juste avant.
        assert "L’ensemble est aussi compatible Dolby Atmos pour projeter le son au-dessus du " \
               "spectateur. Cependant, aucun des 4 haut-parleurs" in answer

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


_TEASER_PAGE = """<article>
<p>À la Comic Con, Marvel a confirmé l’annulation de la série... mais a fait une annonce surprise qui devrait ravir les fans !</p>
<p>New York accueille la Comic Con jusqu’à dimanche, avec de nombreux invités prestigieux venus du monde entier.</p>
<h2>Mauvaise nouvelle pour les fans</h2>
<p>La saison 3 de Born Again, bientôt diffusée, sera la dernière de la série phare de Marvel.</p>
<p>Pour autant, Marvel Studios avait aussi une autre annonce, beaucoup plus positive, à faire aux fans réunis.</p>
<h2>Une annonce qui met du baume au cœur</h2>
<p>La grande surprise de cette soirée centrée sur la série ? Charlie Cox n’est pas apparu seul sur scène ! Il était accompagné des comédiens Krysten Ritter et Finn Jones, de retour avec les Defenders.</p>
</article>"""


class TestAccroche:
    def test_l_accroche_est_reconnue(self):
        from news_story.article_text import is_teaser

        assert is_teaser("Daredevil : une très bonne surprise attend les fans !")
        assert is_teaser("Un film sort", "Cet acteur américain ne tarit pas d'éloges...")
        assert not is_teaser("Le film Dune 3 sortira le 18 décembre 2026")

    def test_la_surprise_est_revelee(self):
        answer = answer_from_article(
            "Daredevil : la saison 3 de Born Again sera bien la dernière, mais une très bonne surprise attend les fans !",
            _TEASER_PAGE,
            chapo="À la Comic Con, Marvel a confirmé l’annulation de la série... mais a fait une annonce "
                  "surprise qui devrait ravir les fans !")
        assert "Krysten Ritter" in answer
        assert "beaucoup plus positive" not in answer     # encore une accroche, pas l'info


class TestCritique:
    _PAGE = """<article>
<p>Notre critique de Below, la nouvelle mini-série horrifique de Netflix avec Josh Hartnett.</p>
<h2>Balance ton port</h2>
<p>Les premières séquences de Below sont pleines de promesses et le prologue intrigue beaucoup.</p>
<h2>La barre est below</h2>
<p>À défaut de réellement convaincre, Below conserve quelque chose d’assez charmant et irrésistible. On en ressort insatisfait mais tout de même avec le sourire aux lèvres.</p>
<p><em>Below est disponible en intégralité sur Netflix depuis ce 8 octobre 2026</em></p>
<div class="article--article-comments"><div class="wpd-comment-text"><p>Comme d’hab avec Netflix, c’est dilué à mort pour faire du temps de visionnage.</p></div></div>
</article>"""

    def test_le_verdict_est_la_conclusion_de_l_article(self):
        from news_story.article_text import is_review

        assert is_review("Below : critique d’une mise en abysse sur Netflix")
        answer = answer_from_article("Below : critique d’une mise en abysse sur Netflix", self._PAGE,
                                     chapo="Notre critique de Below, la nouvelle mini-série horrifique "
                                           "de Netflix avec Josh Hartnett.")
        assert answer.endswith("On en ressort insatisfait mais tout de même avec le sourire aux lèvres.")
        assert "disponible" not in answer          # mention de diffusion ecartee
        assert "dilué à mort" not in answer         # commentaire de lecteur ecarte


class TestEncodageEtBruit:
    def test_page_sans_charset_http_lue_en_utf8(self):
        from news_story.image_fetcher import decode_html

        raw = '<html><head><meta charset="UTF-8" /></head><p>Après, déjà, où</p>'.encode("utf-8")
        assert "Après, déjà, où" in decode_html(raw, "text/html")       # pas de « AprÃ¨s »
        assert "Après" in decode_html(raw, "")
        latin = "<p>Après</p>".encode("latin-1")
        assert "Après" in decode_html(latin, "text/html; charset=ISO-8859-1")

    def test_liens_d_articles_lies_et_encarts_ecartes(self):
        page = """<article>
<ul><li>Le costume a été réingéniéré pour être plus fonctionnel.</li></ul>
<ul><li>Le costume a été réingéniéré pour être plus fonctionnel.</li></ul>
<p>Ce changement de look répond directement aux enjeux scénaristiques de cette suite.</p>
<div class="premium-promo-alert"><p>Tout le monde n'a pas les moyens de payer pour l'information.</p></div>
<div class="card-install-pwa"><p>Ajoutez Numerama à votre écran d'accueil et restez connectés !</p></div>
<div class="embedded-tag-container"><ul><li class="link"><a href="/x">Avengers Doomsday : on sait enfin où est Nick Fury</a></li></ul></div>
<ul><li><a href="/y">Spider-Man va avoir une nouvelle série live-action</a></li></ul>
</article>"""
        texts = [text for _, text in extract_paragraphs(page)]
        assert texts == ["Le costume a été réingéniéré pour être plus fonctionnel.",
                         "Ce changement de look répond directement aux enjeux scénaristiques de cette suite."]

    def test_description_coupee_au_milieu_d_une_phrase(self):
        from news_story.article_text import page_chapo

        page = ('<meta property="og:description" content="Paramount va produire un film '
                'Cyberpunk 2077. Confirmée simultanément">')
        assert page_chapo(page) == "Paramount va produire un film Cyberpunk 2077."
        assert page_chapo('<meta name="description" content="Un début sans fin">') == ""
