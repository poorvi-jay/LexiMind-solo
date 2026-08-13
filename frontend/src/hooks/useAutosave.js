import { useCallback, useEffect, useRef, useState } from 'react'

import { ApiError, api } from '../utils/api'

/** F31 — the PRD's autosave cadence. */
export const AUTOSAVE_INTERVAL_MS = 30_000

/**
 * F31 — "retry 3×". A dropped request costs up to 30s of writing, so a failed
 * save is retried on the spot instead of waiting for the next tick.
 *
 * The delays are short on purpose: `saveNow()` is awaited before the notepad
 * switches documents, so this is time the writer spends watching "Saving…".
 * Worst case is ~3.5s of backoff before the switch is refused and the error
 * surfaces — after which the 30s tick keeps trying anyway, since a failed save
 * leaves the document dirty.
 */
const SAVE_RETRIES = 3
const RETRY_DELAYS_MS = [500, 1000, 2000]

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms))

/**
 * Whether another attempt could plausibly succeed.
 *
 * 4xx answers are the server's settled opinion — a 401 needs a new token and a
 * 422 needs different content, so hammering either just delays the error. 404 is
 * the exception: the document is gone, the caller clears the id, and the retry
 * writes the work to a fresh row instead of losing it.
 */
function worthRetrying(err) {
  if (!(err instanceof ApiError)) return true // network failure, no response
  if (err.status === 404) return true
  return !(err.status >= 400 && err.status < 500)
}

/**
 * Autosaves the writing notepad into the user's saved document.
 *
 * The caller owns `title`, `content` and the F48 `template` (the page needs them
 * anyway) and tells this hook what the server currently holds via adoptDocument()
 * once the draft has loaded. Until then `enabled` must stay false: saving
 * against an unknown baseline would push the empty textarea of a page that
 * hasn't finished loading over the user's real saved text.
 *
 * A save fires on the 30s tick, on demand via saveNow(), when the tab is
 * hidden, and on unmount — but only when something actually changed.
 */
export function useAutosave({ title, content, template, enabled }) {
  const [documentId, setDocumentId] = useState(null)
  // What the server holds. Only ever set from a completed request or from
  // adoptDocument, so it always reflects a real round trip.
  const [baseline, setBaseline] = useState({ title: '', content: '', template: null })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)
  const [lastSavedAt, setLastSavedAt] = useState(null)

  // The timer and the unmount flush need the current values without
  // re-subscribing on every keystroke. Written after each commit rather than
  // during render, which React forbids.
  const latest = useRef({ title, content, template, documentId, baseline, enabled })
  useEffect(() => {
    latest.current = { title, content, template, documentId, baseline, enabled }
  })

  // `saving` is React state, so it lags a synchronous second call. The guard
  // against overlapping saves has to be a ref to be reliable.
  const inFlightRef = useRef(false)

  const dirty =
    enabled &&
    (title !== baseline.title ||
      content !== baseline.content ||
      template !== baseline.template)

  // Resolves true when the server is up to date — either the save landed or
  // there was nothing to send. Callers that are about to replace what's in the
  // notepad (opening another document) must not proceed on false.
  const save = useCallback(async () => {
    const snapshot = latest.current
    if (!snapshot.enabled) return true
    if (inFlightRef.current) return false // a request is already in flight
    if (
      snapshot.title === snapshot.baseline.title &&
      snapshot.content === snapshot.baseline.content &&
      snapshot.template === snapshot.baseline.template
    ) {
      return true // nothing changed since the last successful save
    }
    // An untouched page shouldn't leave an empty document behind.
    if (!snapshot.documentId && !snapshot.content.trim()) return true

    inFlightRef.current = true
    setSaving(true)
    setError(null)

    // Held across attempts rather than read from the snapshot: a 404 clears it
    // so the retry writes a fresh document instead of one that no longer exists.
    let targetId = snapshot.documentId

    try {
      for (let attempt = 0; ; attempt++) {
        try {
          const saved = await api.patch('/writing/autosave', {
            document_id: targetId,
            title: snapshot.title,
            content: snapshot.content,
            template: snapshot.template,
          })
          setDocumentId(saved.id)
          // The server's echo is the truth for template: it ignores a null, so a
          // save that didn't carry one keeps whatever was already recorded.
          //
          // The baseline is the snapshot, not the current values — anything
          // typed during a retry is still unsaved and leaves the page dirty for
          // the next tick to pick up.
          setBaseline({
            title: snapshot.title,
            content: snapshot.content,
            template: saved.template ?? null,
          })
          setLastSavedAt(new Date())
          return true
        } catch (err) {
          // 404 means the document is gone (deleted elsewhere); forget the id so
          // this save writes a fresh one instead of failing forever.
          if (err instanceof ApiError && err.status === 404) {
            targetId = null
            setDocumentId(null)
          }
          if (attempt >= SAVE_RETRIES || !worthRetrying(err)) {
            setError(err?.message || 'Could not save.')
            return false
          }
          await sleep(RETRY_DELAYS_MS[attempt])
        }
      }
    } finally {
      inFlightRef.current = false
      setSaving(false)
    }
  }, [])

  /** Record what the server already has, after the draft has been fetched. */
  const adoptDocument = useCallback(doc => {
    setDocumentId(doc?.id ?? null)
    setBaseline({
      title: doc?.title ?? '',
      content: doc?.content ?? '',
      template: doc?.template ?? null,
    })
    setLastSavedAt(null)
    setError(null)
  }, [])

  /* ── The 30s tick ── */
  useEffect(() => {
    if (!enabled) return
    const timer = setInterval(save, AUTOSAVE_INTERVAL_MS)
    return () => clearInterval(timer)
  }, [enabled, save])

  /* ── Flush when the tab is hidden or the page is left ── */
  // Best effort: a request started as the tab goes away may be cancelled, so
  // this supplements the tick rather than replacing it.
  useEffect(() => {
    const handleHide = () => {
      if (document.visibilityState === 'hidden') save()
    }
    document.addEventListener('visibilitychange', handleHide)
    return () => {
      document.removeEventListener('visibilitychange', handleHide)
      save()
    }
  }, [save])

  return { documentId, dirty, saving, error, lastSavedAt, saveNow: save, adoptDocument }
}
