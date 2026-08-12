import { useCallback, useState } from 'react'

import { api } from '../utils/api'

/**
 * F32 named documents — the library beside the notepad.
 *
 * This hook owns the *list*; the page owns the text being edited. Opening a
 * document therefore returns it rather than storing it, so the page stays the
 * single source of truth for what is in the textarea and there is no second
 * copy to drift.
 *
 * `refresh` is called after anything that changes the library (a new document,
 * a delete, a save that renamed one), because the list carries titles and
 * timestamps that go stale the moment the notepad is saved.
 */
export function useDocuments() {
  const [documents, setDocuments] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [loaded, setLoaded] = useState(false)

  const refresh = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const rows = await api.get('/writing/documents')
      setDocuments(rows)
      setLoaded(true)
      return true
    } catch (err) {
      setError(err?.message || 'Could not load your documents.')
      return false
    } finally {
      setLoading(false)
    }
  }, [])

  /** Save the current work as a new document. Returns it, or null on failure. */
  const createDocument = useCallback(async ({ title, content, template }) => {
    return api.post('/writing/documents', { title, content, template })
  }, [])

  /** Fetch one document in full, for loading into the notepad. */
  const fetchDocument = useCallback(async id => api.get(`/writing/documents/${id}`), [])

  const deleteDocument = useCallback(async id => {
    await api.delete(`/writing/documents/${id}`)
    // Drop it locally too, so the row disappears without waiting for a refetch.
    setDocuments(current => current.filter(doc => doc.id !== id))
  }, [])

  return {
    documents,
    loading,
    loaded,
    error,
    refresh,
    createDocument,
    fetchDocument,
    deleteDocument,
  }
}
