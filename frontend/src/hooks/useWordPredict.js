import { useCallback, useEffect, useRef, useState } from 'react'

import { api } from '../utils/api'

/**
 * F29 — much tighter than the 800ms check debounce. Prediction is meant to keep
 * up with typing, and the backend answers a mid-word request from a vocabulary
 * lookup in single-digit milliseconds.
 */
export const PREDICT_DEBOUNCE_MS = 250

/**
 * v5.0 — the phrase is a separate, much slower request (~1.5s of model time
 * against ~130ms for the pills), so it waits for a real pause in typing rather
 * than firing on the same beat as the pills. Nothing on screen is held up by
 * it: the pills land on their own debounce and the phrase appears when it is
 * ready.
 */
export const PHRASE_DEBOUNCE_MS = 900

/** Matches MAX_CHECK_CHARS in backend/services/nlp_service.py. */
const MAX_CONTEXT_CHARS = 10_000

/**
 * Word and phrase suggestions for the caret position.
 *
 * `context` is the text *before* the caret, so this re-runs when the caret
 * moves as well as when the text changes — clicking into the middle of a
 * sentence should predict from there, not from the end of the document.
 *
 * Only the newest response is allowed to land, so a slow prediction can't
 * replace the suggestions for a position the writer has already left.
 */
export function useWordPredict(context, { enabled = true } = {}) {
  const [words, setWords] = useState([])
  const [predicting, setPredicting] = useState(false)
  const [available, setAvailable] = useState(true)
  // Kept with the context it was produced for. The phrase arrives up to a
  // second and a half after the pills, and a phrase that completes a sentence
  // the writer has already moved past is worse than no phrase, so it is matched
  // on read rather than cleared by an effect.
  const [phraseResult, setPhraseResult] = useState({ context: null, phrase: '' })

  const wordRequest = useRef(0)
  const phraseRequest = useRef(0)

  const usable = enabled && Boolean(context.trim()) && context.length <= MAX_CONTEXT_CHARS

  /* ── the pills (fast) ── */
  useEffect(() => {
    if (!enabled) return

    const timer = setTimeout(() => {
      if (!usable) {
        wordRequest.current += 1 // cancel anything in flight
        setWords([])
        return
      }

      const requestId = wordRequest.current + 1
      wordRequest.current = requestId
      setPredicting(true)

      api
        .post('/nlp/predict', { text: context })
        .then(data => {
          if (wordRequest.current !== requestId) return // superseded
          setWords(data.words || [])
          setAvailable(Boolean(data.available))
        })
        .catch(() => {
          if (wordRequest.current !== requestId) return
          // Prediction is a convenience; a failure just means no pills.
          setWords([])
        })
        .finally(() => {
          if (wordRequest.current === requestId) setPredicting(false)
        })
    }, PREDICT_DEBOUNCE_MS)

    return () => clearTimeout(timer)
  }, [context, enabled, usable])

  /* ── the phrase (slow, on a pause) ── */
  useEffect(() => {
    if (!enabled || !usable) return

    const timer = setTimeout(() => {
      const requestId = phraseRequest.current + 1
      phraseRequest.current = requestId

      api
        .post('/nlp/predict/phrase', { text: context })
        .then(data => {
          if (phraseRequest.current !== requestId) return // superseded
          setPhraseResult({ context, phrase: data.phrase || '' })
        })
        .catch(() => {
          if (phraseRequest.current !== requestId) return
          setPhraseResult({ context, phrase: '' })
        })
    }, PHRASE_DEBOUNCE_MS)

    return () => clearTimeout(timer)
  }, [context, enabled, usable])

  const clear = useCallback(() => {
    wordRequest.current += 1
    phraseRequest.current += 1
    setWords([])
    setPhraseResult({ context: null, phrase: '' })
  }, [])

  return {
    words,
    // Only ever the phrase for the text that is actually in front of the caret.
    phrase: phraseResult.context === context ? phraseResult.phrase : '',
    predicting,
    available,
    clear,
  }
}
