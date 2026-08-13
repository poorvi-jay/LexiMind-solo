"""
backend/services/nlp_service.py
F26 phonetic spell correction · F27 grammar checking · F28 homophone detection

All three run over one spaCy parse of the text, so a /nlp/check request costs a
single tokenisation pass plus (when Java is available) one LanguageTool call.

Loading is lazy and cached: spaCy's model and LanguageTool's JVM are expensive
(the JVM warmup is 10-15s), so warm_up() is called once from the app lifespan
in a background thread and every request reuses the same objects.
"""

import logging
import os
import threading
from pathlib import Path

import jellyfish
import wordfreq

# spaCy is imported lazily inside _get_spacy(). It is a heavy optional
# dependency — its compiled extensions can be refused by the OS (Windows Smart
# App Control blocks unsigned .pyd files), and importing it at module scope made
# that failure take down the entire API, auth and reading included, rather than
# just the writing checks.

logger = logging.getLogger(__name__)

MAX_CHECK_CHARS = 10_000  # a full 50k notepad every 800ms would be far too slow

# ── spelling thresholds ────────────────────────────────────────────────
# A word counts as correctly spelled if it is common enough on its own, or is a
# real dictionary word that at least shows up. Measured on this corpus: correct
# words sit at zipf 3.5-6.8 while classic misspellings land at 1.4-3.0, so the
# frequency test alone separates fone/nife/wuz/thier/recieve/definately from
# ordinary vocabulary. The dictionary clause is what rescues legitimately rare
# words a student would still write — photosynthesis (3.03), mitochondria
# (3.09), dyslexia (3.02) — without letting misspellings back in.
MIN_COMMON_ZIPF = 3.5
MIN_DICTIONARY_ZIPF = 2.0

# Words shorter than this are skipped: at 1-2 letters the phonetic codes collide
# with everything and the suggestions are noise.
MIN_WORD_LENGTH = 3

# How many candidate corrections to offer per misspelling.
MAX_SUGGESTIONS = 3

# Size of the candidate pool the phonetic index is built from.
SUGGESTION_VOCAB_SIZE = 60_000

# Shorthand that is technically a word but almost never the intended one.
#
# Frequency can't catch these and no threshold will: they are *common* precisely
# because people write them ("cuz" sits at zipf 3.89, above plenty of ordinary
# vocabulary), and several are real dictionary headwords, so both halves of the
# validity test wave them through. "cud" is the one the acceptance criteria name
# — a cow chews the cud, but a student writing "I cud not find my bag" meant
# "could".
#
# Kept deliberately short, and only for forms whose legitimate use is rare in
# prose. "cos" (cosine) and "thru" (drive-thru) are left out for exactly that
# reason. The result is advice, not a correction: the writer can ignore it.
INTENDED_WORDS = {
    "cud": "could",
    "wud": "would",
    "cuz": "because",
    "gud": "good",
    "wat": "what",
    "wen": "when",
    "dat": "that",
    "dis": "this",
    "tho": "though",
}

