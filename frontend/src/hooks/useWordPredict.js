import { useCallback, useEffect, useRef, useState } from 'react'

import { api } from '../utils/api'

/**
 * F29 — much tighter than the 800ms check debounce. Prediction is meant to keep
 * up with typing, and the backend answers a mid-word request from a vocabulary
 * lookup in single-digit milliseconds.
 */
export const PREDICT_DEBOUNCE_MS = 250

/** Matches MAX_CHECK_CHARS in backend/services/nlp_service.py. */
const MAX_CONTEXT_CHARS = 10_000

const EMPTY = { words: [], phrase: '' }

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
  const [suggestions, setSuggestions] = useState(EMPTY)
  const [predicting, setPredicting] = useState(false)
  const [available, setAvailable] = useState(true)

  const requestRef = useRef(0)

  useEffect(() => {
    if (!enabled) return

    const timer = setTimeout(() => {
      // Nothing to predict from, and nothing worth a request.
      if (!context.trim() || context.length > MAX_CONTEXT_CHARS) {
        requestRef.current += 1 // cancel anything in flight
        setSuggestions(EMPTY)
        return
      }

      const requestId = requestRef.current + 1
      requestRef.current = requestId
      setPredicting(true)

      api
        .post('/nlp/predict', { text: context })
        .then(data => {
          if (requestRef.current !== requestId) return // superseded
          setSuggestions({ words: data.words || [], phrase: data.phrase || '' })
          setAvailable(Boolean(data.available))
        })
        .catch(() => {
          if (requestRef.current !== requestId) return
          // Prediction is a convenience; a failure just means no pills.
          setSuggestions(EMPTY)
        })
        .finally(() => {
          if (requestRef.current === requestId) setPredicting(false)
        })
    }, PREDICT_DEBOUNCE_MS)

    return () => clearTimeout(timer)
  }, [context, enabled])

  const clear = useCallback(() => {
    requestRef.current += 1
    setSuggestions(EMPTY)
  }, [])

  return { ...suggestions, predicting, available, clear }
}
