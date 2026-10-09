"""Corps d'un article de presse, et la reponse a la question de son titre.

Demande explicite de l'utilisateur : un titre « hook » (« que vaut vraiment
le Dolby Atmos sans barre de son ? ») ne donne pas l'info ; le post doit
porter la REPONSE, avec l'explication qui va avec, et la phrase doit avoir du
sens -- raccourcir l'article si besoin.

Comme tout news_story/ : AUCUNE IA generative, rien d'invente ni de
reformule. On EXTRAIT des phrases entieres de l'article (une phrase entiere a
toujours du sens), choisies parce qu'elles repondent au titre :

- elles reprennent les mots importants du titre ;
- elles sont dans un passage de conclusion (« Verdict », « Au final »,
  « En somme »...) -- la ou un article repond a sa propre question ;
- a defaut, les premieres phrases du corps, apres le chapo.

Les phrases gardent leur ordre d'origine et sont coupees a un budget de
caracteres, jamais au milieu d'une phrase.
"""
from __future__ import annotations

import re
import unicodedata
from html.parser import HTMLParser

_BLOCK_TAGS = {"p", "h2", "h3", "h4", "li", "blockquote"}
_SKIP_TAGS = {"script", "style", "nav", "header", "footer", "aside", "form", "noscript",
              "figure", "figcaption", "button", "svg"}
# Paragraphes de page qui ne sont pas l'article.
_NOISE_RE = re.compile(
    r"(abonne|newsletter|cookies?|publicit|partager|lire aussi|a lire|à lire|"
    r"suivez[- ]nous|tous droits|commentaires?|inscri|connexion|mot de passe|"
    r"javascript|réagir|reagir|signaler)", re.IGNORECASE)
_CONCLUSION_RE = re.compile(
    r"\b(verdict|au final|finalement|en conclusion|en somme|en résumé|en resume|bref|"
    r"au bout du compte|pour résumer|pour resumer|conclusion|en définitive|en definitive|"
    r"résultat|resultat|reste que|il faut donc|on retiendra)\b", re.IGNORECASE)
