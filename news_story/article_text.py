"""Corps d'un article de presse, et la reponse a la question de son titre.

Demande explicite de l'utilisateur : un titre « hook » (« que vaut vraiment
le Dolby Atmos sans barre de son ? ») ne donne pas l'info ; le post doit
porter la REPONSE, avec l'explication qui va avec, et la phrase doit avoir du
sens -- raccourcir l'article si besoin.

Methode de secours : quand une cle API Claude est configuree, c'est Claude qui
lit l'article et redige le texte (news_story/ai_summary.py). Ici, rien
d'invente ni de reformule : on EXTRAIT des phrases entieres de l'article (une
phrase entiere a toujours du sens), choisies parce qu'elles repondent au titre :

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

_BLOCK_TAGS = {"p", "h2", "h3", "h4", "li"}
# blockquote : les tweets / posts Instagram integres (souvent en anglais,
# avec un lien) -- pas le texte de l'article.
_SKIP_TAGS = {"script", "style", "nav", "header", "footer", "aside", "form", "noscript",
              "figure", "figcaption", "button", "svg", "blockquote"}
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
    r"^(cependant|pourtant|toutefois|en revanche|malgre|malgré|or|mais|donc|ainsi|en effet|"
    r"resultat|résultat|concretement|concrètement|autrement dit|en clair)\b", re.IGNORECASE)
_URL_RE = re.compile(r"https?://|www\.|\bt\.co/|pic\.twitter", re.IGNORECASE)
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


# Blocs de page qui ne sont pas l'article : commentaires de lecteurs
# (wpDiscuz...), articles lies, partage, newsletter.
_SKIP_CLASS_RE = re.compile(
    r"comment|wpdiscuz|wpd-|related|share|social|newsletter|recommend|read-also|lire-aussi"
    r"|embedded-tag|tags|premium-promo|paywall|install-pwa",
    re.IGNORECASE)


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
        self._container_tag = ""
        self._container_depth = 0
        self._link: list[str] | None = None     # texte du lien en cours, dans le bloc
        self._link_text: list[str] = []

    def handle_starttag(self, tag, attrs):
        if self._container_tag:
            # Dans un bloc ecarte (commentaires de lecteurs...) : on suit
            # seulement l'imbrication de sa balise pour savoir quand il finit.
            if tag == self._container_tag:
                self._container_depth += 1
            return
        classes = dict(attrs).get("class") or ""
        if tag in ("div", "section", "ol", "ul", "aside") and _SKIP_CLASS_RE.search(classes):
            self._container_tag, self._container_depth = tag, 1
            return
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif tag == "article":
            self._article += 1
        elif tag in _BLOCK_TAGS and not self._skip:
            self._current, self._tag = [], tag
        elif tag == "br" and self._current is not None:
            self._current.append(" ")
        elif tag == "a" and self._current is not None:
            self._link = []

    def handle_endtag(self, tag):
        if self._container_tag:
            if tag == self._container_tag:
                self._container_depth -= 1
                if self._container_depth <= 0:
                    self._container_tag = ""
            return
        if tag in _SKIP_TAGS and self._skip:
            self._skip -= 1
        elif tag == "article" and self._article:
            self._article -= 1
        elif tag == "a" and self._link is not None:
            self._link_text.append("".join(self._link))
            self._link = None
        elif tag == self._tag and self._current is not None:
            text = " ".join("".join(self._current).split())
            links = " ".join(" ".join(self._link_text).split())
            # Un element de liste qui n'est qu'un lien = liste d'articles lies
            # ou de mots-cles, pas le texte de l'article.
            if text and not (self._tag == "li" and links == text):
                self.blocks.append((self._tag, text, self._article > 0))
            self._current, self._link, self._link_text = None, None, []

    def handle_data(self, data):
        if self._current is not None and not self._skip and not self._container_tag:
            self._current.append(data)
            if self._link is not None:
                self._link.append(data)


# Resume en points cles publie par le site lui-meme (Numerama : « Résumé de
# l'article », class="ia-abstract" ; ailleurs « L'essentiel », « À retenir »).
_SUMMARY_CLASS_RE = re.compile(r"abstract|key-?points|essentiel|a-retenir|tl-?dr", re.IGNORECASE)


class _SummaryParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.points: list[str] = []
        self._tag = ""
        self._depth = 0
        self._current: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if self._tag:
            if tag == self._tag:
                self._depth += 1
            elif tag == "li":
                self._current = []
            return
        classes = dict(attrs).get("class") or ""
        if tag in ("div", "section", "aside", "ul") and _SUMMARY_CLASS_RE.search(classes) \
                and not self.points:
            self._tag, self._depth = tag, 1
            if tag == "ul":
                self._depth = 1

    def handle_endtag(self, tag):
        if not self._tag:
            return
        if tag == "li" and self._current is not None:
            text = " ".join("".join(self._current).split())
            if text and text not in self.points:
                self.points.append(text)
            self._current = None
        elif tag == self._tag:
            self._depth -= 1
            if self._depth <= 0:
                self._tag = ""

    def handle_data(self, data):
        if self._current is not None:
            self._current.append(data)


def summary_points(page_html: str) -> list[str]:
    """Les points cles du resume que le site publie en tete d'article (le
    premier trouve), dans l'ordre. [] si la page n'en a pas."""
    parser = _SummaryParser()
    try:
        parser.feed(page_html or "")
    except Exception:  # noqa: BLE001
        pass
    return [p for p in parser.points if len(p) >= 25 and not _URL_RE.search(p)]


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
    seen: set[str] = set()
    for tag, text, _ in blocks:
        if tag == "p" and (len(text) < MIN_PARAGRAPH_CHARS or _NOISE_RE.search(text[:80])):
            continue
        if text in seen:
            continue          # bloc repete (resume affiche deux fois, version mobile...)
        seen.add(text)
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


_CLAUSE_SPLIT_RE = re.compile(r"\s*(?:[,:;]|\bmais\b|\bet\b)\s*", re.IGNORECASE)


def _question_part(title: str) -> str:
    """Ce que le titre demande ou annonce sans le dire :
    - « Sujet : question ? » -> la question (apres le dernier « : ») ;
    - « La saison 3 sera la derniere, mais une surprise attend les fans » ->
      la partie qui porte l'accroche (le reste est deja dit par le titre)."""
    if ":" in title and is_question(title):
        return title.rsplit(":", 1)[1]
    if not is_question(title):
        clauses = [c for c in _CLAUSE_SPLIT_RE.split(title) if c.strip()]
        hooked = [c for c in clauses if _TEASER_RE.search(c)]
        if hooked:
            return " ".join(hooked)
    return title