# ── homophones (F28) ───────────────────────────────────────────────────
# Rule-based and deliberately conservative: a word is only flagged when its
# grammatical context actually looks wrong, never merely for being confusable.
# Flagging every "there" would bury the real mistakes.
POSSESSIVES = {
    "their": ["they're", "there"],
    "your": ["you're"],
    "its": ["it's"],
}
CONTRACTIONS = {
    "they're": (["their"], '"They\'re" means "they are". For possession, use "their".'),
    "you're": (["your"], '"You\'re" means "you are". For possession, use "your".'),
    "it's": (["its"], '"It\'s" means "it is". For possession, use "its".'),
}
COMPARATIVE_CUES = {"more", "less", "fewer", "rather", "other", "else"}
# Past/present tense and modals — a sentence with none of these has no finite
# verb, which is what distinguishes "Their going home" from "Their running shoes".
FINITE_VERB_TAGS = {"VBD", "VBP", "VBZ", "MD"}
# "to" before one of these is nearly always "too"
TOO_ADJECTIVES = {
    "much", "many", "late", "early", "big", "small", "hard", "easy", "long",
    "short", "far", "fast", "slow", "loud", "quiet", "high", "low", "old",
    "young", "heavy", "light", "expensive", "cheap", "tired", "busy",
}
DETERMINERS = {"the", "a", "an", "this", "that", "these", "those", "any", "no"}
# Words that complete "you're …" / "they're …" / "it's …" but are almost never
# something a person owns. The parse cannot make this call: spaCy reads the word
# after a possessive as a noun, so "Your late." and "Your turn." come back with
# identical tags (poss → NOUN → ROOT). Only the word itself separates them.
#
# Used together with the finite-verb test, never alone — "Your late father would
# be proud" has a verb of its own and stays clean, while "Your late again today"
# has none. Words with a strong possessive sense of their own (right, best,
# first, kind, turn) are deliberately absent.
PREDICATE_ADJECTIVES = {
    "late", "welcome", "ready", "sure", "early", "correct", "wrong", "safe",
    "done", "finished", "invited", "allowed", "awesome", "amazing", "great",
    "brilliant", "wonderful", "terrible", "silly", "crazy", "funny", "lucky",
    "sorry", "hilarious", "annoying", "beautiful", "gorgeous", "smart",
}
# Adjectives that act as nouns after a possessive — "do your best", "your own
# room", "at their worst". These are the exception to "a possessive never heads
# an adjective", so that rule has to stand down for them.
NOMINALISED_ADJECTIVES = {"best", "worst", "own", "all", "utmost", "most", "least"}


# ── lazily built resources ─────────────────────────────────────────────
_lock = threading.Lock()
_spacy_nlp = None
_spacy_error = None
_phonetic_index = None
_dictionary = None
_language_tool = None
_language_tool_error = None


def _get_dictionary() -> set:
    """Real-word dictionary from NLTK, used to rescue rare-but-correct words."""
    global _dictionary
    if _dictionary is None:
        import nltk
        from nltk.corpus import words as nltk_words

        try:
            entries = nltk_words.words()
        except LookupError:
            nltk.download("words", quiet=True)
            entries = nltk_words.words()
        _dictionary = {w.lower() for w in entries}
    return _dictionary


def _is_valid_word(word: str) -> bool:
    zipf = wordfreq.zipf_frequency(word, "en")
    if zipf >= MIN_COMMON_ZIPF:
        return True
    return zipf >= MIN_DICTIONARY_ZIPF and word in _get_dictionary()


def is_real_word(word: str) -> bool:
    """
    Public form of the spelling test, shared with prediction_service.

    Word prediction must never offer a misspelling as a completion, and
    wordfreq's frequency list contains plenty of them ("recieve" is common
    enough on the web to rank). Reusing this keeps one definition of what
    counts as a real word instead of two that can drift apart.

    INTENDED_WORDS is excluded here too, so prediction can't offer a form the
    spell checker would immediately underline — "cuz" outranks plenty of
    ordinary vocabulary and would otherwise surface as a pill.
    """
    return word not in INTENDED_WORDS and _is_valid_word(word)


def _get_phonetic_index() -> dict:
    """
    metaphone/soundex code -> candidate words, best first.

    Both codes share one index; a metaphone code and a soundex code never
    collide in practice because metaphone codes are letters-only while soundex
    codes always carry digits.
    """
    global _phonetic_index
    if _phonetic_index is None:
        index: dict[str, list[str]] = {}
        # top_n_list is frequency-ordered, so appending keeps each bucket sorted
        # by how common the candidate is — no extra sort needed.
        for word in wordfreq.top_n_list("en", SUGGESTION_VOCAB_SIZE):
            if len(word) < MIN_WORD_LENGTH or not word.isalpha():
                continue
            if not _is_valid_word(word):
                continue  # never suggest a misspelling as a correction
            for code in (jellyfish.metaphone(word), jellyfish.soundex(word)):
                if code:
                    index.setdefault(code, []).append(word)
        _phonetic_index = index
    return _phonetic_index


