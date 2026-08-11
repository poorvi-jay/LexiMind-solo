import { useEffect, useRef, useState } from 'react'

import { api } from '../utils/api'

/** F26-F28 — long enough that a check runs on a pause, not mid-word. */
export const NLP_DEBOUNCE_MS = 800

/** Matches MAX_CHECK_CHARS in backend/services/nlp_service.py. */
const MAX_CHECK_CHARS = 10_000

const EMPTY_COUNTS = { spelling: 0, grammar: 0, homophone: 0 }

/**
 * Debounced spelling / grammar / homophone checking for the notepad.
 *
 * Only the newest request is allowed to land: a slow check started three
 * keystrokes ago must not overwrite the results of a newer one, which would
 * leave the sidebar pointing at offsets that no longer exist in the text.
 */
export function useNLP(text, { enabled = true } = {}) {
  const [issues, setIssues] = useState([])
  const [counts, setCounts] = useState(EMPTY_COUNTS)
  const [checking, setChecking] = useState(false)
  const [grammarAvailable, setGrammarAvailable] = useState(true)
  const [error, setError] = useState(null)
  // The text the current `issues` were computed from. The offsets are only
  // valid against that exact string, so the UI can tell when they're stale.
  const [checkedText, setCheckedText] = useState('')

  const requestRef = useRef(0)

  useEffect(() => {
    if (!enabled) return

    // Every branch runs inside the timer, including the ones that just clear
    // state: React forbids a synchronous setState in an effect body.
    const timer = setTimeout(() => {
      if (!text.trim()) {
        setIssues([])
        setCounts(EMPTY_COUNTS)
        setCheckedText(text)
        setError(null)
        return
      }

      // The backend rejects anything longer, so don't spend a request on it.
      if (text.length > MAX_CHECK_CHARS) {
        setError(
          `Checking is limited to the first ${MAX_CHECK_CHARS.toLocaleString()} characters.`
        )
        return
      }

      const requestId = requestRef.current + 1
      requestRef.current = requestId
      setChecking(true)

      api
        .post('/nlp/check', { text })
        .then(data => {
          if (requestRef.current !== requestId) return // superseded
          setIssues(data.issues || [])
          setCounts(data.counts || EMPTY_COUNTS)
          setGrammarAvailable(Boolean(data.grammar_available))
          setCheckedText(text)
          setError(null)
        })
        .catch(err => {
          if (requestRef.current !== requestId) return
          setError(err?.message || 'Could not check your writing.')
        })
        .finally(() => {
          if (requestRef.current === requestId) setChecking(false)
        })
    }, NLP_DEBOUNCE_MS)

    return () => clearTimeout(timer)
  }, [text, enabled])

  return {
    issues,
    counts,
    checking,
    grammarAvailable,
    error,
    // Offsets only line up while the text is unchanged since the last check.
    stale: checkedText !== text,
  }
}
