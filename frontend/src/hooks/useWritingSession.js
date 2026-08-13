import { useCallback, useEffect, useRef } from 'react'

import { api } from '../utils/api'

/** Matches the autosave cadence — the two report on the same rhythm. */
export const SESSION_FLUSH_INTERVAL_MS = 30_000

/**
 * F48 — logs the writing session's counters, including `template_used`.
 *
 * This is analytics, not the writer's work: every failure is swallowed. A
 * counter that doesn't reach the server is worth nothing next to interrupting
 * someone mid-sentence, and `useAutosave` is what guards the actual text.
 *
 * One row per document per visit. The row is created lazily on the first report
 * that has words in it, so opening the notepad and walking away leaves nothing
 * behind, and it is upserted from then on rather than appended to.
 */
export function useWritingSession({ documentId, wordCount, counts, template, enabled }) {
  const sessionIdRef = useRef(null)
  // The last payload actually accepted, so an unchanged session stops talking.
  const lastSentRef = useRef(null)
  const inFlightRef = useRef(false)

  const latest = useRef({ documentId, wordCount, counts, template, enabled })
  useEffect(() => {
    latest.current = { documentId, wordCount, counts, template, enabled }
  })

  // Moving to another document starts a new session. `documentId` goes from
  // null to a real id the first time autosave creates the document, and that is
  // the same session continuing — only a change between two real ids is a
  // switch.
  const previousDocumentRef = useRef(documentId)
  useEffect(() => {
    const previous = previousDocumentRef.current
    previousDocumentRef.current = documentId
    if (previous !== null && documentId !== null && documentId !== previous) {
      sessionIdRef.current = null
      lastSentRef.current = null
    }
  }, [documentId])

  const report = useCallback(async () => {
    const snapshot = latest.current
    if (!snapshot.enabled || inFlightRef.current) return
    // Nothing written and no row yet — there is no session to speak of.
    if (!snapshot.wordCount && !sessionIdRef.current) return

    const payload = {
      word_count: snapshot.wordCount,
      spell_error_count: snapshot.counts?.spelling ?? 0,
      grammar_error_count: snapshot.counts?.grammar ?? 0,
      homophone_flag_count: snapshot.counts?.homophone ?? 0,
      template_used: snapshot.template,
    }

    const fingerprint = JSON.stringify(payload)
    if (fingerprint === lastSentRef.current) return

    inFlightRef.current = true
    try {
      const saved = await api.patch('/writing/session', {
        session_id: sessionIdRef.current,
        ...payload,
      })
      sessionIdRef.current = saved.id
      lastSentRef.current = fingerprint
    } catch {
      // Deliberately silent. The next tick tries again with fresher numbers,
      // and a lost counter is not something to tell the writer about.
      //
      // A 404 (row deleted elsewhere) is covered too: the id stays as it is and
      // keeps failing until the session ends, which costs nothing but a row.
    } finally {
      inFlightRef.current = false
    }
  }, [])

  useEffect(() => {
    if (!enabled) return
    const timer = setInterval(report, SESSION_FLUSH_INTERVAL_MS)
    return () => clearInterval(timer)
  }, [enabled, report])

  // Leaving the page is when the session's final numbers are known, so report
  // then as well as on the tick. Best effort — the request may be cancelled as
  // the tab goes away.
  useEffect(() => {
    const handleHide = () => {
      if (document.visibilityState === 'hidden') report()
    }
    document.addEventListener('visibilitychange', handleHide)
    return () => {
      document.removeEventListener('visibilitychange', handleHide)
      report()
    }
  }, [report])
}
