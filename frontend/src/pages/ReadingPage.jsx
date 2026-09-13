import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import ComplexityBadge from '../components/ComplexityBadge.jsx'
import DefinitionPanel from '../components/DefinitionPanel.jsx'
import DocumentNavigator from '../components/DocumentNavigator.jsx'
import PageNav from '../components/PageNav.jsx'
import ReadingProgress from '../components/ReadingProgress.jsx'
import { Toast } from '../components/Toast.jsx'
import WordDisplay from '../components/WordDisplay.jsx'
import { api } from '../utils/api'
import {
  normalizeWord, pageLayout, pagesFromPdf, pagesFromText, searchTerms, splitWords,
  syllableKey,
} from '../utils/document'
import { usePrefs } from '../context/PreferencesContext'
import { useReadingSession } from '../hooks/useReadingSession'
import { useTTSPlayer } from '../hooks/useTTSPlayer'
import { useToast } from '../hooks/useToast.js'

const SAMPLE_TEXT =
  'Solar cells convert sunlight into electrical energy using semiconductor materials. ' +
  'Renewable technology helps communities reduce pollution. ' +
  'These innovations make clean power available to people around the world.'

const CLASSIFY_BATCH = 5000 // /classify max_length (backend/routers/classify.py)

export default function ReadingPage() {
  const { prefs } = usePrefs()
  const { toast, showToast, hideToast } = useToast()

  // Document: pages of { number, text }; text holds paragraphs split by "\n\n".
  const [pages, setPages]                 = useState([])
  const [pageIndex, setPageIndex]         = useState(0)
  const [originals, setOriginals]         = useState({})  // pageIndex → text before Simplify
  const [simplifiedInfo, setSimplifiedInfo] = useState({}) // pageIndex → simplify response
  const [query, setQuery]                 = useState('')

  const [activeIndex, setActiveIndex]     = useState(-1)
  const [classifiedWords, setClassified]  = useState({})
  const [complexity, setComplexity]       = useState(null)
  const [selectedWord, setSelectedWord]   = useState(null)
  const [speed, setSpeed]                 = useState(1.0)
  const [isSimplifying, setIsSimplifying] = useState(false)
  const [isUploading, setIsUploading]     = useState(false)
  const [distractionFree, setDistraction] = useState(false)
  const [syllableView, setSyllableView]   = useState(false)
  const [syllableMap, setSyllableMap]     = useState({})  // cleaned word → syllables

  const [sourceType, setSourceType] = useState('paste')
  // The pages read in the current session, and how many words came before the
  // current one: useReadingSession follows a single running word index, while
  // activeIndex restarts at 0 on every page.
  const [sessionPages, setSessionPages]   = useState([])
  const [sessionOffset, setSessionOffset] = useState(0)
  // True while reading is paused only because a word's definition is open.
  const pausedForDefinitionRef = useRef(false)

  // Classification: words already sent to /classify for this document.
  const docVersionRef   = useRef(0)
  const classifyTriedRef = useRef(new Set())

  const currentPage = pages[pageIndex]
  const currentText = currentPage?.text ?? ''
  const { words, paragraphStarts } = useMemo(() => pageLayout(currentText), [currentText])

  const {
    play, pause, resume, stop, playWord, prefetch, changeSpeed,
    isPlaying, isPaused, isLoading,
    error: ttsError, totalDurationMs,
  } = useTTSPlayer(useCallback(i => setActiveIndex(i), []), handleReadingEnded)

  const isAudioActive = isPlaying || isPaused
  const hasText = pages.some(p => p.text.trim())

  /* ── Session reporting (F37) — everything read since Play was pressed ── */
  const sessionWords = useMemo(
    () => sessionPages.flatMap(i => splitWords(pages[i]?.text ?? '')),
    [sessionPages, pages],
  )
  const sessionHardWords = useMemo(
    () => sessionWords.filter(w => classifiedWords[normalizeWord(w)] === 'Hard').length,
    [sessionWords, classifiedWords],
  )

  const { recordReplay, end: endSession } = useReadingSession({
    isPlaying, isPaused,
    // Auto-advancing to the next page stops one audio and starts the next in
    // the same tick; isLoading covers that gap so it isn't read as the session
    // ending. A speed change is covered the same way.
    isPreparing: isLoading,
    activeIndex: activeIndex >= 0 ? sessionOffset + activeIndex : -1,
    totalWords: sessionWords.length,
    hardWordCount: sessionHardWords,
    sourceType,
    simplified: sessionPages.some(i => simplifiedInfo[i]),
    complexityScore: complexity?.flesch_kincaid_grade ?? null,
  })

  const resetSession = useCallback(() => {
    setSessionPages([])
    setSessionOffset(0)
  }, [])

  useEffect(() => {
    if (ttsError) showToast(`Could not play audio: ${ttsError}`, 'error')
  }, [ttsError, showToast])

  // Warm the first TTS chunks once typing settles so Play starts instantly.
  useEffect(() => {
    if (!words.length || isPlaying || isPaused) return
    const id = setTimeout(() => prefetch(words, speed, prefs.phrasePauses), 600)
    return () => clearTimeout(id)
  }, [words, speed, prefs.phrasePauses, prefetch, isPlaying, isPaused])

  // While a page plays, warm the next page so auto-advance has no gap.
  useEffect(() => {
    if (!isPlaying || !pages[pageIndex + 1]) return
    prefetch(splitWords(pages[pageIndex + 1].text), speed, prefs.phrasePauses)
  }, [isPlaying, pageIndex, pages, speed, prefs.phrasePauses, prefetch])

  /* ── Complexity of the current page — not shown to the reader (a difficulty
     score can be discouraging); kept only for the session log / analytics ── */
  useEffect(() => {
    const text = currentText.trim()
    if (!text) return
    let cancelled = false
    const id = setTimeout(() => {
      api.post('/reading/complexity', { text })
        .then(data => { if (!cancelled) setComplexity(data) })
        .catch(() => {})
    }, 500)
    return () => { cancelled = true; clearTimeout(id) }
  }, [currentText])

  /* ── Syllable breakdowns for the current page (only while the view is on) ── */
  useEffect(() => {
    if (!syllableView || !words.length) return
    const missing = [...new Set(words.map(syllableKey).filter(Boolean))]
      .filter(w => !(w in syllableMap))
    if (!missing.length) return
    let cancelled = false
    api.post('/reading/syllabify', { words: missing })
      .then(data => { if (!cancelled) setSyllableMap(prev => ({ ...prev, ...data.results })) })
      .catch(() => {
        if (!cancelled) showToast('Could not load syllable breakdown.', 'warning')
      })
    return () => { cancelled = true }
  }, [syllableView, words, syllableMap, showToast])

  /* ── Classify words not yet labelled, across all pages (debounced) ── */
  useEffect(() => {
    const version = docVersionRef.current
    const id = setTimeout(() => {
      const tried = classifyTriedRef.current
      // Labels are keyed by normalized word, so classify each unique word once.
      const missing = [...new Set(
        pages.flatMap(p => splitWords(p.text)).map(normalizeWord).filter(Boolean),
      )].filter(w => !tried.has(w))
      if (!missing.length) return
      missing.forEach(w => tried.add(w))

      // /classify rejects > 5000 words (422), so long documents go in batches.
      const batches = []
      for (let i = 0; i < missing.length; i += CLASSIFY_BATCH) {
        batches.push(missing.slice(i, i + CLASSIFY_BATCH))
      }
      Promise.all(batches.map(batch => api.post('/classify', { words: batch })))
        .then(responses => {
          if (docVersionRef.current !== version) return
          const labels = {}
          responses.flatMap(data => data.results || []).forEach(item => {
            const clean = normalizeWord(item.word)
            if (clean) labels[clean] = item.label
          })
          setClassified(prev => ({ ...prev, ...labels }))
        })
        .catch(error => console.error('Could not classify hard words:', error))
    }, 500)
    return () => clearTimeout(id)
  }, [pages])

  /* ── Load a new document ── */
  function loadDocument(nextPages, nextSourceType) {
    if (isPlaying || isPaused || isLoading) handleStop()
    docVersionRef.current += 1
    classifyTriedRef.current = new Set()
    setClassified({})
    setPages(nextPages)
    setPageIndex(0)
    setOriginals({})
    setSimplifiedInfo({})
    setQuery('')
    setActiveIndex(-1)
    setSourceType(nextSourceType)
  }

  /* ── Edit the current page's text ── */
  function updatePageText(index, text) {
    // New text invalidates the playing audio's word indices — stop it.
    if (isPlaying || isPaused || isLoading) handleStop()
    setPages(prev => prev.map((p, i) => (i === index ? { ...p, text } : p)))
    setActiveIndex(-1)
  }

  function goToPage(index) {
    if (index < 0 || index >= pages.length || index === pageIndex) return
    if (isPlaying || isPaused || isLoading) handleStop()
    setPageIndex(index)
    setActiveIndex(-1)
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
      const isPdf = file.type === 'application/pdf'
      const data = await api.postForm(isPdf ? '/ocr/pdf' : '/ocr/image', formData)
      const nextPages = isPdf
        ? pagesFromPdf(data.page_texts ?? [data.text])
        : pagesFromText(data.text)
      if (!nextPages.length) {
        showToast('Could not extract text. Please try a clearer file.', 'error')
        return
      }
      loadDocument(nextPages, isPdf ? 'pdf' : 'image')
      const pageNote = nextPages.length > 1 ? ` across ${nextPages.length} pages` : ''
      showToast(`Extracted ${data.word_count} words${pageNote}.`, 'success')
    } catch (err) {
      showToast(err.message, 'error')
    } finally {
      setIsUploading(false)
      event.target.value = ''
    }
  }

  /* ── Simplify the current page ── */
  async function handleSimplify() {
    const index = pageIndex
    const sourceText = currentText.trim()
    if (!sourceText) return
    setIsSimplifying(true)
    try {
      const data = await api.post('/reading/simplify', { text: sourceText })
      // Never replace the user's notes with an empty result.
      if (!data?.simplified_text?.trim()) {
        showToast('Simplification unavailable. Your original text is unchanged.', 'warning')
        return
      }
      setOriginals(prev => ({ ...prev, [index]: prev[index] ?? currentText }))
      setSimplifiedInfo(prev => ({ ...prev, [index]: data }))
      updatePageText(index, data.simplified_text)
      showToast(pages.length > 1 ? `Page ${currentPage.number} simplified.` : 'Text simplified.', 'success')
    } catch (error) {
      showToast(error?.message || 'Simplification unavailable.', 'warning')
    } finally {
      setIsSimplifying(false)
    }
  }

  function handleRestore() {
    const index = pageIndex
    if (originals[index] == null) return
    updatePageText(index, originals[index])
    setOriginals(prev => { const next = { ...prev }; delete next[index]; return next })
    setSimplifiedInfo(prev => { const next = { ...prev }; delete next[index]; return next })
    showToast('Original text restored.', 'info')
  }

  function handleWordClick(word) {
    const clean = normalizeWord(word)
    if (!clean) return
    // Pause reading so the definition and word audio don't talk over it;
    // handleDefinitionClose resumes it.
    if (isPlaying) {
      pauseReading()
      pausedForDefinitionRef.current = true
    }
    setSelectedWord(clean)
    // Asking to hear a word again is what the repeat log counts (F41, F49).
    recordReplay(clean)
    playWord(clean)
  }

  /* ── Play — the player chunks the page's word list, so indices match WordDisplay ── */
  function handlePlay() {
    setSessionPages(prev => (prev.includes(pageIndex) ? prev : [...prev, pageIndex]))
    play(words, speed, prefs.phrasePauses)
  }

  /* ── A page finished: continue onto the next page, or end the session ── */
  function handleReadingEnded() {
    const next = pageIndex + 1
    if (next < pages.length) {
      setSessionOffset(offset => offset + words.length)
      setSessionPages(prev => (prev.includes(next) ? prev : [...prev, next]))
      setPageIndex(next)
      setActiveIndex(-1)
      play(splitWords(pages[next].text), speed, prefs.phrasePauses)
    } else {
      endSession()
      resetSession()
    }
  }

  // No paused-time bookkeeping here: useReadingSession banks time only while
  // isPlaying is true, so a pause is simply never counted.
  function pauseReading() {
    pause()
  }

  function resumeReading() {
    resume()
  }

  function handlePauseResume() {
    pausedForDefinitionRef.current = false
    if (isPaused) resumeReading()
    else pauseReading()
  }

  /* ── Closing the definition resumes reading if a word click paused it ── */
  function handleDefinitionClose() {
    setSelectedWord(null)
    if (pausedForDefinitionRef.current && isPaused) resumeReading()
    pausedForDefinitionRef.current = false
  }

  function handleStop() {
    pausedForDefinitionRef.current = false
    // Stop while paused never passes through the playing -> stopped transition
    // useReadingSession watches for, so the session is ended explicitly here.
    endSession()
    resetSession()
    stop()
    setActiveIndex(-1)
  }

  function handleSpeedChange(nextSpeed) {
    setSpeed(nextSpeed)
    changeSpeed(nextSpeed)
  }

  /* ── Keyboard: Escape leaves focus mode; ← / → change page ── */
  useEffect(() => {
    function handleKeyDown(e) {
      if (e.key === 'Escape' && distractionFree) {
        setDistraction(false)
        return
      }
      const typing = e.target.closest?.('input, textarea, select, [contenteditable="true"]')
      if (typing || selectedWord || e.altKey || e.ctrlKey || e.metaKey) return
      if (e.key === 'ArrowRight') goToPage(pageIndex + 1)
      if (e.key === 'ArrowLeft') goToPage(pageIndex - 1)
    }

    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  })

  const activeSearchTerms = useMemo(
    () => (query.trim().length >= 2 ? searchTerms(query) : []),
    [query],
  )

  const isMultiPage = pages.length > 1
  const pageSimplified = simplifiedInfo[pageIndex]

  const readingColumn = (
    <>
      <PageNav pages={pages} pageIndex={pageIndex} onChange={goToPage} />
      <WordDisplay
        words={words}
        paragraphStarts={paragraphStarts}
        activeIndex={activeIndex}
        classifiedWords={classifiedWords}
        onWordClick={handleWordClick}
        focusRulerEnabled={prefs.focusRuler}
        searchTerms={activeSearchTerms}
        syllableView={syllableView}
        syllableMap={syllableMap}
      />
      <ReadingProgress
        activeIndex={activeIndex}
        totalWords={words.length}
        durationMs={totalDurationMs}
      />
      {isMultiPage && words.length > 150 && (
        <PageNav pages={pages} pageIndex={pageIndex} onChange={goToPage} />
      )}
    </>
  )

  return (
    <main
      className={
        distractionFree
          ? 'min-h-screen p-4 pb-28'
          : 'min-h-screen bg-gray-50/70 px-4 py-6 pb-28 dark:bg-[#1E1E1E] sm:px-6'
      }
    >
      {/* ════════════ NORMAL MODE ════════════ */}
      {!distractionFree && (
        <section className="mx-auto max-w-[1440px]">
          {/* Page header — compact once a document is open */}
          <div className={hasText ? 'mb-5' : 'mb-8'}>
            <p className="text-sm font-semibold uppercase tracking-wide text-blue-600 dark:text-blue-300">
              Reading workspace
            </p>
            <h1 className={`mt-1 font-bold tracking-tight text-gray-950 dark:text-white
              ${hasText ? 'text-2xl' : 'text-3xl sm:text-4xl'}`}>
              Read, listen, and understand.
            </h1>
            {!hasText && (
              <p className="mt-3 max-w-2xl text-base leading-relaxed text-gray-600 dark:text-gray-300">
                Upload your notes, simplify hard passages, listen with word-by-word
                highlighting, and tap any word for its meaning.
              </p>
            )}
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
                  onClick={() => loadDocument(pagesFromText(SAMPLE_TEXT), 'sample')}
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
                  value=""
                  onChange={e => loadDocument(pagesFromText(e.target.value), 'paste')}
                  aria-label="Paste your reading text"
                />
              </div>
            </div>
          )}

          {/* ════════════ READING LAYOUT ════════════
              xl:  [pages + search] [reading] [tools]
              lg:  [reading] [pages + search / tools stacked]
              <lg: reading, then pages/search, then tools */}
          {hasText && (
            <div
              className={`grid gap-5 ${isMultiPage
                ? 'lg:grid-cols-[minmax(0,1fr)_300px] xl:grid-cols-[260px_minmax(0,1fr)_320px]'
                : 'lg:grid-cols-[minmax(0,1fr)_320px]'}`}
            >
              {isMultiPage && (
                <aside
                  className="order-2 lg:order-none lg:col-start-2 lg:row-start-1
                              xl:sticky xl:top-20 xl:col-start-1 xl:self-start"
                  aria-label="Pages and search"
                >
                  <DocumentNavigator
                    pages={pages}
                    pageIndex={pageIndex}
                    onSelectPage={goToPage}
                    query={query}
                    onQueryChange={setQuery}
                  />
                </aside>
              )}

              <section
                className={`order-1 min-w-0 space-y-4 lg:order-none lg:col-start-1 lg:row-start-1
                  ${isMultiPage ? 'lg:row-span-2 xl:col-start-2 xl:row-span-1' : ''}`}
                aria-label="Reading content"
              >
                {readingColumn}
              </section>

              {/* Tools: upload, edit this page, simplify, complexity */}
              <aside
                className={`order-3 space-y-4 lg:order-none lg:col-start-2 lg:self-start
                  ${isMultiPage
                    ? 'lg:row-start-2 xl:sticky xl:top-20 xl:col-start-3 xl:row-start-1'
                    : 'lg:sticky lg:top-20 lg:row-start-1'}`}
                aria-label="Tools"
              >
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
                      onClick={() => loadDocument(pagesFromText(SAMPLE_TEXT), 'sample')}
                      className="rounded-lg border border-gray-200 px-3 py-2 text-xs
                                  font-semibold text-gray-600 hover:bg-gray-50
                                  dark:border-gray-700 dark:text-gray-300"
                    >
                      Sample
                    </button>
                  </div>

                  <label
                    className="text-xs font-semibold text-gray-500 dark:text-gray-400"
                    htmlFor="page-editor"
                  >
                    {isMultiPage ? `Edit page ${currentPage.number}` : 'Edit text'}
                  </label>
                  <textarea
                    id="page-editor"
                    className="surface mt-1 min-h-[16rem] w-full resize-y rounded-xl border
                                border-gray-200 bg-white p-3 text-sm leading-relaxed shadow-inner
                                focus:border-blue-400 focus:outline-none
                                dark:border-gray-700"
                    value={currentText}
                    onChange={e => updatePageText(pageIndex, e.target.value)}
                  />

                  <div className="mt-3 flex flex-wrap gap-2">
                    <button
                      type="button"
                      onClick={handleSimplify}
                      disabled={!currentText.trim() || isSimplifying}
                      className="rounded-lg bg-purple-600 px-3 py-2 text-xs font-semibold
                                  text-white hover:bg-purple-700 disabled:opacity-50"
                    >
                      {isSimplifying ? 'Simplifying…' : isMultiPage ? '✨ Simplify this page' : '✨ Simplify'}
                    </button>
                    {pageSimplified && (
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

                {complexity && currentText.trim() && (
                  <ComplexityBadge
                    complexity={complexity}
                    title={isMultiPage ? `Page ${currentPage.number}` : 'This text'}
                  />
                )}

                {pageSimplified && (
                  <div
                    className="rounded-xl border border-purple-100 bg-purple-50 p-3
                                text-xs text-purple-800
                                dark:border-purple-900 dark:bg-purple-950/40 dark:text-purple-200"
                  >
                    This page has been rewritten in simpler words. Use Restore to
                    bring back the original.
                  </div>
                )}
              </aside>
            </div>
          )}
        </section>
      )}

      {/* ════════════ DISTRACTION-FREE MODE ════════════ */}
      {distractionFree && hasText && (
        <section className="distraction-free mx-auto max-w-3xl space-y-4">
          {readingColumn}
        </section>
      )}

      {/* ═══════════════════════════════════════════════════
          STICKY PLAYBACK TOOLBAR — always visible
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
                disabled={isLoading || !words.length}
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

            {/* Syllable view toggle */}
            <button
              type="button"
              onClick={() => setSyllableView(!syllableView)}
              aria-pressed={syllableView}
              className={`rounded-xl border px-4 py-2 text-xs font-semibold
                ${syllableView
                  ? 'border-blue-300 bg-blue-50 text-blue-700 dark:border-blue-700 dark:bg-blue-950/50 dark:text-blue-200'
                  : 'border-gray-200 text-gray-600 hover:bg-gray-50 dark:border-gray-700 dark:text-gray-300 dark:hover:bg-gray-800'}`}
            >
              {syllableView ? '✓ Syl·la·bles' : 'Syl·la·bles'}
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

            {/* Current page / word indicator */}
            {isMultiPage && (
              <span className="hidden text-xs font-semibold text-gray-500 dark:text-gray-400 md:inline">
                Page {currentPage.number}
              </span>
            )}
            {isAudioActive && activeIndex >= 0 && activeIndex < words.length && (
              <div className="current-word-display ml-2 hidden sm:flex" aria-live="polite">
                {words[activeIndex]?.replace(/[^a-zA-Z']/g, '') || ''}
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
                  width: `${words.length > 0
                    ? Math.round(((activeIndex + 1) / words.length) * 100)
                    : 0}%`
                }}
              />
            </div>
          )}
        </div>
      )}

      {/* Definition panel */}
      <DefinitionPanel
        word={selectedWord}
        onClose={handleDefinitionClose}
        onPlayWord={playWord}
      />

      {toast && <Toast message={toast.message} type={toast.type} onClose={hideToast} />}
    </main>
  )
}
