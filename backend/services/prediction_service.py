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

Both the three word pills and the phrase come out of a single generate() call:
its first-step scores give the next-word candidates and its output gives the
phrase, which costs one model pass (~0.45s) instead of two (~0.6s).

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
PHRASE_TOKENS = 8         # roughly a short clause
TOP_K = 60                # candidate next-tokens to filter down to whole words

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
_SENTENCE_END = re.compile(r"[.!?]$")


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


def _clean_phrase(raw: str) -> str:
    """
    Trim a raw continuation into something safe to insert.

    Returns "" rather than anything doubtful — a bad phrase pill is worse than
    no phrase pill, and the UI only shows it when it is non-empty.
    """
    phrase = _PHRASE_STOP.split(raw, 1)[0]
    phrase = phrase.rstrip()
    if not phrase.strip():
        return ""

    # Hitting the token limit usually truncates the last word, so drop it unless
    # the phrase ended on its own at a sentence boundary.
    if not _SENTENCE_END.search(phrase):
        parts = phrase.rsplit(" ", 1)
        phrase = parts[0] if len(parts) > 1 else ""

    phrase = phrase.rstrip()
    # Punctuation-only leftovers ("  .") are not worth offering.
    if not any(c.isalpha() for c in phrase):
        return ""
    return phrase


def _predict_from_model(context: str) -> tuple[list[str], str]:
    """Next-word candidates and a phrase completion, from one generate() call."""
    import torch
    import wordfreq

    tokenizer, model = _get_model()
    if model is None:
        return [], ""

    # Never feed a trailing space. GPT-2's BPE attaches a space to the word that
    # follows it ("Ġthe"), so a dangling space is a token the model has barely
    # seen, and it answers with word-continuation fragments: "She opened the
    # door and " predicted "iced it." Stripping it restores normal predictions,
    # and the caller re-inserts the spacing.
    context = context.rstrip()
    if not context:
        return [], ""

    inputs = tokenizer(context, return_tensors="pt")
    prompt_length = inputs["input_ids"].shape[1]

    with _model_lock, torch.inference_mode():
        output = model.generate(
            **inputs,
            max_new_tokens=PHRASE_TOKENS,
            do_sample=False,               # deterministic: the same context
                                           # always offers the same pills
            repetition_penalty=1.2,
            pad_token_id=tokenizer.eos_token_id,
            output_scores=True,
            return_dict_in_generate=True,
        )

    # ── word pills, from the first generation step's distribution ──
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

    # The continuation carries the leading space the model generated; callers
    # insert at a caret whose spacing they already know, so hand it over bare.
    phrase = _clean_phrase(tokenizer.decode(output.sequences[0][prompt_length:])).lstrip()
    return words, phrase


def predict(text: str) -> dict:
    """
    Suggestions for the caret position at the end of `text`.

    `words` holds up to three single-word pills and `phrase` a longer
    completion, empty when there isn't a good one.
    """
    available, reason = status()

    try:
        context = text[-CONTEXT_CHARS:]

        if not context.strip():
            # Nothing to condition on; the model would predict from its own
            # start-of-document prior, which is web boilerplate.
            return {
                "words": [],
                "phrase": "",
                "available": available,
                "unavailable_reason": None if available else reason,
            }

        match = _TRAILING_WORD.search(context)
        if match:
            # Mid-word: finish the word being typed, no phrase. A one-letter
            # stub is too ambiguous to rank, and handing the fragment to the
            # model instead just produces unrelated text, so offer nothing.
            prefix = match.group()
            return {
                "words": _complete_prefix(prefix) if len(prefix) >= MIN_PREFIX_LENGTH else [],
                "phrase": "",
                "available": True,  # prefix search needs no model
                "unavailable_reason": None,
            }

        words, phrase = _predict_from_model(context)
        available, reason = status()
        return {
            "words": words,
            "phrase": phrase,
            "available": available,
            "unavailable_reason": None if available else reason,
        }

    except Exception as exc:  # noqa: BLE001
        # A prediction is a convenience; never let it break the writing page.
        logger.warning("Prediction failed: %s", exc)
        return {
            "words": [],
            "phrase": "",
            "available": False,
            "unavailable_reason": "Prediction failed.",
        }