def _skip_edit_tree_lemmatizer() -> None:
    """
    Let spaCy import when its edit_trees extension can't be loaded.

    `spacy/pipeline/__init__.py` imports EditTreeLemmatizer unconditionally, and
    that pulls in a compiled `edit_trees` extension. If the OS refuses to load
    that one file — Windows Smart App Control blocks unsigned .pyd files, and
    picked this one — the whole of spaCy becomes unimportable, even though every
    other extension in the package loads fine.

    en_core_web_sm uses the rule-based `lemmatizer`, never the trainable
    EditTreeLemmatizer, so registering a placeholder costs us nothing that this
    project uses: the pipeline still loads tok2vec, tagger, parser,
    attribute_ruler and lemmatizer, which is everything the checks read.

    This does not work around the block — the blocked file is simply never
    loaded. Anything that genuinely needs EditTrees raises instead.
    """
    import sys
    import types

    name = "spacy.pipeline._edit_tree_internals.edit_trees"
    if name in sys.modules:
        return

    # The failed import leaves half of spaCy cached in sys.modules. Those stale
    # submodules survive the retry but were bound to the discarded `spacy`
    # module object, so the fresh one never regains its attributes — which broke
    # lemminflect, whose import does `spacy.tokens.Token.set_extension(...)`.
    # Clearing them makes the retry a genuinely fresh import.
    for module in [m for m in sys.modules if m == "spacy" or m.startswith("spacy.")]:
        del sys.modules[module]

    placeholder = types.ModuleType(name)

    class EditTrees:  # pragma: no cover — only reachable if something uses it
        def __init__(self, *args, **kwargs):
            raise RuntimeError(
                "spaCy's edit_trees extension could not be loaded on this machine, "
                "so the trainable lemmatizer is unavailable."
            )

    placeholder.EditTrees = EditTrees
    sys.modules[name] = placeholder


def _get_spacy():
    """The parsed-language model, or None when spaCy can't be loaded."""
    global _spacy_nlp, _spacy_error
    if _spacy_nlp is None and _spacy_error is None:
        try:
            try:
                import spacy
            except ImportError as exc:
                # Only retry for the one component we know we can do without;
                # any other import failure is real and should surface.
                if "edit_trees" not in str(exc):
                    raise
                logger.warning(
                    "spaCy's edit_trees extension is blocked (%s); loading without "
                    "the trainable lemmatizer, which this project does not use.",
                    exc,
                )
                _skip_edit_tree_lemmatizer()
                import spacy

            # The parser is what gives us heads and dependencies for the
            # homophone rules; NER is dead weight here.
            _spacy_nlp = spacy.load("en_core_web_sm", exclude=["ner"])
        except Exception as exc:  # noqa: BLE001 — missing model, blocked DLL
            _spacy_error = str(exc)
            logger.warning("Writing checks unavailable: %s", exc)
    return _spacy_nlp


def checks_status() -> tuple[bool, str | None]:
    """(available, reason-if-not) for the spaCy-backed checks."""
    if _spacy_nlp is not None:
        return True, None
    if _spacy_error is not None:
        return False, _spacy_error
    return False, "Writing checks are still starting up."


def _ensure_java_on_path() -> None:
    """
    Make a JAVA_HOME install visible to language_tool_python.

    It finds Java with shutil.which, which only looks at this process's PATH,
    and some JDK installs set JAVA_HOME without putting its bin on PATH.

    This does not rescue a stale environment: a process that started before
    Java was installed inherits neither variable, so a freshly installed JRE
    stays invisible to it until it is restarted — set JAVA_HOME in backend/.env
    to bridge that, since dotenv loads before this runs.
    """
    import shutil

    if shutil.which("java"):
        return

    java_home = os.environ.get("JAVA_HOME", "").strip().strip('"')
    if not java_home:
        return

    bin_dir = Path(java_home) / "bin"
    if (bin_dir / "java.exe").exists() or (bin_dir / "java").exists():
        os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"
        logger.info("Added JAVA_HOME's bin to PATH for grammar checking: %s", bin_dir)


def _get_language_tool():
    """
    LanguageTool, or None when it can't run.

    It needs a local Java install. Without one, grammar checking is reported as
    unavailable and spelling/homophones still work, rather than failing the
    whole request.
    """
    global _language_tool, _language_tool_error
    if _language_tool is None and _language_tool_error is None:
        try:
            import language_tool_python

            _ensure_java_on_path()
            _language_tool = language_tool_python.LanguageTool("en-US")
        except Exception as exc:  # no Java, download failure, port in use…
            _language_tool_error = str(exc)
            logger.warning("Grammar checking disabled: %s", exc)
    return _language_tool


