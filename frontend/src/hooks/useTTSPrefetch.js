import { useCallback, useRef } from 'react'

import { BASE_URL } from '../utils/api'

// Must match the voice used for real playback (useTTSPlayer.js / ReadingPage.jsx),
// otherwise a cache hit would silently play back in the wrong voice.
const DEFAULT_VOICE = 'en-GB-SoniaNeural'

/**
 * Pre-fetches TTS audio in the background so playback is instant.
 * Uses the fast binary endpoint when available, falls back to JSON.
 */
export function useTTSPrefetch() {
  const cacheRef = useRef({})
  const pendingRef = useRef(null)

  const makeKey = (text, speed, phrasePauses) =>
    `${text}|${speed}|${phrasePauses}`

  /* ── Try fast binary endpoint, fall back to JSON ── */
  const fetchFast = async (text, speed, phrasePauses) => {
    const body = JSON.stringify({
      text,
      speed,
      voice: DEFAULT_VOICE,
      phrase_pauses: phrasePauses,
    })

    // Try binary endpoint first
    try {
      const res = await fetch(`${BASE_URL}/tts/generate-fast`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body,
      })

      if (res.ok) {
        const blob = await res.blob()
        const timingsHeader = res.headers.get('X-Word-Timings')
        const durationHeader = res.headers.get('X-Duration-Ms')

        if (timingsHeader && durationHeader) {
          return {
            blob,
            word_timings: JSON.parse(timingsHeader),
            duration_ms: parseInt(durationHeader, 10),
          }
        }
      }
    } catch {
      // Fall through to JSON endpoint
    }

    // Fallback — JSON endpoint
    const res = await fetch(`${BASE_URL}/tts/generate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body,
    })

    if (!res.ok) throw new Error('TTS prefetch failed')

    const data = await res.json()

    const binary = atob(data.audio_b64)
    const bytes  = new Uint8Array(binary.length)
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i)

    return {
      blob: new Blob([bytes], { type: 'audio/mpeg' }),
      word_timings: data.word_timings,
      duration_ms: data.duration_ms || 0,
    }
  }
  // ↑ BUG FIX: This closing brace was MISSING in the original file.
  //   The original code had fetchFast's body flow directly into prefetch,
  //   causing a syntax error.

  /* ── Prefetch — call when text/speed changes ── */
  const prefetch = useCallback(async (text, speed, phrasePauses) => {
    if (!text.trim()) return

    const key = makeKey(text, speed, phrasePauses)

    if (cacheRef.current[key]) return
    if (pendingRef.current === key) return

    pendingRef.current = key

    try {
      const result = await fetchFast(text, speed, phrasePauses)
      cacheRef.current[key] = result
    } catch (err) {
      console.warn('TTS prefetch failed:', err)
    } finally {
      if (pendingRef.current === key) {
        pendingRef.current = null
      }
    }
  }, [])

  /* ── Get cached result (null if not ready) ── */
  const getCached = useCallback((text, speed, phrasePauses) => {
    const key = makeKey(text, speed, phrasePauses)
    return cacheRef.current[key] || null
  }, [])

  /* ── Check if prefetch is in progress ── */
  const isPrefetching = useCallback((text, speed, phrasePauses) => {
    const key = makeKey(text, speed, phrasePauses)
    return pendingRef.current === key
  }, [])

  /* ── Clear all cached audio ── */
  const clearCache = useCallback(() => {
    cacheRef.current = {}
  }, [])

  return { prefetch, getCached, isPrefetching, clearCache }
}