import { useCallback, useEffect, useRef, useState } from 'react'

import { api } from '../utils/api'

const VOICE = 'en-GB-SoniaNeural'

// Small first chunk so audio starts fast; later chunks are fetched while
// the current one plays. Char cap keeps every chunk under the backend's
// 1000-char TTS limit so nothing is silently cut off.
const FIRST_CHUNK_WORDS = 12
const CHUNK_WORDS       = 40
const MAX_CHUNK_WORDS   = 60
const MAX_CHUNK_CHARS   = 900
const PREFETCH_AHEAD    = 2
const MAX_CACHE         = 30
const SPEED_DEBOUNCE_MS = 350

/** Split words[startIndex..] into chunks, preferring sentence ends. */
function buildChunks(words, startIndex) {
  const chunks = []
  let i = startIndex
  while (i < words.length) {
    const target = chunks.length === 0 ? FIRST_CHUNK_WORDS : CHUNK_WORDS
    const start = i
    let chars = 0
    while (i < words.length) {
      chars += words[i].length + 1
      i++
      const count = i - start
      if (count >= MAX_CHUNK_WORDS || chars >= MAX_CHUNK_CHARS) break
      if (count >= target && /[.!?]["')\]]*$/.test(words[i - 1])) break
      if (count >= target * 1.5) break
    }
    chunks.push({ start, text: words.slice(start, i).join(' ') })
  }
  return chunks
}

function base64ToBlob(b64) {
  const binary = atob(b64)
  const bytes  = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i)
  return new Blob([bytes], { type: 'audio/mpeg' })
}

/**
 * @param onWordChange(globalIndex) — highlight callback (-1 = none)
 * @param onEnded() — fired only when the last chunk finishes naturally
 *                    (not on stop, error, or restart)
 */
export function useTTSPlayer(onWordChange, onEnded) {
  const [isPlaying, setIsPlaying]             = useState(false)
  const [isPaused, setIsPaused]               = useState(false)
  const [isLoading, setIsLoading]             = useState(false)
  const [error, setError]                     = useState(null)
  const [totalDurationMs, setTotalDurationMs] = useState(0)

  const audioRef       = useRef(null)
  const timingsRef     = useRef([])
  const timeoutRefs    = useRef([])
  const wordIndexRef   = useRef(-1)   // global index of highlighted word
  const cacheRef       = useRef(new Map())
  const sessionRef     = useRef(0)    // bumps on every start/stop to cancel stale work
  const wordsRef       = useRef([])
  const chunksRef      = useRef([])
  const chunkIdxRef    = useRef(0)
  const speedRef       = useRef(1.0)
  const pausesRef      = useRef(true)
  const activeRef      = useRef(false)
  const pausedRef      = useRef(false)
  const restartRef     = useRef(false) // speed changed while paused
  const speedTimerRef  = useRef(null)
  const onWordRef      = useRef(onWordChange)
  const playChunkRef   = useRef(null)   // lets onended recurse into playChunk

  const onEndedRef     = useRef(onEnded)

  useEffect(() => { onWordRef.current = onWordChange }, [onWordChange])
  useEffect(() => { onEndedRef.current = onEnded }, [onEnded])

  const setWord = useCallback((i) => {
    wordIndexRef.current = i
    onWordRef.current(i)
  }, [])

  /* ══════════════════════════════════════════════
     FETCH (cached, shared by play + prefetch)
     ══════════════════════════════════════════════ */
  const fetchChunk = useCallback((text, speed, phrasePauses) => {
    const key   = `${text}|${speed}|${phrasePauses}`
    const cache = cacheRef.current
    if (cache.has(key)) return cache.get(key)

    const promise = api.post('/tts/generate', {
      text, speed, voice: VOICE, phrase_pauses: phrasePauses,
    }).then(data => ({
      blob: base64ToBlob(data.audio_b64),
      word_timings: data.word_timings || [],
      duration_ms: data.duration_ms || 0,
    }))
    promise.catch(() => cache.delete(key))

    cache.set(key, promise)
    if (cache.size > MAX_CACHE) cache.delete(cache.keys().next().value)
    return promise
  }, [])

  const prefetchFrom = useCallback((fromChunk) => {
    const chunks = chunksRef.current
    for (let c = fromChunk; c < Math.min(chunks.length, fromChunk + PREFETCH_AHEAD); c++) {
      fetchChunk(chunks[c].text, speedRef.current, pausesRef.current).catch(() => {})
    }
  }, [fetchChunk])

  /* ══════════════════════════════════════════════
     SYNC — setTimeout chain anchored to play start
     ══════════════════════════════════════════════ */
  const clearSync = useCallback(() => {
    timeoutRefs.current.forEach(clearTimeout)
    timeoutRefs.current = []
  }, [])

  const scheduleSync = useCallback((timings, audioStartTime, baseIndex) => {
    clearSync()
    timings.forEach((t, i) => {
      const delay = t.start_ms - (Date.now() - audioStartTime)
      if (delay >= 0) {
        const id = setTimeout(() => setWord(baseIndex + i), delay)
        timeoutRefs.current.push(id)
      }
    })
  }, [clearSync, setWord])

  /** Highlight the word already under the playhead (used on resume). */
  const syncToCurrentTime = useCallback((baseIndex) => {
    const audio = audioRef.current
    if (!audio) return
    const ms = audio.currentTime * 1000
    const timings = timingsRef.current
    let idx = -1
    for (let i = 0; i < timings.length && timings[i].start_ms <= ms; i++) idx = i
    if (idx >= 0) setWord(baseIndex + idx)
  }, [setWord])

  const releaseAudio = useCallback(() => {
    clearSync()
    const audio = audioRef.current
    if (audio) {
      audio.onended = null
      audio.onerror = null
      audio.pause()
      if (audio.src) URL.revokeObjectURL(audio.src)
      audioRef.current = null
    }
  }, [clearSync])

  const finish = useCallback(() => {
    releaseAudio()
    activeRef.current  = false
    pausedRef.current  = false
    restartRef.current = false
    timingsRef.current = []
    setIsPlaying(false)
    setIsPaused(false)
    setIsLoading(false)
    setWord(-1)
  }, [releaseAudio, setWord])

  /* ══════════════════════════════════════════════
     CHUNK PLAYBACK
     ══════════════════════════════════════════════ */
  const playChunk = useCallback(async (session, chunkIdx) => {
    const chunks = chunksRef.current
    if (chunkIdx >= chunks.length) {
      finish()
      onEndedRef.current?.()
      return
    }
    const chunk = chunks[chunkIdx]
    chunkIdxRef.current = chunkIdx
    prefetchFrom(chunkIdx + 1)

    const data = await fetchChunk(chunk.text, speedRef.current, pausesRef.current)
    if (sessionRef.current !== session) return

    releaseAudio()
    const url   = URL.createObjectURL(data.blob)
    const audio = new Audio(url)
    audioRef.current   = audio
    timingsRef.current = data.word_timings

    if (chunkIdx === 0) {
      const chunkWords = chunk.text.split(/\s+/).length
      const totalLeft  = wordsRef.current.length - chunks[0].start
      setTotalDurationMs(Math.round((data.duration_ms / chunkWords) * totalLeft))
    }

    audio.onended = () => {
      if (sessionRef.current !== session) return
      playChunkRef.current(session, chunkIdx + 1).catch(err => {
        if (sessionRef.current !== session) return
        setError(err.message)
        finish()
      })
    }
    audio.onerror = () => {
      if (sessionRef.current !== session) return
      setError('Audio playback failed')
      finish()
    }

    // Paused while this chunk was loading — stay paused; resume() plays it.
    if (pausedRef.current) {
      setIsLoading(false)
      return
    }

    await audio.play()
    if (sessionRef.current !== session) return
    // Anchor AFTER play() resolves — this is when sound actually starts.
    const audioStartTime = Date.now() - audio.currentTime * 1000

    setIsLoading(false)
    setIsPlaying(true)
    scheduleSync(data.word_timings, audioStartTime, chunk.start)
  }, [fetchChunk, prefetchFrom, releaseAudio, scheduleSync, finish])

  useEffect(() => { playChunkRef.current = playChunk }, [playChunk])

  const startFrom = useCallback(async (startIndex) => {
    const session = ++sessionRef.current
    releaseAudio()
    activeRef.current  = true
    pausedRef.current  = false
    restartRef.current = false
    chunksRef.current  = buildChunks(wordsRef.current, startIndex)
    setError(null)
    setIsPaused(false)
    setIsLoading(true)

    try {
      await playChunk(session, 0)
    } catch (err) {
      if (sessionRef.current !== session) return
      console.error('[TTS] play error:', err)
      setError(err.message)
      finish()
    }
  }, [releaseAudio, playChunk, finish])

  /* ══════════════════════════════════════════════
     PUBLIC API
     ══════════════════════════════════════════════ */
  const play = useCallback((words, speed = 1.0, phrasePauses = true, startIndex = 0) => {
    wordsRef.current  = words
    speedRef.current  = speed
    pausesRef.current = phrasePauses
    setWord(-1)
    return startFrom(startIndex)
  }, [startFrom, setWord])

  /** Warm the cache for the first chunks so Play starts instantly. */
  const prefetch = useCallback((words, speed = 1.0, phrasePauses = true) => {
    if (!words.length) return
    buildChunks(words, 0).slice(0, PREFETCH_AHEAD).forEach(c => {
      fetchChunk(c.text, speed, phrasePauses).catch(() => {})
    })
  }, [fetchChunk])

  const pause = useCallback(() => {
    if (audioRef.current) audioRef.current.pause()
    clearSync()
    pausedRef.current = true
    setIsPlaying(false)
    setIsPaused(true)
  }, [clearSync])

  const resume = useCallback(async () => {
    if (!pausedRef.current) return
    if (restartRef.current || !audioRef.current) {
      const from = Math.max(wordIndexRef.current, chunksRef.current[chunkIdxRef.current]?.start ?? 0)
      return startFrom(from)
    }
    const session = sessionRef.current
    const chunk   = chunksRef.current[chunkIdxRef.current]
    try {
      await audioRef.current.play()
      if (sessionRef.current !== session) return
      const audio = audioRef.current
      pausedRef.current = false
      syncToCurrentTime(chunk.start)
      scheduleSync(timingsRef.current, Date.now() - audio.currentTime * 1000, chunk.start)
      setIsPlaying(true)
      setIsPaused(false)
    } catch (err) {
      setError(err.message)
    }
  }, [startFrom, scheduleSync, syncToCurrentTime])

  const stop = useCallback(() => {
    sessionRef.current++
    clearTimeout(speedTimerRef.current)
    finish()
    setTotalDurationMs(0)
  }, [finish])

  /**
   * Mid-session speed change: stop current audio, regenerate from the
   * current word at the new speed, and re-anchor sync. Debounced so a
   * slider drag triggers one regeneration, not one per step.
   */
  const changeSpeed = useCallback((newSpeed) => {
    speedRef.current = newSpeed
    clearTimeout(speedTimerRef.current)
    if (!activeRef.current) return

    if (pausedRef.current) {
      restartRef.current = true
      return
    }

    speedTimerRef.current = setTimeout(() => {
      if (!activeRef.current || pausedRef.current) return
      const chunkStart = chunksRef.current[chunkIdxRef.current]?.start ?? 0
      startFrom(Math.max(wordIndexRef.current, chunkStart))
    }, SPEED_DEBOUNCE_MS)
  }, [startFrom])

  /* ══════════════════════════════════════════════
     PLAY SINGLE WORD
     ══════════════════════════════════════════════ */
  const playWord = useCallback(async (word) => {
    try {
      const data = await api.post('/tts/word', { word, voice: VOICE })
      const a    = new Audio(URL.createObjectURL(base64ToBlob(data.audio_b64)))
      a.onended = () => URL.revokeObjectURL(a.src)
      a.play()
    } catch (err) {
      console.error('Word TTS error:', err)
    }
  }, [])

  const getCurrentWordIndex = useCallback(() => wordIndexRef.current, [])

  // Stop audio and pending highlight timeouts when the page unmounts.
  useEffect(() => () => {
    sessionRef.current++
    clearTimeout(speedTimerRef.current)
    timeoutRefs.current.forEach(clearTimeout)
    if (audioRef.current) audioRef.current.pause()
  }, [])

  return {
    play, pause, resume, stop, playWord, prefetch, changeSpeed, getCurrentWordIndex,
    isPlaying, isPaused, isLoading, error, totalDurationMs,
  }
}
