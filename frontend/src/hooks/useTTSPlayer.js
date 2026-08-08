import { useCallback, useRef, useState } from 'react'

import { api } from '../utils/api'

export function useTTSPlayer(onWordChange) {
  const [isPlaying, setIsPlaying]             = useState(false)
  const [isPaused, setIsPaused]               = useState(false)
  const [isLoading, setIsLoading]             = useState(false)
  const [error, setError]                     = useState(null)
  const [totalDurationMs, setTotalDurationMs] = useState(0)

  const audioRef      = useRef(null)
  const timingsRef    = useRef([])
  const wordIndexRef  = useRef(-1)
  const baseIndexRef  = useRef(0)
  const rafRef        = useRef(null)
  const scaleRef      = useRef(1)

  /* ══════════════════════════════════════════════
     SCALE FACTOR
     ══════════════════════════════════════════════ */
  const computeScale = useCallback(() => {
    const audio   = audioRef.current
    const timings = timingsRef.current

    if (!audio || !timings.length || !isFinite(audio.duration) || audio.duration === 0) {
      scaleRef.current = 1
      return
    }

    const audioDurationMs = audio.duration * 1000
    const last            = timings[timings.length - 1]
    const lastEndMs       = (last.start_ms || 0) + (last.duration_ms || 0)

    if (lastEndMs <= 0) {
      scaleRef.current = 1
      return
    }

    scaleRef.current = audioDurationMs / lastEndMs

    console.log(
      `[TTS sync] audio: ${Math.round(audioDurationMs)}ms, ` +
      `timings span: ${Math.round(lastEndMs)}ms → ` +
      `scale: ${scaleRef.current.toFixed(3)}`
    )
  }, [])

  /* ══════════════════════════════════════════════
     SYNC ENGINE
     ══════════════════════════════════════════════ */
  const findWordIndex = useCallback((timings, timingMs) => {
    let lo = 0
    let hi = timings.length - 1
    let result = -1

    while (lo <= hi) {
      const mid = (lo + hi) >>> 1
      if (timings[mid].start_ms <= timingMs) {
        result = mid
        lo = mid + 1
      } else {
        hi = mid - 1
      }
    }
    return result
  }, [])

  const startSync = useCallback(() => {
    if (rafRef.current) cancelAnimationFrame(rafRef.current)

    const tick = () => {
      const audio   = audioRef.current
      const timings = timingsRef.current
      const scale   = scaleRef.current

      if (!audio || audio.paused || !timings.length) {
        rafRef.current = null
        return
      }

      const audioMs  = audio.currentTime * 1000
      const timingMs = scale > 0 ? audioMs / scale : audioMs
      const wordIdx  = findWordIndex(timings, timingMs)

      if (wordIdx !== wordIndexRef.current) {
        wordIndexRef.current = wordIdx
        onWordChange(wordIdx >= 0 ? baseIndexRef.current + wordIdx : -1)

      }

      rafRef.current = requestAnimationFrame(tick)
    }

    rafRef.current = requestAnimationFrame(tick)
  }, [findWordIndex, onWordChange])

  const stopSync = useCallback(() => {
    if (rafRef.current) {
      cancelAnimationFrame(rafRef.current)
      rafRef.current = null
    }
  }, [])

  /* ══════════════════════════════════════════════
     RELEASE — tear down the current element safely
     ══════════════════════════════════════════════ */
  const releaseAudio = useCallback(() => {
    const audio = audioRef.current
    if (!audio) return

    // Detach the handlers first. Revoking a blob URL while the element still
    // points at it makes the element fire `error`, and a stale handler would
    // then tear down whatever playback has started in the meantime — flipping
    // the UI back to "Play" and killing the new highlight loop mid-sentence.
    audio.onended = null
    audio.onerror = null
    audio.onstalled = null

    audio.pause()

    const src = audio.src
    audio.removeAttribute('src')
    audio.load() // resets the element so it lets go of the blob
    if (src) URL.revokeObjectURL(src)

    audioRef.current = null
  }, [])

  /* ══════════════════════════════════════════════
     PLAY — fixed: no double src assignment
     ══════════════════════════════════════════════ */
  const play = useCallback(async (text, speed = 1.0, phrasePauses = true, prefetched = null, baseIndex = 0) => {
    try {
      setIsLoading(true)
      setError(null)
      setIsPaused(false)
      stopSync()

      

      releaseAudio()

      let blob, wordTimings, durationMs

      if (prefetched) {
        blob        = prefetched.blob
        wordTimings = prefetched.word_timings
        durationMs  = prefetched.duration_ms || 0
      } else {
        const data = await api.post('/tts/generate', {
          text,
          speed,
          voice: 'en-GB-SoniaNeural',
          phrase_pauses: phrasePauses,
        })

        const binary = atob(data.audio_b64)
        const bytes  = new Uint8Array(binary.length)
        for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i)
        blob        = new Blob([bytes], { type: 'audio/mpeg' })
        wordTimings = data.word_timings
        durationMs  = data.duration_ms || 0
      }

      // Log for debugging
      console.log(`[TTS] received ${wordTimings?.length || 0} word timings`)
      if (wordTimings?.length > 0) {
        console.log(`[TTS] first word: "${wordTimings[0].word}" at ${wordTimings[0].start_ms}ms`)
        const last = wordTimings[wordTimings.length - 1]
        console.log(`[TTS] last word: "${last.word}" at ${last.start_ms}ms`)
      }

      const url = URL.createObjectURL(blob)

      // ── FIX: Create Audio WITHOUT url, then set src once ──
      const audio = new Audio()

      audioRef.current     = audio
      timingsRef.current   = wordTimings || []
      wordIndexRef.current = -1
      baseIndexRef.current = baseIndex
      scaleRef.current     = 1
      setTotalDurationMs(durationMs)

      // Wait for metadata before playing
      const metadataReady = new Promise((resolve) => {
        audio.addEventListener('loadedmetadata', () => {
          console.log(`[TTS] audio duration from metadata: ${audio.duration}s`)
          resolve()
        }, { once: true })
      })

      // Handle canplaythrough for extra safety
      const canPlay = new Promise((resolve) => {
        audio.addEventListener('canplaythrough', resolve, { once: true })
      })

      audio.addEventListener('durationchange', () => {
        console.log(`[TTS] duration changed to: ${audio.duration}s`)
        computeScale()
      })

      // Set src ONCE and load
      audio.src = url
      audio.preload = 'auto'
      audio.load()

      // Wait for at least metadata
      await metadataReady

      // Also wait for canplaythrough to avoid early stop
      await canPlay

      computeScale()

      // Both handlers bail out if this element has since been replaced, so a
      // late event from a superseded playback can't disturb the current one.
      audio.onended = () => {
        if (audioRef.current !== audio) return
        console.log('[TTS] audio ended naturally')
        stopSync()
        wordIndexRef.current = -1
        baseIndexRef.current = 0
        setIsPlaying(false)
        setIsPaused(false)
        onWordChange(-1)
        URL.revokeObjectURL(url)
      }

      audio.onerror = (e) => {
        if (audioRef.current !== audio) return
        console.error('[TTS] audio error:', e)
        stopSync()
        setError('Audio playback failed')
        setIsPlaying(false)
        setIsLoading(false)
        URL.revokeObjectURL(url)
      }

      // Also catch stall/suspend
      audio.onstalled = () => {
        console.warn('[TTS] audio stalled — network issue?')
      }

      setIsLoading(false)
      setIsPlaying(true)

      await audio.play()
      console.log(`[TTS] playback started, duration: ${audio.duration}s`)

      computeScale()
      startSync()
    } catch (err) {
      console.error('[TTS] play error:', err)
      setError(err.message)
      setIsLoading(false)
      setIsPlaying(false)
    }
  }, [stopSync, startSync, computeScale, onWordChange, releaseAudio])

  /* ══════════════════════════════════════════════
     PAUSE / RESUME / STOP
     ══════════════════════════════════════════════ */
  const pause = useCallback(() => {
    if (audioRef.current) audioRef.current.pause()
    stopSync()
    setIsPlaying(false)
    setIsPaused(true)
  }, [stopSync])

  const resume = useCallback(async () => {
    if (!audioRef.current || !isPaused) return
    try {
      await audioRef.current.play()
      startSync()
      setIsPlaying(true)
      setIsPaused(false)
    } catch (err) {
      setError(err.message)
    }
  }, [isPaused, startSync])

  const stop = useCallback(() => {
    stopSync()
    releaseAudio()
    timingsRef.current   = []
    wordIndexRef.current = -1
    baseIndexRef.current = 0
    scaleRef.current     = 1
    setIsPlaying(false)
    setIsPaused(false)
    setTotalDurationMs(0)
    onWordChange(-1)
  }, [stopSync, onWordChange, releaseAudio])

  /* ══════════════════════════════════════════════
     PLAY SINGLE WORD
     ══════════════════════════════════════════════ */
  const playWord = useCallback(async (word) => {
    try {
      const data   = await api.post('/tts/word', { word, voice: 'en-GB-SoniaNeural' })
      const binary = atob(data.audio_b64)
      const bytes  = new Uint8Array(binary.length)
      for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i)
      const blob  = new Blob([bytes], { type: 'audio/mpeg' })
      const a     = new Audio()
      a.src = URL.createObjectURL(blob)
      a.onended = () => URL.revokeObjectURL(a.src)
      a.play()
    } catch (err) {
      console.error('Word TTS error:', err)
    }
  }, [])

  const getCurrentWordIndex = useCallback(() => {
    if (wordIndexRef.current >= 0) {
      return baseIndexRef.current + wordIndexRef.current
    }

    // The rAF sync loop may not have ticked yet. Derive the position from the
    // audio clock instead, so callers never mistake "not tracked yet" for
    // "at the very beginning" and rewind the whole passage.
    const audio   = audioRef.current
    const timings = timingsRef.current
    if (!audio || !timings.length) return -1

    const scale = scaleRef.current > 0 ? scaleRef.current : 1
    const index = findWordIndex(timings, (audio.currentTime * 1000) / scale)
    return index >= 0 ? baseIndexRef.current + index : -1
  }, [findWordIndex])

  return {
    play, pause, resume, stop, playWord, getCurrentWordIndex,
    isPlaying, isPaused, isLoading, error, totalDurationMs,
  }
}