def grammar_status() -> tuple[bool, str | None]:
    """(available, reason-if-not) without forcing a load attempt."""
    if _language_tool is not None:
        return True, None
    if _language_tool_error is not None:
        return False, _language_tool_error
    return False, "Grammar checking is still starting up."


def _readiness(resource, error) -> str:
    """
    'ready' | 'loading' | 'unavailable', from a resource and its recorded error.

    The distinction the status tuples above can't express is the one /health
    needs: "not yet" and "never" are both `available: False`, but a client
    should wait on the first and stop asking on the second. Every loader here
    records its failure instead of raising, so a set error means the attempt is
    over — nothing retries within a process.
    """
    if resource is not None:
        return "ready"
    return "unavailable" if error is not None else "loading"


def checks_readiness() -> str:
    """Whether the spaCy-backed checks (F26-F28) can run. Never blocks."""
    return _readiness(_spacy_nlp, _spacy_error)


def grammar_readiness() -> str:
    """Whether LanguageTool (F27) can run. Never blocks."""
    return _readiness(_language_tool, _language_tool_error)


def warm_up() -> None:
    """
    Build every cached resource. Called once at startup, off the event loop.

    Each loader records its own failure rather than raising, so one unavailable
    dependency doesn't stop the others from warming.
    """
    with _lock:
        _get_dictionary()
        _get_phonetic_index()
        _get_spacy()
        _get_language_tool()

    # Both loaders are silent on success, so without this the only way to tell
    # LanguageTool finished is to make a request and read grammar_available.
    # Outside the lock: the readiness helpers don't take it.
    # Plain ASCII: this lands on a cp1252 console on Windows, where an em dash
    # comes out as a replacement character.
    logger.info(
        "Writing checks warmed up - checks: %s, grammar: %s",
        checks_readiness(),
        grammar_readiness(),
    )


# ── F26 · phonetic spell correction ────────────────────────────────────
def _suggest_corrections(word: str) -> list[str]:
    """Words that sound like `word`, closest spelling first."""
    index = _get_phonetic_index()
    candidates: list[str] = []
    seen = set()

    for code in (jellyfish.metaphone(word), jellyfish.soundex(word)):
        for candidate in index.get(code, ()):
            if candidate != word and candidate not in seen:
                seen.add(candidate)
                candidates.append(candidate)

    # Rank on spelling similarity weighted by how common the candidate is.
    # Similarity alone is not enough: it ranks "fone" -> fon, fine, fiona ahead
    # of "phone", and misses "was" for "wuz" entirely, because a misspelling
    # usually looks more like other rare strings than like the word meant.
    # Frequency alone is just as bad ("nife" -> navy). The product puts the
    # intended word in the top two for fone/nife/wuz/thier.
    candidates.sort(
        key=lambda c: jellyfish.jaro_winkler_similarity(word, c)
        * wordfreq.zipf_frequency(c, "en"),
        reverse=True,
    )
    return candidates[:MAX_SUGGESTIONS]


def _spelling_issues(doc) -> list[dict]:
    issues = []
    for token in doc:
        if not token.is_alpha or len(token.text) < MIN_WORD_LENGTH:
            continue
        # Acronyms and proper nouns aren't dictionary words. A capital mid
        # sentence is the reliable signal — POS tags are unreliable on the
        # misspellings we're here to catch.
        if token.text.isupper():
            continue
        if token.text[0].isupper() and not token.is_sent_start:
            continue

        lower = token.text.lower()
        # Checked before the validity test, which these would otherwise pass.
        if lower in INTENDED_WORDS:
            meant = INTENDED_WORDS[lower]
            issues.append({
                "type": "spelling",
                "start": token.idx,
                "end": token.idx + len(token.text),
                "text": token.text,
                "message": f'Did you mean "{meant}"?',
                "suggestions": [meant],
            })
            continue

        if _is_valid_word(lower):
            continue
        # Inflections fall through the frequency and dictionary tests together
        # — "wagged" is neither common enough nor a dictionary headword — so
        # check the lemma before calling it a misspelling.
        if token.lemma_ and _is_valid_word(token.lemma_.lower()):
            continue

        issues.append({
            "type": "spelling",
            "start": token.idx,
            "end": token.idx + len(token.text),
            "text": token.text,
            "message": f'"{token.text}" doesn\'t look like a word.',
            "suggestions": _suggest_corrections(lower),
        })
    return issues


