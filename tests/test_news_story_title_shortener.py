"""Raccourcissement du titre d'une Story : factuel (jamais invente), coupe
uniquement sur une frontiere de mot, et detection de rumeur conservatrice
(seulement si le texte source le dit deja lui-meme)."""
from news_story.title_shortener import build_display_title, is_rumor


class TestIsRumor:
    def test_the_word_rumeur_marks_the_article_as_a_rumor(self):
        assert is_rumor("Rumeur : un nouveau jeu serait en preparation") is True

    def test_leak_marks_the_article_as_a_rumor(self):
        assert is_rumor("Fuite : les premieres images du jeu circulent") is True

    def test_non_confirme_marks_the_article_as_a_rumor(self):
        assert is_rumor("Des informations non confirmees circulent sur le studio") is True

    def test_an_ordinary_factual_title_is_not_a_rumor(self):
        assert is_rumor("Le studio annonce la sortie du jeu pour mars") is False

    def test_the_conditional_tense_alone_is_not_treated_as_a_rumor(self):
        # Le conditionnel sert aussi a des titres parfaitement factuels (une
        # annonce officielle peut employer "pourrait" ou "serait") -- ce
        # n'est pas un signal fiable de rumeur, contrairement a un mot
        # explicite comme "rumeur"/"fuite"/"non confirme".
        assert is_rumor("Le prix du jeu pourrait augmenter des le mois prochain") is False

    def test_detection_is_accent_and_case_insensitive(self):
        assert is_rumor("RUMEUR : ANNONCE IMMINENTE") is True
        assert is_rumor("rûmeûrs autour du prochain opus") is True

    def test_a_rumor_marker_in_the_summary_alone_is_enough(self):
        assert is_rumor("Le studio prepare quelque chose", summary="Rumeur non confirmee") is True


class TestBuildDisplayTitle:
    def test_a_short_title_is_returned_unchanged(self):
        result = build_display_title("Un titre court", max_chars=90)
        assert result.text == "Un titre court"
        assert result.truncated is False
        assert result.is_rumor is False

    def test_a_long_title_is_truncated_on_a_word_boundary(self):
        title = "Un titre extremement long qui depasse largement la limite de caracteres autorisee ici"
        result = build_display_title(title, max_chars=40)
        assert len(result.text) <= 41  # 40 + l'ellipse
        assert result.truncated is True
        assert result.text.endswith("…")
        # Jamais un mot coupe au milieu.
        body = result.text.rstrip("…").strip()
        assert title.startswith(body)

    def test_multiple_spaces_are_normalized(self):
        result = build_display_title("Un   titre   avec   des   espaces", max_chars=90)
        assert result.text == "Un titre avec des espaces"

    def test_a_rumor_title_is_prefixed_and_flagged(self):
        result = build_display_title("Rumeur : le jeu serait annule", max_chars=90)
        assert result.text.startswith("RUMEUR : ")
        assert result.is_rumor is True

    def test_the_rumor_prefix_counts_against_the_character_budget(self):
        result = build_display_title("Rumeur : " + "x" * 200, max_chars=40)
        assert len(result.text) <= 41

    def test_never_invents_words_beyond_the_source_title(self):
        title = "Sortie confirmee pour le mois de mars"
        result = build_display_title(title, max_chars=200)
        assert result.text == title  # rien ajoute, rien reformule

    def test_a_non_rumor_title_is_never_prefixed(self):
        result = build_display_title("Le jeu sort officiellement demain", max_chars=90)
        assert not result.text.startswith("RUMEUR")

    def test_a_title_with_a_nearby_sentence_boundary_is_cut_there_instead_of_mid_clause(self):
        # La premiere phrase (58 caracteres) tient dans le budget de 70 avec
        # de la marge -- preferee a une coupure de mot qui laisserait la
        # deuxieme phrase commencee et coupee en plein milieu.
        title = ("Le studio confirme la date de sortie du jeu. Le prix "
                 "n'a en revanche toujours pas ete communique par l'editeur")
        result = build_display_title(title, max_chars=70)
        assert result.text == "Le studio confirme la date de sortie du jeu."
        assert not result.text.endswith("…")  # une phrase complete n'a pas besoin d'ellipse
        assert result.truncated is True

    def test_a_too_short_first_sentence_falls_back_to_word_boundary(self):
        # La premiere phrase (9 caracteres) recupererait trop peu du budget
        # (ratio sous _SENTENCE_BOUNDARY_MIN_RATIO) -- mieux vaut couper sur
        # un mot plus loin que produire un titre inutilement court.
        title = "Confirme. Un nouveau jeu de course arrive sur toutes les plateformes cette annee"
        result = build_display_title(title, max_chars=40)
        assert result.text != "Confirme."
        assert result.text.endswith("…")

    def test_a_sentence_boundary_result_stays_a_verbatim_prefix(self):
        title = ("Le studio confirme officiellement la date de sortie du jeu tres attendu. "
                 "Deuxieme phrase qui ne doit jamais apparaitre ici")
        result = build_display_title(title, max_chars=90)
        body = result.text.rstrip("…").strip()
        assert title.startswith(body)
