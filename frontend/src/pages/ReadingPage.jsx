import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import ComplexityBadge from '../components/ComplexityBadge.jsx'
import DefinitionPanel from '../components/DefinitionPanel.jsx'
import ReadingProgress from '../components/ReadingProgress.jsx'
import { Toast } from '../components/Toast.jsx'
import WordDisplay from '../components/WordDisplay.jsx'
import { api } from '../utils/api'
import { usePrefs } from '../context/PreferencesContext'
import { useTTSPlayer } from '../hooks/useTTSPlayer'
import { useTTSPrefetch } from '../hooks/useTTSPrefetch'
import { useToast } from '../hooks/useToast.js'

const SAMPLE_TEXT =
  'Solar cells convert sunlight into electrical energy using semiconductor materials. ' +
  'Renewable technology helps communities reduce pollution. ' +
  'These innovations make clean power available to people around the world.'

function normalizeWord(word) {
  return String(word || '').toLowerCase().replace(/[^\w']/g, '')
}
export default function ReadingPage() {
  const { prefs } = usePrefs()
  const { toast, showToast, hideToast } = useToast()

  const [text, setText]                   = useState('')
  const [words, setWords]                 = useState([])
  const [activeIndex, setActiveIndex]     = useState(-1)
  const [classifiedWords, setClassified]  = useState({})
  const [complexity, setComplexity]       = useState(null)
  const [simplified, setSimplified]       = useState(null)
  const [originalText, setOriginalText]   = useState('')
  const [selectedWord, setSelectedWord]   = useState(null)
  const [speed, setSpeed]                 = useState(1.0)
  const [isSimplifying, setIsSimplifying] = useState(false)
  const [isUploading, setIsUploading]     = useState(false)
  const [distractionFree, setDistraction] = useState(false)
  const [classificationReady, setClassificationReady] = useState(false)

  // Store the raw word_timings so we can use backend's word list
  const [wordTimings, setWordTimings]     = useState([])
  const classifyRequestRef = useRef(0)
  const { prefetch, getCached, clearCache } = useTTSPrefetch()

  const {
    play, pause, resume, stop, playWord, getCurrentWordIndex,
    isPlaying, isPaused, isLoading,
    error: ttsError, totalDurationMs,
  } = useTTSPlayer(useCallback(i => setActiveIndex(i), []))

  useEffect(() => {
    if (ttsError) showToast(`Could not play audio: ${ttsError}`, 'error')
  }, [ttsError, showToast])

    useEffect(() => {
    function handleKeyDown(e) {
      if (e.key === 'Escape' && distractionFree) {
        setDistraction(false)
      }
    }

    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [distractionFree])

  useEffect(() => {
    if (text.trim()) prefetch(text.trim(), speed, prefs.phrasePauses)
  }, [text, speed, prefs.phrasePauses, prefetch])

  /* ── FIX #3: When TTS data arrives, rebuild words from timings ── */
  const displayWords = useMemo(() => {
    // If we have word timings, use THOSE words so indices match exactly
    if (wordTimings.length > 0) {
      return wordTimings.map(t => t.word)
    }
    // Fallback to split
    return words
  }, [wordTimings, words])

  /* ── Load text ── */
  function loadText(raw, options = {}) {
  const cleaned = raw.trim()
  setText(raw)
  setActiveIndex(-1)
  setWordTimings([])

  if (!cleaned) {
    setWords([])
    setClassified({})
    setClassificationReady(false)
    setComplexity(null)
    setSimplified(null)
    setOriginalText('')
    clearCache()
    return
  }

  const nextWords = cleaned.split(/\s+/).filter(w => w.length > 0)
  setWords(nextWords)
  setOriginalText(options.originalText ?? cleaned)
  setSimplified(options.simplified ?? null)
  setClassified({})
  setClassificationReady(false)

  const classifyRequestId = classifyRequestRef.current + 1
  classifyRequestRef.current = classifyRequestId

    api
      .post('/reading/complexity', { text: cleaned })
      .then(setComplexity)
      .catch(() => showToast('Could not calculate complexity.', 'warning'))

    api
  .post('/classify', { words: nextWords })
  .then(data => {
    if (classifyRequestRef.current !== classifyRequestId) return

    const classified = {}

    ;(data.results || []).forEach(item => {
      const cleanWord = normalizeWord(item.word)
      if (cleanWord) classified[cleanWord] = item.label
    })

    setClassified(classified)
    setClassificationReady(true)
  })
  .catch(error => {
    if (classifyRequestRef.current !== classifyRequestId) return

    console.error('Could not classify hard words:', error)
    setClassified({})
    setClassificationReady(true)
  })

  }

  /* ── File upload ── */
  async function handleFileUpload(event) {
    const file = event.target.files[0]
    if (!file) return
    if (file.size > 10_000_000) {
      showToast('File too large. Maximum 10 MB.', 'error')
      return
    }
    const allowed = ['image/jpeg', 'image/png', 'application/pdf']
    if (!allowed.includes(file.type)) {
      showToast('Please upload a JPG, PNG or PDF.', 'error')
      return
    }
    setIsUploading(true)
    try {
      const formData = new FormData()
      formData.append('file', file)
      const endpoint = file.type === 'application/pdf' ? '/ocr/pdf' : '/ocr/image'
      const data = await api.postForm(endpoint, formData)
      loadText(data.text)
      showToast(`Extracted ${data.word_count} words.`, 'success')
    } catch (err) {
      showToast(err.message, 'error')
    } finally {
      setIsUploading(false)
      event.target.value = ''
    }
  }

  /* ── Simplify ── */
  async function handleSimplify() {
    if (!text.trim()) return
    const sourceText = text.trim()
    setIsSimplifying(true)
    try {
      const data = await api.post('/reading/simplify', { text: sourceText })
      loadText(data.simplified_text, {
        originalText: originalText || sourceText,
        simplified: data,
      })
      showToast('Text simplified.', 'success')
    } catch (error) {
      showToast(error?.message || 'Simplification unavailable.', 'warning')
    } finally {
      setIsSimplifying(false)
    }
  }

  function handleRestore() {
    loadText(originalText)
    showToast('Original text restored.', 'info')
  }

  function handleWordClick(word) {
    const clean = normalizeWord(word)
    if (!clean) return
    setSelectedWord(clean)
    playWord(clean)
  }

  /* ── Play — also capture word_timings for display sync ── */
  async function handlePlay() {
    const trimmed = text.trim()
    const cached = getCached(trimmed, speed, prefs.phrasePauses)

    // Capture word timings for display word sync
    if (cached?.word_timings) {
      setWordTimings(cached.word_timings)
      console.log(`[ReadingPage] Using ${cached.word_timings.length} cached timing words`)
    } else {
      // If not cached, we'll get timings from the API response
      // The play function handles this internally, but we also need them
      try {
        const data = await api.post('/tts/generate', {
          text: trimmed,
          speed,
          voice: 'en-GB-SoniaNeural',
          phrase_pauses: prefs.phrasePauses,
        })

        const binary = atob(data.audio_b64)
        const bytes  = new Uint8Array(binary.length)
        for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i)
        const blob = new Blob([bytes], { type: 'audio/mpeg' })

        const prefetchedData = {
          blob,
          word_timings: data.word_timings,
          duration_ms: data.duration_ms || 0,
        }

        if (data.word_timings) {
          setWordTimings(data.word_timings)
          console.log(`[ReadingPage] Using ${data.word_timings.length} fresh timing words`)
        }

        play(trimmed, speed, prefs.phrasePauses, prefetchedData)
        return
      } catch (err) {
        showToast(err.message, 'error')
        return
      }
    }

    play(trimmed, speed, prefs.phrasePauses, cached)
  }

  function handlePauseResume() {
    if (isPaused) resume()
    else pause()
  }

  function handleStop() {
    stop()
    setWordTimings([])
    setActiveIndex(-1)
  }

  async function handleSpeedChange(nextSpeed) {
    setSpeed(nextSpeed)

    if (!isPlaying) return

    // Only resume from a position we actually know. Falling back to 0 here
    // meant an untracked position replayed the entire passage from the top —
    // which reads as "it started over" a few lines in.
    const currentIndex = Math.max(getCurrentWordIndex(), activeIndex)
    if (currentIndex < 0) return

    const sourceWords = showWords.length > 0 ? showWords : words
    const remainingText = sourceWords.slice(currentIndex).join(' ')

    if (!remainingText.trim()) return

    stop()
    setWordTimings([])
    await play(remainingText, nextSpeed, prefs.phrasePauses, null, currentIndex)
  }

  const hasText = displayWords.length > 0 || words.length > 0
  const showWords = displayWords.length > 0 ? displayWords : words
  const isAudioActive = isPlaying || isPaused

  const classifierHardWordPct = useMemo(() => {
    if (!classificationReady || words.length === 0) return null

    const hardCount = words.filter(word => {
      const clean = normalizeWord(word)
      return classifiedWords[clean] === 'Hard'
    }).length

    return Math.round((hardCount / words.length) * 100)
  }, [classificationReady, words, classifiedWords])

  const displayedComplexity = useMemo(() => {
    if (!complexity || classifierHardWordPct === null) return complexity
    return { ...complexity, hard_word_pct: classifierHardWordPct }
  }, [complexity, classifierHardWordPct])

  return (
    <main
      className={
        distractionFree
          ? 'min-h-screen p-4 pb-28'
          : 'min-h-screen bg-gray-50/70 px-4 py-8 pb-28 dark:bg-[#1E1E1E] sm:px-6'
      }
    >
      {/* ════════════ NORMAL MODE ════════════ */}
      {!distractionFree && (
        <section className="mx-auto max-w-6xl">
          {/* Page header */}
          <div className="mb-8">
            <p className="text-sm font-semibold uppercase tracking-wide text-blue-600 dark:text-blue-300">
              Reading workspace
            </p>
            <h1 className="mt-2 text-3xl font-bold tracking-tight text-gray-950 dark:text-white sm:text-4xl">
              Read, listen, and understand.
            </h1>
            <p className="mt-3 max-w-2xl text-base leading-relaxed text-gray-600 dark:text-gray-300">
              Upload your notes, simplify hard passages, listen with word-by-word
              highlighting, and tap any word for its meaning.
            </p>
          </div>

          {/* ════════════ EMPTY STATE ════════════ */}
          {!hasText && (
            <div
              className="mx-auto max-w-2xl rounded-3xl border-2 border-dashed
                          border-gray-300 bg-white p-10 text-center shadow-sm
                          dark:border-gray-700 dark:bg-[#2A2A2A]"
            >
              <p className="text-lg font-semibold text-gray-900 dark:text-white">
                Get started
              </p>
              <p className="mx-auto mt-2 max-w-md text-sm text-gray-500 dark:text-gray-400">
                Choose how you'd like to add your reading material.
              </p>

              <div className="mt-8 grid gap-4 sm:grid-cols-3">
                <label
                  className="group flex cursor-pointer flex-col items-center gap-3
                              rounded-2xl border border-gray-200 bg-gray-50 p-6
                              transition-colors hover:border-blue-300 hover:bg-blue-50
                              dark:border-gray-700 dark:bg-[#333] dark:hover:border-blue-600"
                >
                  <span className="text-4xl" aria-hidden="true">📄</span>
                  <span className="text-sm font-semibold text-gray-700 dark:text-gray-200">
                    {isUploading ? 'Uploading…' : 'Upload file'}
                  </span>
                  <span className="text-xs text-gray-400">PDF, JPG or PNG</span>
                  <input
                    type="file"
                    accept="image/*,.pdf"
                    onChange={handleFileUpload}
                    className="sr-only"
                  />
                </label>

                <label
                  className="group flex cursor-pointer flex-col items-center gap-3
                              rounded-2xl border border-gray-200 bg-gray-50 p-6
                              transition-colors hover:border-blue-300 hover:bg-blue-50
                              dark:border-gray-700 dark:bg-[#333] dark:hover:border-blue-600"
                >
                  <span className="text-4xl" aria-hidden="true">📷</span>
                  <span className="text-sm font-semibold text-gray-700 dark:text-gray-200">
                    Scan notes
                  </span>
                  <span className="text-xs text-gray-400">Take or upload a photo</span>
                  <input
                    type="file"
                    accept="image/*"
                    capture="environment"
                    onChange={handleFileUpload}
                    className="sr-only"
                  />
                </label>

                <button
                  type="button"
                  onClick={() => loadText(SAMPLE_TEXT)}
                  className="group flex flex-col items-center gap-3
                              rounded-2xl border border-gray-200 bg-gray-50 p-6
                              transition-colors hover:border-blue-300 hover:bg-blue-50
                              dark:border-gray-700 dark:bg-[#333] dark:hover:border-blue-600"
                >
                  <span className="text-4xl" aria-hidden="true">✨</span>
                  <span className="text-sm font-semibold text-gray-700 dark:text-gray-200">
                    Try sample
                  </span>
                  <span className="text-xs text-gray-400">See how it works</span>
                </button>
              </div>

              <div className="mt-6">
                <textarea
                  className="min-h-32 w-full resize-y rounded-2xl border border-gray-200
                              bg-gray-50 p-4 text-sm shadow-inner
                              focus:border-blue-400 focus:outline-none
                              dark:border-gray-700 dark:bg-[#333]"
                  placeholder="Or paste your text here…"
                  value={text}
                  onChange={e => loadText(e.target.value)}
                  aria-label="Paste your reading text"
                />
              </div>
            </div>
          )}

          {/* ════════════ READING LAYOUT ════════════ */}
          {hasText && (
            <div className="grid gap-6 lg:grid-cols-[280px_1fr]">
              {/* Left sidebar */}
              <aside className="space-y-4 lg:sticky lg:top-24 lg:self-start">
                <div
                  className="rounded-2xl border border-gray-200 bg-white p-4 shadow-sm
                              dark:border-gray-800 dark:bg-[#2A2A2A]"
                >
                  <div className="mb-3 flex flex-wrap gap-2">
                    <label
                      className="cursor-pointer rounded-lg bg-blue-600 px-3 py-2 text-xs
                                  font-semibold text-white hover:bg-blue-700"
                    >
                      {isUploading ? 'Uploading…' : 'Upload'}
                      <input
                        type="file"
                        accept="image/*,.pdf"
                        onChange={handleFileUpload}
                        className="sr-only"
                      />
                    </label>
                    <button
                      type="button"
                      onClick={() => loadText(SAMPLE_TEXT)}
                      className="rounded-lg border border-gray-200 px-3 py-2 text-xs
                                  font-semibold text-gray-600 hover:bg-gray-50
                                  dark:border-gray-700 dark:text-gray-300"
                    >
                      Sample
                    </button>
                  </div>

                  <label
                    className="text-xs font-semibold text-gray-500 dark:text-gray-400"
                    htmlFor="sidebar-text"
                  >
                    Edit text
                  </label>
                  <textarea
                    id="sidebar-text"
                    className="surface mt-1 min-h-24 w-full resize-y rounded-xl border
                                border-gray-200 bg-white p-3 text-xs shadow-inner
                                focus:border-blue-400 focus:outline-none
                                dark:border-gray-700"
                    value={text}
                    onChange={e => loadText(e.target.value)}
                  />

                  <div className="mt-3 flex flex-wrap gap-2">
                    <button
                      type="button"
                      onClick={handleSimplify}
                      disabled={!hasText || isSimplifying}
                      className="rounded-lg bg-purple-600 px-3 py-2 text-xs font-semibold
                                  text-white hover:bg-purple-700 disabled:opacity-50"
                    >
                      {isSimplifying ? 'Simplifying…' : '✨ Simplify'}
                    </button>
                    {simplified && (
                      <button
                        type="button"
                        onClick={handleRestore}
                        className="rounded-lg border border-gray-200 px-3 py-2 text-xs
                                    font-semibold text-gray-600 hover:bg-gray-50
                                    dark:border-gray-700 dark:text-gray-300"
                      >
                        Restore
                      </button>
                    )}
                  </div>
                </div>

                {displayedComplexity && <ComplexityBadge complexity={displayedComplexity} />}

                {simplified && (
                  <div
                    className="rounded-xl border border-purple-100 bg-purple-50 p-3
                                text-xs text-purple-800
                                dark:border-purple-900 dark:bg-purple-950/40 dark:text-purple-200"
                  >
                    Hard words reduced from {simplified.original_hard_word_pct}%
                    to {simplified.simplified_hard_word_pct}%.
                  </div>
                )}
              </aside>

              {/* Right: reading content */}
              <section className="space-y-4" aria-label="Reading content">
                <WordDisplay
                  words={showWords}
                  activeIndex={activeIndex}
                  classifiedWords={classifiedWords}
                  onWordClick={handleWordClick}
                  focusRulerEnabled={prefs.focusRuler}
                />

                <ReadingProgress
                  activeIndex={activeIndex}
                  totalWords={showWords.length}
                  durationMs={totalDurationMs}
                />
              </section>
            </div>
          )}
        </section>
      )}

      {/* ════════════ DISTRACTION-FREE MODE ════════════ */}
      {distractionFree && hasText && (
        <section className="distraction-free mx-auto max-w-3xl space-y-4">
          <WordDisplay
            words={showWords}
            activeIndex={activeIndex}
            classifiedWords={classifiedWords}
            onWordClick={handleWordClick}
            focusRulerEnabled={prefs.focusRuler}
          />
          <ReadingProgress
            activeIndex={activeIndex}
            totalWords={showWords.length}
            durationMs={totalDurationMs}
          />
        </section>
      )}

      {/* ═══════════════════════════════════════════════════
          FIX #2: STICKY PLAYBACK TOOLBAR — always visible
          ═══════════════════════════════════════════════════ */}
      {hasText && (
        <div
          className="fixed bottom-0 left-0 right-0 z-50
                      border-t border-gray-200 bg-white/95 backdrop-blur-sm
                      dark:border-gray-700 dark:bg-[#1E1E1E]/95"
          role="toolbar"
          aria-label="Playback controls"
        >
          <div className="mx-auto flex max-w-4xl flex-wrap items-center gap-3 px-4 py-3 sm:px-6">
            {/* PRIMARY: Play / Pause */}
            {!isPlaying && !isPaused ? (
              <button
                type="button"
                onClick={handlePlay}
                disabled={isLoading}
                className="rounded-xl bg-blue-600 px-6 py-2.5 text-sm font-bold
                            text-white shadow-md shadow-blue-200 hover:bg-blue-700
                            disabled:opacity-50 dark:shadow-none"
              >
                {isLoading ? 'Loading…' : '▶  Play'}
              </button>
            ) : (
              <button
                type="button"
                onClick={handlePauseResume}
                className={`rounded-xl px-6 py-2.5 text-sm font-bold text-white shadow-md
                  ${isPaused
                    ? 'bg-green-600 shadow-green-200 hover:bg-green-700 dark:shadow-none'
                    : 'bg-yellow-500 shadow-yellow-200 hover:bg-yellow-600 dark:shadow-none'
                  }`}
              >
                {isPaused ? '▶  Resume' : '⏸  Pause'}
              </button>
            )}

            {/* SECONDARY: Stop */}
            <button
              type="button"
              onClick={handleStop}
              className="rounded-xl border border-gray-200 px-4 py-2 text-xs
                          font-semibold text-gray-600 hover:bg-gray-50
                          dark:border-gray-700 dark:text-gray-300 dark:hover:bg-gray-800"
            >
              ⏹ Stop
            </button>

            {/* Focus toggle */}
            <button
              type="button"
              onClick={() => setDistraction(!distractionFree)}
              className="rounded-xl border border-gray-200 px-4 py-2 text-xs
                          font-semibold text-gray-600 hover:bg-gray-50
                          dark:border-gray-700 dark:text-gray-300 dark:hover:bg-gray-800"
            >
              {distractionFree ? '← Exit Focus' : '🎯 Focus'}
            </button>

            {/* Current word indicator */}
            {isAudioActive && activeIndex >= 0 && activeIndex < showWords.length && (
              <div className="current-word-display ml-2 hidden sm:flex" aria-live="polite">
                {showWords[activeIndex]?.replace(/[^a-zA-Z']/g, '') || ''}
              </div>
            )}

            {/* Speed — pushed right */}
            <div className="ml-auto flex items-center gap-2">
              <label htmlFor="speed-range" className="text-xs font-semibold text-gray-400">
                Speed
              </label>
              <input
                id="speed-range"
                type="range"
                min="0.5"
                max="2.0"
                step="0.1"
                value={speed}
                onChange={e => handleSpeedChange(Number(e.target.value))}
                className="w-20 accent-blue-600 sm:w-24"
                aria-label="Reading speed"
              />
              <span className="w-8 text-xs font-bold text-gray-600 dark:text-gray-300">
                {speed}x
              </span>
            </div>
          </div>

          {/* Mini progress bar in the toolbar */}
          {isAudioActive && (
            <div className="reading-progress-track h-1 rounded-none">
              <div
                className="reading-progress-fill rounded-none"
                style={{
                  width: `${showWords.length > 0
                    ? Math.round(((activeIndex + 1) / showWords.length) * 100)
                    : 0}%`
                }}
              />
            </div>
          )}
        </div>
      )}

      {/* Definition panel */}
      <DefinitionPanel word={selectedWord} onClose={() => setSelectedWord(null)} />

      {toast && <Toast message={toast.message} type={toast.type} onClose={hideToast} />}
    </main>
  )
}