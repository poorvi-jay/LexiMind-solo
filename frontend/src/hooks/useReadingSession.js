import { useCallback, useEffect, useRef } from 'react'

import { api } from '../utils/api'

/**
 * The PRD's minimum for a reading session — anything shorter is an accidental
 * open, not a read. The server enforces the same number (MIN_READING_SECONDS in
 * backend/services/session_service.py); this copy only saves a pointless request.
 */
export const MIN_SESSION_SECONDS = 30

/**
 * F37 — times a reading session and reports it once, when it ends.
 *
 * A session is one stretch of listening to one loaded text. It ends on Stop,
 * when the audio finishes, when different text is loaded, or when the reader
 * leaves the page. It does not end on a pause, nor on a speed change — which
 * stops and restarts the player under the hood.
 *
 * Time is banked only while `isPlaying` is true. Every other state — paused,
 * stopped, the gap while a speed change regenerates audio — is simply never
 * counted, so "active playback, excluding pauses" needs no special cases.
 *
 * Word replays (the reader tapping a word to hear it) ride along in the same
 * report, and are sent even for a session too short to log: they are never
 * accidental and need no playback. See backend/services/session_service.py.
 *
 * Like useWritingSession this is analytics, so every failure is swallowed.
 */
export function useReadingSession({
  isPlaying, isPaused, isPreparing,
  activeIndex, totalWords, hardWordCount,
  sourceType, simplified, complexityScore,
}) {
  const latest = useRef({ totalWords, hardWordCount, sourceType, simplified, complexityScore })
  useEffect(() => {
    latest.current = { totalWords, hardWordCount, sourceType, simplified, complexityScore }
  })

  const startedAtRef    = useRef(null) // wall clock at this session's first playback
  const segmentStartRef = useRef(null) // performance.now() when the current stretch began
  const activeMsRef     = useRef(0)    // playback time banked from finished stretches
  const furthestRef     = useRef(-1)   // furthest word index reached, for WPM
  const repeatsRef      = useRef({})   // word -> taps during this session

  const end = useCallback(({ keepalive = false } = {}) => {
    // Close a stretch still in progress. The banking effect below normally has
    // already, but not when end() runs from an event handler in the same tick
    // as the state change — Stop, or new text loaded while audio plays. In that
    // second case any audio that keeps going is not carried into the next
    // session; it is the old text's audio, and it stops being counted here.
    if (segmentStartRef.current !== null) {
      activeMsRef.current += performance.now() - segmentStartRef.current
      segmentStartRef.current = null
    }

    const snapshot = latest.current
    const report = {
      started_at: startedAtRef.current ? startedAtRef.current.toISOString() : null,
      duration_seconds: Math.round(activeMsRef.current / 1000),
      words_read: Math.max(0, Math.min(furthestRef.current + 1, snapshot.totalWords)),
      total_words: snapshot.totalWords,
      hard_word_count: snapshot.hardWordCount,
      source_type: snapshot.sourceType,
      simplified: snapshot.simplified,
      complexity_score: snapshot.complexityScore,
      word_repeats: repeatsRef.current,
    }

    // Reset before sending, so a second end() moments later — the player's own
    // stop straight after the Stop button, or an unmount — finds nothing and
    // cannot log the same session twice.
    startedAtRef.current = null
    activeMsRef.current = 0
    furthestRef.current = -1
    repeatsRef.current = {}

    const longEnough = report.duration_seconds >= MIN_SESSION_SECONDS
    const hasRepeats = Object.keys(report.word_repeats).length > 0
    if (!longEnough && !hasRepeats) return

    api.post('/sessions/reading', report, { keepalive }).catch(() => {
      // Deliberately silent — a lost analytics row is not worth interrupting a
      // reader over.
    })
  }, [])

  // Bank playing time on every transition of isPlaying.
  useEffect(() => {
    if (isPlaying) {
      if (startedAtRef.current === null) startedAtRef.current = new Date()
      segmentStartRef.current = performance.now()
    } else if (segmentStartRef.current !== null) {
      activeMsRef.current += performance.now() - segmentStartRef.current
      segmentStartRef.current = null
    }
  }, [isPlaying])

  useEffect(() => {
    if (activeIndex > furthestRef.current) furthestRef.current = activeIndex
  }, [activeIndex])

  // The player stopping on its own — the audio finishing, or an error — ends
  // the session. A pause does not (isPaused). Neither does a speed change: its
  // handler marks isPreparing before stopping the old audio, and React batches
  // the two, so this never sees the stop without the flag.
  const wasPlayingRef = useRef(false)
  useEffect(() => {
    const stoppedOnItsOwn = wasPlayingRef.current && !isPlaying && !isPaused && !isPreparing
    wasPlayingRef.current = isPlaying
    if (stoppedOnItsOwn) end()
  }, [isPlaying, isPaused, isPreparing, end])

  // Leaving: closing the tab, or navigating to another route. keepalive lets
  // the request outlive the page it was sent from.
  useEffect(() => {
    const handlePageHide = () => end({ keepalive: true })
    window.addEventListener('pagehide', handlePageHide)
    return () => {
      window.removeEventListener('pagehide', handlePageHide)
      end({ keepalive: true })
    }
  }, [end])

  const recordReplay = useCallback(word => {
    if (!word) return
    repeatsRef.current[word] = (repeatsRef.current[word] || 0) + 1
  }, [])

  return { recordReplay, end }
}
