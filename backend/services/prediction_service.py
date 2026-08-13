"""
backend/services/prediction_service.py
F29 word prediction · v5.0 phrase completion

Two different jobs, deliberately answered by two different mechanisms:

  - **Mid-word** ("I went to the hos|") the writer is stuck on spelling, which is
    the case that matters most for a dyslexic writer. This is answered by a
    frequency-ranked prefix search over the vocabulary, the way dedicated word
    prediction does it. DistilGPT-2 is bad at it — asked to continue "hos" it
    offers hosiery, hosking, hossein before hospital, because a partial word
    tokenises differently from the same word written in full.

  - **At a word boundary** ("I went to the |") the writer wants to know what
    comes next, which is a language-model question, so DistilGPT-2 answers it.

The pills and the phrase are served by **two separate entry points**, because
they cost wildly different amounts. The pills need only the next-word
distribution, which is one forward pass (~130ms); the phrase needs a real
continuation (~1.5s on this CPU) and is roughly 90% of the work. Producing both
together made every keystroke wait on the slow half, so predict() answers with
pills alone and predict_phrase() is called separately, on a longer pause.

Nothing here raises. A prediction that fails is not worth failing a keystroke
over, so every entry point degrades to empty results and the bar just stays
hidden.
"""

import logging
import re
import threading

from backend.services import nlp_service

logger = logging.getLogger(__name__)

MODEL_NAME = "distilgpt2"

# Only the tail of the document conditions the prediction; feeding more costs
# time and DistilGPT-2 only has a 1024-token window anyway.
CONTEXT_CHARS = 400

WORD_SUGGESTIONS = 3      # F29: three single-word pills
# The pills only need the next-word distribution, which is the first generation
# step — one forward pass rather than a full continuation.
WORD_STEP_TOKENS = 1
# Enough material to trim back to a finished thought. Measured across 17
# realistic contexts: 10 tokens produces the same phrases as 20 (the model
# usually closes a sentence well before the cap) at half the time, while the
# old budget of 8 rarely left enough to trim and still say anything.
PHRASE_TOKENS = 12
TOP_K = 60                # candidate next-tokens to filter down to whole words

# A phrase is worth offering only if it reads as a finished piece of writing.
PHRASE_MIN_WORDS = 3
PHRASE_MAX_WORDS = 10

# Below this a prefix matches so much of the vocabulary that the ranking is
# meaningless — "a" would just return the most common words in English.
MIN_PREFIX_LENGTH = 2

# Generation runs on one shared model; serialise so concurrent requests don't
# interleave inside torch.
_model_lock = threading.Lock()
_load_lock = threading.Lock()

_tokenizer = None
_model = None
_load_error: str | None = None
_prefix_vocab: list[str] | None = None

_TRAILING_WORD = re.compile(r"[A-Za-z']+$")
# The model happily drifts into a new paragraph or a quotation; the phrase pill
# should stop at the end of the current thought.
_PHRASE_STOP = re.compile(r'[\n\r"“”]')
_SENTENCE_END = re.compile(r"[.!?]")

# A continuation that begins with a contraction tail would be inserted after a
# space — "and he 's a very good guy" — so it is not worth offering.
_CONTRACTION_START = re.compile(r"^'(s|t|re|ll|d|ve|m)\b", re.I)

# DistilGPT-2 is trained on scraped web text, and on school-shaped prompts it
# volunteers things a writing aid for children must not put on screen — a real
# measured example was "In conclusion, the experiment showed that" completing to
# "a single dose of cannabis was associated with increased". This is not content
# moderation, just a floor: any phrase touching one of these is dropped, and the
# pill simply doesn't appear.
_UNSUITABLE = frozenset({
    "cannabis", "cocaine", "heroin", "marijuana", "weed", "drug", "drugs",
    "alcohol", "drunk", "beer", "vodka", "whisky", "cigarette", "cigarettes",
    "smoking", "vape", "sex", "sexual", "sexy", "porn", "nude", "naked",
    "rape", "raped", "murder", "murdered", "kill", "killed", "killing",
    "suicide", "gun", "guns", "shot", "shooting", "stabbed", "kidnapped",
    "abuse", "abused", "terrorist", "bomb", "damn", "hell", "shit", "fuck",
    "bitch", "bastard", "gambling", "casino", "betting",
})