# Vocabulaire d'accroche : un titre ou un chapo qui l'emploie annonce une
# info sans la donner (« une tres bonne surprise attend les fans », « cet
# acteur americain », « voici pourquoi »). L'article la donne plus loin.
_TEASER_RE = re.compile(
    r"\b(surprises?|annonces?|révél\w*|revel\w*|découvr\w*|decouvr\w*|voici|voilà|voila|"
    r"secrets?|raisons?|pourquoi|comment|cet(?:te)? (?:acteur|actrice|film|série|serie|star|"
    r"réalisateur|realisateur|personnage)|ce (?:film|personnage|réalisateur|realisateur|détail|"
    r"detail)|celui-ci|celle-ci|on vous (?:dit|explique)|réponse|reponse|ci-dessous|"
    r"ravir|attend(?:ent)?|points? (?:positifs?|négatifs?|negatifs?|forts?|faibles?)|"
    r"bonnes? nouvelles?|mauvaises? nouvelles?|ce qu'on sait|ce qu’on sait)\b", re.IGNORECASE)
_PROPER_NOUN_RE = re.compile(r"(?<![.!?«\"“]\s)(?<!^)\b([A-ZÀ-ÖØ-Þ][\wÀ-ÖØ-öø-ÿ'’-]+)")


_NAME_QUESTION_RE = re.compile(
    r"\b(qui|quel(?:le)?s? (?:acteur|actrice|film|série|serie|star|réalisateur|realisateur|"
    r"personnage|studio|plateforme))\b", re.IGNORECASE)


_REVIEW_RE = re.compile(
    r"\b(critique|on a vu|verdict|test|avis|review|faut-il (?:voir|regarder)|vaut-il|"
    r"est-(?:il|elle) (?:un |une )?(?:bon|bonne|réussi|reussi))", re.IGNORECASE)