# Mots qui introduisent une reponse ou une nuance (« Cependant, aucun des
# haut-parleurs n'est dedie... ») : une phrase qui commence ainsi tranche.
_ANSWER_MARKERS_RE = re.compile(
    r"^(cependant|pourtant|toutefois|en revanche|malgre|malgré|or|mais|donc|ainsi|"
    r"resultat|résultat|concretement|concrètement|autrement dit|en clair)\b", re.IGNORECASE)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?…])\s+(?=[«\"A-ZÀ-ÖØ-Þ0-9])")
_WORD_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = set("""
le la les un une des du de d l et ou a au aux en dans sur sous pour par avec sans
ce cet cette ces se sa son ses leur leurs que qui quoi dont ou est sont etre ete
il elle ils elles on nous vous je tu y ne pas plus moins tres tout tous toute
vraiment comment pourquoi quand quel quelle quels quelles combien faut peut va
vaut fait faire avoir aussi encore deja bien mais donc car comme si
""".split())

MIN_PARAGRAPH_CHARS = 60


class _BodyParser(HTMLParser):
    """Paragraphes de l'article : texte des <p>/<h2>/<li>... hors navigation,
    en-tetes, pieds de page et scripts ; dans <article> quand la page en a un."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks: list[tuple[str, str, bool]] = []   # (balise, texte, dans <article>)
        self._skip = 0
        self._article = 0
        self._current: list[str] | None = None
        self._tag = ""

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif tag == "article":
            self._article += 1
        elif tag in _BLOCK_TAGS and not self._skip:
            self._current, self._tag = [], tag
        elif tag == "br" and self._current is not None:
            self._current.append(" ")

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS and self._skip:
            self._skip -= 1
        elif tag == "article" and self._article:
            self._article -= 1
        elif tag == self._tag and self._current is not None:
            text = " ".join("".join(self._current).split())
            if text:
                self.blocks.append((self._tag, text, self._article > 0))
            self._current = None

    def handle_data(self, data):
        if self._current is not None and not self._skip:
            self._current.append(data)


def extract_paragraphs(page_html: str) -> list[tuple[str, str]]:
    """[(balise, texte)] du corps de l'article, dans l'ordre de la page."""
    parser = _BodyParser()
    try:
        parser.feed(page_html or "")
    except Exception:  # noqa: BLE001 -- une page mal formee donne ce qu'on a pu lire
        pass
    blocks = parser.blocks
    if any(in_article for *_, in_article in blocks):
        blocks = [b for b in blocks if b[2]]
    out = []
    for tag, text, _ in blocks:
        if tag == "p" and (len(text) < MIN_PARAGRAPH_CHARS or _NOISE_RE.search(text[:80])):
            continue
        out.append((tag, text))
    return out


def _fold(text: str) -> str:
    text = text.replace("\u2019", "'").replace("\u00a0", " ")
    text = unicodedata.normalize("NFKD", text.lower())
    return " ".join("".join(c for c in text if not unicodedata.combining(c)).split())


def _stem(word: str) -> str:
    # Singulier/pluriel seulement (« barres » = « barre ») : rien de plus fin,
    # une racinisation agressive confondrait des mots differents.
    return word[:-1] if len(word) > 4 and word.endswith(("s", "x")) else word


def keywords(text: str) -> set[str]:
    return {_stem(w) for w in _WORD_RE.findall(_fold(text)) if len(w) > 2 and w not in _STOPWORDS}


def _quotes_open(text: str) -> bool:
    """Une citation est-elle ouverte et pas refermee dans `text` ?"""
    return text.count("«") > text.count("»") or text.count('"') % 2 == 1 or \
        text.count("\u201c") > text.count("\u201d")


def split_sentences(paragraph: str) -> list[str]:
    """Phrases du paragraphe. Jamais de coupe a l'interieur d'une citation :
    « "Je pense que... ", repond-il. "Nous avons..." » garde la citation
    entiere, sinon la phrase extraite n'aurait pas de sens."""
    parts = [s.strip() for s in _SENTENCE_SPLIT_RE.split(paragraph) if s.strip()]
    merged: list[str] = []
    for part in parts:
        if merged and _quotes_open(merged[-1]):
            merged[-1] = f"{merged[-1]} {part}"
        else:
            merged.append(part)
    return merged


def is_question(title: str) -> bool:
    return "?" in (title or "")


def _question_part(title: str) -> str:
    """La question elle-meme : apres le dernier « : » d'un titre « Sujet :
    question ? » (« que vaut vraiment le Dolby Atmos sans barre de son ? »)."""
    if ":" in title and is_question(title):
        return title.rsplit(":", 1)[1]
    return title


def answer_from_article(title: str, page_html: str, *, chapo: str = "",
                        max_chars: int = 320) -> str:
    """Phrases de l'article qui repondent au titre, dans l'ordre, sans
    depasser `max_chars` (au moins une phrase entiere). "" si la page ne
    donne rien d'exploitable."""
    paragraphs = extract_paragraphs(page_html)
    if not paragraphs:
        return ""
    title_words = keywords(title)
    question_words = keywords(_question_part(title)) or title_words
    subject_words = title_words - question_words
    chapo_fold = _fold(" ".join((chapo or "").split()))

    # Phrases de l'article, avec leur contexte (section de reponse, conclusion).
    sentences: list[tuple[int, str, bool, bool]] = []
    in_conclusion = in_answer_section = False
    for tag, text in paragraphs:
        if tag in ("h2", "h3", "h4"):
            in_conclusion = bool(_CONCLUSION_RE.search(text))
            # Intertitre qui reprend la question du titre : la reponse est dessous.
            in_answer_section = len(keywords(text) & title_words) >= max(2, len(title_words) // 3)
            continue
        for sentence in split_sentences(text):
            sentences.append((len(sentences) + 1, sentence, in_conclusion, in_answer_section))
    if not sentences:
        return ""

    # Un mot present dans presque toutes les phrases (le nom du produit, du
    # film) ne distingue pas la reponse : il pese moins (idf).
    import math
    word_sets = {pos: keywords(sentence) for pos, sentence, *_ in sentences}
    total = len(sentences)

    def idf(word: str) -> float:
        df = sum(1 for ws in word_sets.values() if word in ws)
        return math.log((total + 1) / (df + 1)) + 0.3

    candidates: list[tuple[int, float, str]] = []
    for position, sentence, conclusion, answer_section in sentences:
        if len(sentence) < 25 or _fold(sentence) in chapo_fold:
            continue          # trop court, ou deja dit par le chapo
        words = word_sets[position]
        score = sum(2.0 * idf(w) for w in words & question_words)
        score += sum(0.5 * idf(w) for w in words & subject_words)
        if conclusion or _CONCLUSION_RE.search(sentence[:60]):
            score += 3.0
        if answer_section:
            score += 2.0
        if _ANSWER_MARKERS_RE.search(sentence):
            score += 2.0
        if is_question(title) and sentence.endswith("?"):
            score -= 5.0      # une autre question n'est pas une reponse
        candidates.append((position, score, sentence))
    if not candidates:
        return ""

    best = max(candidates, key=lambda c: (c[1], -c[0]))
    if best[1] <= 0:
        # Rien ne repond clairement : le debut du corps (souvent la reponse).
        chosen = sorted(candidates)[:3]
    else:
        # Une phrase qui commence par « Cependant », « Donc »... s'appuie sur
        # la precedente : on la garde pour que l'ensemble ait du sens. Puis la
        # suivante, qui explique.
        chosen = [best]
        if _ANSWER_MARKERS_RE.search(best[2]):
            chosen += [c for c in candidates if c[0] == best[0] - 1]
        chosen += [c for c in candidates if c[0] == best[0] + 1]
    text = ""
    for _, _, sentence in sorted(chosen):
        candidate = f"{text} {sentence}".strip()
        if text and len(candidate) > max_chars:
            break
        text = candidate
    return text