# ── F28 · homophone detection ──────────────────────────────────────────
def _next_token(doc, token):
    nxt = token.i + 1
    return doc[nxt] if nxt < len(doc) else None


def _homophone_verdict(doc, token):
    """(suggestions, message) when the context looks wrong, else None."""
    lower = token.lower_
    nxt = _next_token(doc, token)

    # "Their going", "Your late", "Its raining" — a possessive that modifies a
    # verb or adjective instead of a noun is the contraction misspelled.
    # A possessive determiner has to modify a noun phrase. Each of these signals
    # means it doesn't, so the contraction was meant instead.
    if lower in POSSESSIVES:
        after = _next_token(doc, nxt) if nxt is not None else None
        ends_in_ing = nxt is not None and nxt.text.lower().endswith("ing")

        # "Their running shoes are red" and "Their going home" get identical
        # parses — poss, amod(VBG), noun — so no dependency separates them.
        # What does: the real possessive sits in a sentence that has a finite
        # verb of its own, while the mistake leaves the sentence without one.
        has_finite_verb = any(t.tag_ in FINITE_VERB_TAGS for t in token.sent)
        gerund_modifier = (
            ends_in_ing
            and after is not None
            and after.pos_ in {"NOUN", "PROPN"}
            and has_finite_verb
        )

        # "Your late." / "You're welcome." — a possessive in front of a
        # predicate adjective, in a sentence carrying no finite verb of its own.
        # Both halves are load-bearing: without the word list this fires on
        # "Your turn.", and without the verb test it fires on "Your late father
        # would be proud."
        predicate_fragment = (
            nxt is not None and nxt.lower_ in PREDICATE_ADJECTIVES and not has_finite_verb
        )

        # "Your the best" — nothing may sit between a possessive and its noun,
        # so a determiner straight after it means the possessive is wrong.
        determiner_after = nxt is not None and nxt.pos_ == "DET"
        # "Do your best" — the one place a possessive legitimately heads an
        # adjective, so the rule below has to let it through.
        nominalised = nxt is not None and nxt.lower_ in NOMINALISED_ADJECTIVES

        # Unambiguous: a real possessive always heads a noun, so these never
        # fire on "their running shoes" and must not defer to the guard above.
        strong = (
            determiner_after
            # "Your going" — modifying a verb or adjective outright
            or (token.head.pos_ in {"VERB", "AUX", "ADJ"} and not nominalised)
            # "Their going home and I lost my phone" — parsed as the subject
            or token.dep_ == "nsubj"
            or predicate_fragment
        )
        # Ambiguous: both the mistake and a genuine gerund modifier put an -ing
        # word after the possessive, so only these defer to gerund_modifier.
        gerund_shaped = (nxt is not None and nxt.pos_ in {"VERB", "AUX"}) or ends_in_ing

        if strong or (gerund_shaped and not gerund_modifier):
            options = POSSESSIVES[lower]
            return options, (
                f'"{token.text}" is possessive here. Did you mean '
                f'{" or ".join(chr(34) + o + chr(34) for o in options)}?'
            )

    # "It's tail" — a contraction directly in front of a bare noun wants the
    # possessive. "It's raining" is fine, so gerunds are excluded.
    if lower in CONTRACTIONS:
        if (
            nxt is not None
            and nxt.pos_ == "NOUN"
            and not nxt.text.lower().endswith("ing")
        ):
            return CONTRACTIONS[lower]

    # "more apples then you" — a comparison earlier in the sentence wants "than".
    if lower == "then":
        for earlier in doc[token.sent.start : token.i]:
            if earlier.tag_ in {"JJR", "RBR"} or earlier.lower_ in COMPARATIVE_CUES:
                return ["than"], 'Comparisons use "than", not "then".'

    if lower == "to" and nxt is not None and nxt.lower_ in TOO_ADJECTIVES:
        return ["too"], '"Too" is the one that means excessively.'

    if lower == "too" and nxt is not None and nxt.pos_ == "VERB":
        return ["to"], '"To" is the one that goes in front of a verb.'

    if lower == "affect" and token.i > 0 and doc[token.i - 1].lower_ in DETERMINERS:
        return ["effect"], 'The noun is "effect"; "affect" is the verb.'

    if lower == "effect" and token.i > 0 and doc[token.i - 1].lower_ == "to":
        return ["affect"], 'The verb is "affect"; "effect" is the noun.'

    if lower == "loose" and token.pos_ == "VERB":
        return ["lose"], '"Lose" is the verb; "loose" means not tight.'

    if lower == "lose" and token.pos_ == "ADJ":
        return ["loose"], '"Loose" means not tight; "lose" is the verb.'

    if lower == "weather" and nxt is not None and nxt.lower_ == "or":
        return ["whether"], '"Whether" introduces a choice.'

    return None