_AVAILABILITY_RE = re.compile(
    r"\b(est|sont|sera|seront) (?:disponibles?|visibles?)\b|\bdisponible (?:en intégralité|sur)\b|"
    r"\bau cinéma (?:le|depuis|à partir)\b|\ben salles? (?:le|depuis)\b", re.IGNORECASE)


def is_review(title: str) -> bool:
    """Une critique (« Below : critique », « On a vu... ») : l'info, c'est le
    verdict -- la conclusion de l'article."""
    return bool(_REVIEW_RE.search(title or ""))


def verdict_from_article(page_html: str, *, chapo: str = "", max_chars: int = 600) -> str:
    """Conclusion d'une critique : les dernieres phrases du corps de l'article
    (hors mention « disponible sur Netflix depuis... »), dans l'ordre, sans
    depasser `max_chars`."""
    paragraphs = [text for tag, text in extract_paragraphs(page_html) if tag == "p"]
    chapo_fold = _fold(" ".join((chapo or "").split()))
    sentences: list[str] = []
    for paragraph in paragraphs:
        for sentence in split_sentences(paragraph):
            folded = _fold(sentence)
            if len(sentence) < 25 or _URL_RE.search(sentence) or _AVAILABILITY_RE.search(sentence):
                continue
            if _quotes_open(sentence) or sentence.count("»") > sentence.count("«"):
                continue      # morceau de citation (ouverte ou fermee ailleurs) : pas de sens seul
            if folded in chapo_fold or (chapo_fold and folded[:60] in chapo_fold):
                continue
            sentences.append(sentence)
    kept: list[str] = []
    used = 0
    for sentence in reversed(sentences):
        if kept and used + 1 + len(sentence) > max_chars:
            break
        kept.insert(0, sentence)
        used += len(sentence) + 1
    return " ".join(kept)


def is_teaser(title: str, chapo: str = "") -> bool:
    """Le titre (ou le chapo) annonce-t-il une info sans la donner ?"""
    return is_question(title) or is_review(title) or bool(_TEASER_RE.search(title or "")) or \
        bool(_TEASER_RE.search(chapo or "")) or "..." in (chapo or "") or "…" in (chapo or "")


def _new_names(sentence: str, known: set[str]) -> int:
    """Noms propres de la phrase absents du titre (acteurs, films...) : une
    phrase qui en cite donne de l'info, une accroche n'en cite pas."""
    names = {_fold(m) for m in _PROPER_NOUN_RE.findall(sentence) if len(m) > 2}
    return len(names - known)


# Budget large : l'utilisateur prefere que toute l'info pertinente soit sur
# l'image, quitte a masquer la photo (le post passe alors en texte pleine
# image, voir post_composer).
DEFAULT_MAX_CHARS = 600


