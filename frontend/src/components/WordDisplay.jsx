import { useEffect, useMemo, useRef } from 'react'

import { splitWordBySyllables, syllableKey } from '../utils/document'

/**
 * Renders words as clickable tokens with:
 *  - Current word highlight (active)
 *  - Current sentence highlight (sentence-active)
 *  - Hard word marking — soft amber, NOT red (#2)
 *  - Focus ruler with dimming overlays (#7)
 *  - Hard word legend (#3)
 *  - Current word display (#5)
 *  - Book-like reading width (#1)
 */
export default function WordDisplay({
  words,
  activeIndex,
  classifiedWords,
  onWordClick,
  focusRulerEnabled,
  paragraphStarts = [0],   // word indices where each paragraph begins
  searchTerms = [],        // normalized search terms to outline
  syllableView = false,    // show every word split into syllables
  syllableMap = {},        // cleaned word → ["pho","to","syn","the","sis"]
}) {
  const activeWordRef = useRef(null)
  const firstHitRef   = useRef(null)
  const containerRef  = useRef(null)
  const rulerRef      = useRef(null)
  const dimTopRef     = useRef(null)
  const dimBottomRef  = useRef(null)

  /* ── Count hard words for legend visibility ── */
  const hardWordCount = useMemo(() => {
    return words.filter(w => {
      const clean = String(w || '').toLowerCase().replace(/[^\w']/g, '')
      const label = classifiedWords[clean]
      return label === 'Hard'
    }).length
  }, [words, classifiedWords])

  /* ── Compute sentence boundaries ── */
  const sentenceRanges = useMemo(() => {
    const ranges = []
    let start = 0
    words.forEach((w, i) => {
      if (/[.!?]$/.test(w)) {
        ranges.push({ start, end: i })
        start = i + 1
      }
    })
    if (start < words.length) {
      ranges.push({ start, end: words.length - 1 })
    }
    return ranges
  }, [words])

  /* ── Which sentence is active? ── */
  const activeSentence = useMemo(() => {
    if (activeIndex < 0) return null
    return sentenceRanges.find(r => activeIndex >= r.start && activeIndex <= r.end) || null
  }, [activeIndex, sentenceRanges])

  /* ── Current active word text for display (#5) ── */
  const activeWord = activeIndex >= 0 && activeIndex < words.length
    ? words[activeIndex].replace(/[^a-zA-Z']/g, '')
    : null

  /* ── Position focus ruler + dimming overlays (#7) ── */
  useEffect(() => {
    if (!focusRulerEnabled || !activeWordRef.current || !containerRef.current) {
      if (rulerRef.current) rulerRef.current.style.display = 'none'
      if (dimTopRef.current) dimTopRef.current.style.display = 'none'
      if (dimBottomRef.current) dimBottomRef.current.style.display = 'none'
      return
    }

    const wordRect      = activeWordRef.current.getBoundingClientRect()
    const containerRect = containerRef.current.getBoundingClientRect()
    const wordY         = wordRect.top - containerRect.top
    const rulerHeight   = wordRect.height * 1.5
    const containerHeight = containerRef.current.scrollHeight

    if (rulerRef.current) {
      rulerRef.current.style.display = 'block'
      rulerRef.current.style.top     = `${wordY - rulerHeight * 0.15}px`
      rulerRef.current.style.height  = `${rulerHeight}px`
    }

    if (dimTopRef.current) {
      dimTopRef.current.style.display = 'block'
      dimTopRef.current.style.top     = '0px'
      dimTopRef.current.style.height  = `${Math.max(0, wordY - rulerHeight * 0.15)}px`
    }

    if (dimBottomRef.current) {
      const bottomStart = wordY + rulerHeight * 0.85
      dimBottomRef.current.style.display = 'block'
      dimBottomRef.current.style.top     = `${bottomStart}px`
      dimBottomRef.current.style.height  = `${Math.max(0, containerHeight - bottomStart)}px`
    }
  }, [activeIndex, focusRulerEnabled])

  /* ── Auto-scroll active word into view ── */
  useEffect(() => {
    if (activeWordRef.current) {
      activeWordRef.current.scrollIntoView({
        behavior: 'smooth',
        block: 'center',
        inline: 'nearest',
      })
    }
  }, [activeIndex])

  /* ── Search: which words match, and scroll to the first match ── */
  const isSearchHit = clean =>
    searchTerms.length > 0 && clean.length > 0 && searchTerms.some(t => clean.includes(t))

  const firstHitIndex = useMemo(() => {
    if (!searchTerms.length) return -1
    return words.findIndex(w => {
      const clean = String(w || '').toLowerCase().replace(/[^\w']/g, '')
      return clean.length > 0 && searchTerms.some(t => clean.includes(t))
    })
  }, [words, searchTerms])

  useEffect(() => {
    if (activeIndex < 0 && firstHitRef.current) {
      firstHitRef.current.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }
  }, [firstHitIndex, words]) // eslint-disable-line react-hooks/exhaustive-deps

  /* ── Group word indices into paragraphs ── */
  const paragraphs = useMemo(() => {
    const starts = paragraphStarts.length ? paragraphStarts : [0]
    return starts.map((start, p) => {
      const end = p + 1 < starts.length ? starts[p + 1] : words.length
      return Array.from({ length: Math.max(0, end - start) }, (_, k) => start + k)
    }).filter(indices => indices.length)
  }, [paragraphStarts, words.length])

  return (
    <div className="space-y-3">
      {/* ── CHANGE #3: Hard word legend ── */}
      {hardWordCount > 0 && (
        <div className="hard-word-legend reading-content-area" role="note">
          <span className="hard-word-legend-swatch" aria-hidden="true" />
          <span>
            <strong>{hardWordCount} highlighted word{hardWordCount !== 1 ? 's' : ''}</strong> may
            be challenging. Tap any to hear pronunciation and see definitions.
          </span>
        </div>
      )}

      {/* ── CHANGE #5: Current word display ── */}
      {activeWord && (
        <div
          className="reading-content-area flex items-center gap-2"
          role="status"
          aria-live="polite"
          aria-label={`Currently reading: ${activeWord}`}
        >
          <span className="text-xs font-medium text-gray-400 dark:text-gray-500">
            Reading:
          </span>
          <span className="current-word-display">{activeWord}</span>
        </div>
      )}

      {/* ── Main reading panel ── */}
      <div
        ref={containerRef}
        className="reading-panel reading-content-area relative rounded-2xl border
                    border-gray-200 bg-white p-6 shadow-sm sm:p-8
                    dark:border-gray-700 dark:bg-[#2A2A2A]"
        role="region"
        aria-label="Reading area"
      >
        {/* Focus ruler layers (#7) */}
        {focusRulerEnabled && (
          <>
            <div ref={dimTopRef} className="focus-ruler-dim" style={{ display: 'none' }} />
            <div ref={rulerRef} className="focus-ruler" style={{ display: 'none' }} />
            <div ref={dimBottomRef} className="focus-ruler-dim" style={{ display: 'none' }} />
          </>
        )}

        {/* Word tokens, one <p> per paragraph (indices stay page-global) */}
        <div className="relative z-10">
          {paragraphs.map(indices => (
            <p key={indices[0]} className="reading-paragraph m-0">
              {indices.map(i => {
                const word       = words[i]
                const isActive   = i === activeIndex
                const cleanWord  = String(word || '').toLowerCase().replace(/[^\w']/g, '')
                const label      = classifiedWords[cleanWord]
                const isHard     = label === 'Hard'
                const isHit      = isSearchHit(cleanWord)
                const isSentenceActive =
                  activeSentence && i >= activeSentence.start && i <= activeSentence.end

                const classes = [
                  'word-token',
                  isActive && 'active',
                  isHard && 'hard-word',
                  isHit && 'search-hit',
                  isSentenceActive && !isActive && 'sentence-active',
                ]
                  .filter(Boolean)
                  .join(' ')

                return (
                  <button
                    key={`${word}-${i}`}
                    ref={isActive ? activeWordRef : i === firstHitIndex ? firstHitRef : null}
                    type="button"
                    onClick={() => onWordClick(word)}
                    className={classes}
                    tabIndex={isHard || isHit ? 0 : -1}
                    aria-label={
                      isHard
                        ? `${word} — may be challenging, tap for definition`
                        : `Play word ${word}`
                    }
                  >
                    {syllableView
                      ? splitWordBySyllables(word, syllableMap[syllableKey(word)])
                          .map((piece, p, all) => (
                            <span key={p}>
                              {/* no dot where the word already breaks itself
                                  ("light-dependent") */}
                              {p > 0 && /[a-z']$/i.test(all[p - 1]) && (
                                <span className="syllable-sep" aria-hidden="true">·</span>
                              )}
                              {piece}
                            </span>
                          ))
                      : word}
                  </button>
                )
              })}
            </p>
          ))}
        </div>
      </div>
    </div>
  )
}