def _get_prefix_vocab() -> list[str]:
    """Real words, most common first, for mid-word completion."""
    global _prefix_vocab
    if _prefix_vocab is None:
        import wordfreq

        # top_n_list is already frequency-ordered, so prefix hits come out
        # ranked with no extra sorting.
        _prefix_vocab = [
            word
            for word in wordfreq.top_n_list("en", nlp_service.SUGGESTION_VOCAB_SIZE)
            if word.isalpha() and nlp_service.is_real_word(word)
        ]
    return _prefix_vocab


def _get_model():
    """(tokenizer, model), or (None, None) when the model can't be loaded."""
    global _tokenizer, _model, _load_error

    if _model is not None or _load_error is not None:
        return _tokenizer, _model

    with _load_lock:
        if _model is not None or _load_error is not None:
            return _tokenizer, _model
        try:
            from transformers import AutoModelForCausalLM, AutoTokenizer

            tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
            model = AutoModelForCausalLM.from_pretrained(MODEL_NAME)
            model.eval()
            _tokenizer, _model = tokenizer, model
            logger.info("Loaded %s for word prediction", MODEL_NAME)
        except Exception as exc:  # noqa: BLE001 — offline, no disk space, etc.
            _load_error = str(exc)
            logger.warning("Word prediction unavailable: %s", exc)

    return _tokenizer, _model


def warm_up() -> None:
    """Build the vocabulary and load the model. Called once from the lifespan."""
    _get_prefix_vocab()
    _get_model()


def status() -> tuple[bool, str | None]:
    """(available, reason-if-not) without forcing a load."""
    if _model is not None:
        return True, None
    if _load_error is not None:
        return False, _load_error
    return False, "Word prediction is still starting up."


def readiness() -> str:
    """
    'ready' | 'loading' | 'unavailable' for DistilGPT-2. Never blocks.

    Reads the globals directly rather than taking _load_lock, so /health answers
    immediately while the ~350MB model is still downloading — which is the whole
    point of asking.

    This describes the model only. Mid-word completion runs off the prefix
    vocabulary and keeps working without it, so 'unavailable' here means the
    word-boundary pills are gone, not that /nlp/predict fails.
    """
    if _model is not None:
        return "ready"
    return "unavailable" if _load_error is not None else "loading"


def _complete_prefix(prefix: str) -> list[str]:
    """Most common real words starting with `prefix` (F29, mid-word case)."""
    lowered = prefix.lower()
    out = []
    for word in _get_prefix_vocab():
        if word.startswith(lowered) and word != lowered:
            out.append(word)
            if len(out) == WORD_SUGGESTIONS:
                break
    # Match how the writer is typing, so the pill drops straight in.
    if prefix[:1].isupper():
        out = [w.capitalize() for w in out]
    return out


def _bare(word: str) -> str:
    """A word with its punctuation stripped, for looking up in the word sets."""
    return re.sub(r"[^\w']", "", word).lower()


def _clean_phrase(raw: str) -> str:
    """
    Turn a raw continuation into a finished sentence, or into nothing.

    **Only a completed sentence is offered.** Measured across 17 realistic
    contexts, that one rule separates the good phrases from the bad ones
    exactly: every completion that reached a full stop read as writing ("had to
    go back and get it.", "using a variety of ingredients."), and every one that
    ran out of tokens first read as a fragment ("I was going to be in the
    middle", "dissolved it in a liquid solution, then used"). Trimming the
    fragments back word by word was tried first and could not save them — what
    is missing is the rest of the thought, not the last word.

    The cost is coverage: the pill now appears for roughly six contexts in ten
    instead of all of them. That is the right way round — the pill is optional,
    and a suggestion that has to be re-read is worse than no suggestion.
    """
    phrase = _PHRASE_STOP.split(raw, 1)[0].strip()

    ended = _SENTENCE_END.search(phrase)
    if not ended:
        return ""  # the model never finished the thought
    phrase = phrase[: ended.end()].strip()

    if _CONTRACTION_START.match(phrase):
        return ""

    words = phrase.split()
    if not PHRASE_MIN_WORDS <= len(words) <= PHRASE_MAX_WORDS:
        return ""
    if any(_bare(word) in _UNSUITABLE for word in words):
        return ""
    if not any(c.isalpha() for c in phrase):
        return ""
    return phrase