def _homophone_issues(doc) -> list[dict]:
    issues = []
    for token in doc:
        verdict = _homophone_verdict(doc, token)
        if verdict is None:
            continue
        suggestions, message = verdict
        issues.append({
            "type": "homophone",
            "start": token.idx,
            "end": token.idx + len(token.text),
            "text": token.text,
            "message": message,
            "suggestions": suggestions,
        })
    return issues


# ── tense consistency ──────────────────────────────────────────────────
# LanguageTool checks a sentence against its own rules but makes no judgement
# about narrative tense, so "She has a disabled son, so she worked daily" passes
# clean even at its picky level. This catches the common case: a story told in
# the past that slips into the present.
#
# The parse alone can't decide it — "she has ... so she worked" and "she said
# she has three children" both hang the present verb off a past one as `ccomp`.
# What separates them is what the governing verb means, so the rule leans on
# these exclusions and stays quiet whenever one applies.

# Verbs whose complement clause is legitimately present: "he discovered that
# water boils at 100 degrees".
_REPORTING_VERBS = frozenset({
    "say", "tell", "think", "know", "believe", "discover", "learn", "realize",
    "realise", "notice", "remember", "explain", "show", "prove", "find", "see",
    "hear", "understand", "decide", "admit", "argue", "claim", "mention",
    "report", "suggest", "insist", "reply", "answer", "ask", "add", "write",
    "read", "teach", "warn", "promise", "agree", "feel", "hope",
})

# Adverbs that deliberately move a clause into the present: "and now we know".
_PRESENT_TIME_MARKERS = frozenset({
    "now", "today", "currently", "nowadays", "still", "already", "recently",
    "lately", "sometimes", "always", "usually", "often",
})

# A relative clause states a property rather than narrative time, so present
# tense is correct inside a past sentence: "a town that has a river".
_RELATIVE_CLAUSE_DEPS = frozenset({"relcl", "acl"})

# Verbs of opinion describe an attitude that still holds while the events around
# them are over: "I like their shoes and I lost my phone" is not a tense slip.
# "have" and "want" are deliberately absent — those are the ones worth catching.
_OPINION_VERBS = frozenset({
    "like", "love", "hate", "prefer", "enjoy", "admire", "dislike", "adore",
})

_PRESENT_TAGS = frozenset({"VBZ", "VBP"})


def _tense_issues(doc) -> list[dict]:
    from lemminflect import getInflection

    issues = []
    for sent in doc.sents:
        past = [t for t in sent if t.tag_ == "VBD"]
        if not past:
            continue  # no past-tense anchor, so nothing to be inconsistent with

        for token in sent:
            if token.tag_ not in _PRESENT_TAGS:
                continue
            # "I finished my work and I am happy" is fine — a present state
            # alongside a past event. Copulas cause most of the false alarms.
            if token.lemma_ == "be" or token.lemma_.lower() in _OPINION_VERBS:
                continue
            if any(a.lemma_.lower() in _REPORTING_VERBS for a in token.ancestors):
                continue
            if token.dep_ in _RELATIVE_CLAUSE_DEPS or any(
                a.dep_ in _RELATIVE_CLAUSE_DEPS for a in token.ancestors
            ):
                continue
            if any(
                c.lower_ in _PRESENT_TIME_MARKERS
                for c in (*token.children, *token.head.children)
            ):
                continue

            forms = getInflection(token.lemma_, tag="VBD") or ()
            suggestions = [
                form.capitalize() if token.text[:1].isupper() else form
                for form in forms[:MAX_SUGGESTIONS]
                if form.lower() != token.lower_
            ]
            if not suggestions:
                continue

            issues.append({
                "type": "grammar",
                "start": token.idx,
                "end": token.idx + len(token.text),
                "text": token.text,
                "message": (
                    f'The rest of this sentence is in the past tense. '
                    f'Did you mean "{suggestions[0]}"?'
                ),
                "suggestions": suggestions,
            })

    return issues