def answer_from_article(title: str, page_html: str, *, chapo: str = "",
                        max_chars: int = DEFAULT_MAX_CHARS) -> str:
    """Phrases de l'article qui repondent au titre (ou revelent ce que
    l'accroche annonce), dans l'ordre, sans depasser `max_chars` (au moins
    une phrase entiere). "" si la page ne donne rien d'exploitable."""
    if is_review(title):
        verdict = verdict_from_article(page_html, chapo=chapo, max_chars=max_chars)
        if verdict:
            return verdict
    # Accroche (« pour une bonne raison », « un gros point positif ») et le
    # site resume lui-meme l'article en points cles : c'est l'info, deja
    # condensee par la redaction (phrases du site, prises telles quelles).
    # Pour un titre-question, la phrase de l'article qui y repond est plus
    # directe : extraction ci-dessous.
    points = [] if is_question(title) else summary_points(page_html)
    if points:
        chapo_fold = _fold(" ".join((chapo or "").split()))
        kept, used = [], 0
        for point in points:
            if _fold(point) in chapo_fold:
                continue
            if kept and used + 1 + len(point) > max_chars:
                break
            kept.append(point if point[-1] in ".!?…»" else point + ".")
            used += len(point) + 1
        if kept:
            return " ".join(kept)
    paragraphs = extract_paragraphs(page_html)
    if not paragraphs:
        return ""
    title_words = keywords(title)
    question_words = keywords(_question_part(title)) or title_words
    subject_words = title_words - question_words
    chapo_fold = _fold(" ".join((chapo or "").split()))
    known_names = {_fold(w) for w in re.findall(r"[\wÀ-ÿ'’-]+", f"{title} {chapo}")}
    hook_words = {_fold(m.group(0)) for m in _TEASER_RE.finditer(f"{title} {chapo}")}
    # L'info cachee est un NOM (« cet acteur », « une surprise », « qui... ? ») :
    # les phrases qui citent des noms absents du titre sont celles qui la
    # donnent. Pour « que vaut... ? », la reponse n'est pas un nom : pas de bonus.
    wants_names = bool(_NAME_QUESTION_RE.search(title or "")) or (
        not is_question(title) and is_teaser(title, chapo))

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

    # Phrase qui reprend l'accroche en question (« La grande surprise de cette
    # soiree ? ») : la revelation suit immediatement.
    reveal_after = {pos for pos, sentence, *_ in sentences
                    if sentence.rstrip().endswith(("?", ":"))
                    and any(h in _fold(sentence) for h in hook_words)}

    candidates: list[tuple[int, float, str]] = []
    for position, sentence, conclusion, answer_section in sentences:
        folded = _fold(sentence)
        if len(sentence) < 25 or folded in chapo_fold or (chapo_fold and folded[:60] in chapo_fold):
            continue          # trop court, ou deja dit par le chapo (meme tronque)
        if _URL_RE.search(sentence):
            continue          # tweet / post integre, lien : pas le texte de l'article
        words = word_sets[position]
        names = min(_new_names(sentence, known_names), 4) if wants_names else 0
        teaser = bool(_TEASER_RE.search(sentence))
        score = sum(2.0 * idf(w) for w in words & question_words if _fold(w) not in hook_words)
        score += sum(0.5 * idf(w) for w in words & subject_words)
        score += 1.2 * names
        if conclusion or _CONCLUSION_RE.search(sentence[:60]):
            score += 3.0
        if answer_section:
            score += 2.0
        if _ANSWER_MARKERS_RE.search(sentence):
            score += 2.0
        if position - 1 in reveal_after or position - 2 in reveal_after:
            score += 5.0      # juste apres « La grande surprise ? » : la reponse
        if teaser and (names == 0 or not wants_names):
            score -= 3.0      # encore une accroche, sans aucun nom : pas l'info
        if sentence.rstrip().endswith("?"):
            score -= 5.0      # une question n'est pas une reponse
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
        # Puis la meilleure phrase d'ailleurs dans l'article (une deuxieme
        # info : « les Defenders seront de retour »), si elle est pertinente.
        taken = {c[0] for c in chosen}
        others = [c for c in candidates if c[0] not in taken and c[1] >= best[1] * 0.5 and c[1] > 0]
        if others:
            chosen.append(max(others, key=lambda c: (c[1], -c[0])))
    # Budget : la reponse d'abord (dans l'ordre de priorite de `chosen`), les
    # ajouts seulement s'il reste de la place ; puis l'ordre de l'article.
    kept: list[tuple[int, float, str]] = []
    used = 0
    for item in chosen:
        if item in kept:
            continue
        if kept and used + 1 + len(item[2]) > max_chars:
            continue
        kept.append(item)
        used += len(item[2]) + 1
    return " ".join(sentence for _, _, sentence in sorted(kept))


_META_DESCRIPTION_RE = re.compile(
    r'<meta[^>]+(?:property|name)="(?:og:description|description)"[^>]+content="([^"]*)"',
    re.IGNORECASE)


def page_chapo(page_html: str) -> str:
    """Le chapo tel que la page le declare (og:description / description) --
    plus propre que le resume RSS de certains sites, qui enchaine le chapo et
    le debut de l'article, intertitre compris. "" si absent."""
    import html as html_lib

    match = _META_DESCRIPTION_RE.search(page_html or "")
    if not match:
        return ""
    text = " ".join(html_lib.unescape(match.group(1)).split())
    # Page UTF-8 lue comme du Latin-1 (« AprÃ¨s ») : on repare.
    if "Ã" in text:
        try:
            text = text.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    # Description coupee par le site au milieu d'une phrase (Numerama :
    # « … un film Cyberpunk 2077. Confirmée simultanément ») : on retire le
    # morceau final. Rien de complet -> "" (l'appelant prend le resume du flux).
    if text and text[-1] not in ".!?…»\"')":
        end = max(text.rfind(mark) for mark in (". ", "! ", "? ", "… ", "» "))
        text = text[:end + 1] if end > 0 else ""
    return text