def _generate(context: str, max_new_tokens: int):
    """(tokenizer, output, prompt_length) or None when the model can't run."""
    import torch

    tokenizer, model = _get_model()
    if model is None:
        return None

    # Never feed a trailing space. GPT-2's BPE attaches a space to the word that
    # follows it ("Ġthe"), so a dangling space is a token the model has barely
    # seen, and it answers with word-continuation fragments: "She opened the
    # door and " predicted "iced it." Stripping it restores normal predictions,
    # and the caller re-inserts the spacing.
    context = context.rstrip()
    if not context:
        return None

    inputs = tokenizer(context, return_tensors="pt")
    prompt_length = inputs["input_ids"].shape[1]

    with _model_lock, torch.inference_mode():
        output = model.generate(
            **inputs,
            max_new_tokens=max_new_tokens,
            do_sample=False,               # deterministic: the same context
                                           # always offers the same pills
            repetition_penalty=1.2,
            pad_token_id=tokenizer.eos_token_id,
            output_scores=True,
            return_dict_in_generate=True,
        )
    return tokenizer, output, prompt_length


def _words_from_model(context: str) -> list[str]:
    """The three word pills — one forward pass, ~130ms."""
    import torch
    import wordfreq

    generated = _generate(context, WORD_STEP_TOKENS)
    if generated is None:
        return []
    tokenizer, output, _ = generated

    # The first generation step's distribution is the next-word distribution.
    first_step = output.scores[0][0]
    _, indices = torch.topk(first_step, TOP_K)

    words: list[str] = []
    seen: set[str] = set()
    for index in indices:
        token = tokenizer.decode([index])
        word = token.strip()
        # A leading space marks a word start; without it the token is a
        # continuation of the previous word, not a new one.
        if not token.startswith(" ") or not word.isalpha():
            continue
        if wordfreq.zipf_frequency(word, "en") <= 2.0:
            continue  # a fragment or something too obscure to offer
        if word.lower() in seen:
            continue
        seen.add(word.lower())
        words.append(word)
        if len(words) == WORD_SUGGESTIONS:
            break

    return words


def _phrase_from_model(context: str) -> str:
    """The phrase completion — the expensive call, ~90% of the old total."""
    generated = _generate(context, PHRASE_TOKENS)
    if generated is None:
        return ""
    tokenizer, output, prompt_length = generated

    # The continuation carries the leading space the model generated; callers
    # insert at a caret whose spacing they already know, so hand it over bare.
    return _clean_phrase(tokenizer.decode(output.sequences[0][prompt_length:])).lstrip()


def predict(text: str) -> dict:
    """
    The three word pills for the caret at the end of `text` (F29).

    Deliberately does *not* produce the phrase. One forward pass answers this in
    about 130ms, where generating a phrase as well took roughly 1.6s — and the
    pills are what has to keep up with typing. Callers ask for the phrase
    separately via predict_phrase().
    """
    available, reason = status()

    try:
        context = text[-CONTEXT_CHARS:]

        if not context.strip():
            # Nothing to condition on; the model would predict from its own
            # start-of-document prior, which is web boilerplate.
            return {
                "words": [],
                "available": available,
                "unavailable_reason": None if available else reason,
            }

        match = _TRAILING_WORD.search(context)
        if match:
            # Mid-word: finish the word being typed. A one-letter stub is too
            # ambiguous to rank, and handing the fragment to the model instead
            # just produces unrelated text, so offer nothing.
            prefix = match.group()
            return {
                "words": _complete_prefix(prefix) if len(prefix) >= MIN_PREFIX_LENGTH else [],
                "available": True,  # prefix search needs no model
                "unavailable_reason": None,
            }

        words = _words_from_model(context)
        available, reason = status()
        return {
            "words": words,
            "available": available,
            "unavailable_reason": None if available else reason,
        }

    except Exception as exc:  # noqa: BLE001
        # A prediction is a convenience; never let it break the writing page.
        logger.warning("Word prediction failed: %s", exc)
        return {
            "words": [],
            "available": False,
            "unavailable_reason": "Prediction failed.",
        }


def predict_phrase(text: str) -> dict:
    """
    A phrase completion for the caret at the end of `text` (v5.0).

    Split out from predict() because it is the slow half: the client fires it on
    a longer pause so the word pills are never held up behind it. Empty is a
    normal answer — mid-word there is nothing to complete, and at a boundary the
    model often fails to finish a sentence (see _clean_phrase).
    """
    available, reason = status()

    try:
        context = text[-CONTEXT_CHARS:]
        if not context.strip() or _TRAILING_WORD.search(context):
            return {
                "phrase": "",
                "available": available,
                "unavailable_reason": None if available else reason,
            }

        phrase = _phrase_from_model(context)
        available, reason = status()
        return {
            "phrase": phrase,
            "available": available,
            "unavailable_reason": None if available else reason,
        }

    except Exception as exc:  # noqa: BLE001
        logger.warning("Phrase prediction failed: %s", exc)
        return {"phrase": "", "available": False, "unavailable_reason": "Prediction failed."}