# ── F27 · grammar ──────────────────────────────────────────────────────
# LanguageTool carries its own spell checker. Its findings are reported as
# spelling rather than grammar so they land in the right sidebar bucket, and
# they back up F26 rather than competing with it: the phonetic checker lowercases
# a word before looking it up, so it is blind to casing errors like "SHe", whose
# lowercase form is a perfectly good word.
_SPELLING_RULE_MARKERS = ("MORFOLOGIK", "SPELLER")


def _grammar_issues(text: str, sentence_starts: set[int]) -> tuple[list[dict], list[dict]]:
    """Return (grammar issues, spelling issues) from LanguageTool."""
    tool = _get_language_tool()
    if tool is None:
        return [], []

    grammar: list[dict] = []
    spelling: list[dict] = []

    for match in tool.check(text):
        # language_tool_python 3.x names these rule_id / error_length; the
        # camelCase spellings raise AttributeError.
        end = match.offset + match.error_length
        issue = {
            "type": "grammar",
            "start": match.offset,
            "end": end,
            "text": text[match.offset : end],
            "message": match.message,
            "suggestions": list(match.replacements)[:MAX_SUGGESTIONS],
        }

        if any(marker in match.rule_id for marker in _SPELLING_RULE_MARKERS):
            # The same proper-noun guard F26 uses: an unknown capitalised word
            # away from a sentence start is far more likely a name than a typo.
            if issue["text"][:1].isupper() and match.offset not in sentence_starts:
                continue
            issue["type"] = "spelling"
            spelling.append(issue)
        else:
            grammar.append(issue)

    return grammar, spelling


# ── public entry point ─────────────────────────────────────────────────
def check_text(text: str) -> dict:
    """Run all three checks and return issues sorted by position."""
    def empty(checks_available=True, checks_reason=None):
        grammar_available, grammar_reason = grammar_status()
        return {
            "issues": [],
            "counts": {"spelling": 0, "grammar": 0, "homophone": 0},
            "grammar_available": grammar_available,
            "grammar_unavailable_reason": None if grammar_available else grammar_reason,
            "checks_available": checks_available,
            "checks_unavailable_reason": checks_reason,
        }

    if not text.strip():
        return empty()

    nlp = _get_spacy()
    if nlp is None:
        # Every check is built on the spaCy parse, so without it there is
        # nothing to report — but the notepad itself keeps working.
        available, reason = checks_status()
        return empty(checks_available=available, checks_reason=reason)

    doc = nlp(text)

    spelling = _spelling_issues(doc)
    homophones = _homophone_issues(doc)
    grammar, tool_spelling = _grammar_issues(text, {sent[0].idx for sent in doc.sents})

    def overlaps(issue, spans):
        return any(issue["start"] < end and start < issue["end"] for start, end in spans)

    # LanguageTool goes first: where it has an opinion about the same word, its
    # rule is better evidence than this heuristic.
    tool_spans = [(g["start"], g["end"]) for g in grammar]
    grammar += [t for t in _tense_issues(doc) if not overlaps(t, tool_spans)]

    # F26 owns a word it already flagged — its phonetic suggestions are the
    # better advice — so LanguageTool only fills the gaps it leaves behind.
    claimed = [(i["start"], i["end"]) for i in spelling + homophones]
    spelling += [s for s in tool_spelling if not overlaps(s, claimed)]

    # Spelling and homophones are the more specific, more actionable reports, so
    # a grammar match covering the same span is dropped instead of duplicating.
    claimed = [(i["start"], i["end"]) for i in spelling + homophones]
    grammar = [g for g in grammar if not overlaps(g, claimed)]

    issues = sorted(spelling + homophones + grammar, key=lambda i: (i["start"], i["type"]))
    available, reason = grammar_status()

    return {
        "issues": issues,
        "counts": {
            "spelling": len(spelling),
            "grammar": len(grammar),
            "homophone": len(homophones),
        },
        "grammar_available": available,
        "grammar_unavailable_reason": None if available else reason,
        "checks_available": True,
        "checks_unavailable_reason": None,
    }
