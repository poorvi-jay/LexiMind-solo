import { useCallback, useEffect, useRef, useState } from 'react'

import { ApiError, api } from '../utils/api'

/** F31 — the PRD's autosave cadence. */
export const AUTOSAVE_INTERVAL_MS = 30_000

/**
 * Autosaves the writing notepad into the user's saved document.
 *
 * The caller owns `title` and `content` (the textarea needs them anyway) and
 * tells this hook what the server currently holds by calling adoptDocument()
 * once the draft has loaded. Until then `enabled` must stay false: saving
 * against an unknown baseline would push the empty textarea of a page that
 * hasn't finished loading over the user's real saved text.
 *
 * A save fires on the 30s tick, on demand via saveNow(), when the tab is
 * hidden, and on unmount — but only when something actually changed.
 */
export function useAutosave({ title, content, enabled }) {
  const [documentId, setDocumentId] = useState(null)
  // What the server holds. Only ever set from a completed request or from
  // adoptDocument, so it always reflects a real round trip.
  const [baseline, setBaseline] = useState({ title: '', content: '' })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)
  const [lastSavedAt, setLastSavedAt] = useState(null)

  // The timer and the unmount flush need the current values without
  // re-subscribing on every keystroke. Written after each commit rather than
  // during render, which React forbids.
  const latest = useRef({ title, content, documentId, baseline, saving, enabled })
  useEffect(() => {
    latest.current = { title, content, documentId, baseline, saving, enabled }
  })

  const dirty = enabled && (title !== baseline.title || content !== baseline.content)

  const save = useCallback(async () => {
    const snapshot = latest.current
    if (!snapshot.enabled || snapshot.saving) return
    if (
      snapshot.title === snapshot.baseline.title &&
      snapshot.content === snapshot.baseline.content
    ) {
      return // nothing changed since the last successful save
    }
    // An untouched page shouldn't leave an empty document behind.
    if (!snapshot.documentId && !snapshot.content.trim()) return

    setSaving(true)
    setError(null)
    try {
      const saved = await api.patch('/writing/autosave', {
        document_id: snapshot.documentId,
        title: snapshot.title,
        content: snapshot.content,
      })
      setDocumentId(saved.id)
      setBaseline({ title: snapshot.title, content: snapshot.content })
      setLastSavedAt(new Date())
    } catch (err) {
      // 404 means the document is gone (deleted elsewhere); forget the id so
      // the next save writes a fresh one instead of failing forever.
      if (err instanceof ApiError && err.status === 404) setDocumentId(null)
      setError(err?.message || 'Could not save.')
    } finally {
      setSaving(false)
    }
  }, [])

  /** Record what the server already has, after the draft has been fetched. */
  const adoptDocument = useCallback(doc => {
    setDocumentId(doc?.id ?? null)
    setBaseline({ title: doc?.title ?? '', content: doc?.content ?? '' })
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
