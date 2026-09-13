/**
 * Document model for the Reading page: text split into pages, each page
 * split into paragraphs. Pure functions — no React — so they're easy to test.
 *
 * A page is { number, text } where `number` is the label shown to the user
 * (the real PDF page number) and `text` holds paragraphs separated by "\n\n".
 */

export const PAGE_TARGET_WORDS = 300   // auto-split size for pasted/photo text
const LONG_PARAGRAPH_WORDS     = 120   // split OCR walls of text into readable chunks
const PARAGRAPH_TARGET_WORDS   = 80

export function splitWords(text) {
  return String(text || '').split(/\s+/).filter(Boolean)
}

/** Paragraph strings of a page's (possibly user-edited) text. Every line
 *  break counts, so lists and typed paragraphs stay as the user wrote them. */
export function parseParagraphs(text) {
  return String(text || '')
    .split(/\n+/)
    .map(p => p.replace(/[ \t]+/g, ' ').trim())
    .filter(Boolean)
}

/** Word list of a page + the word indices where each paragraph starts.
 *  Indices match splitWords(text), which is what TTS timings use. */
export function pageLayout(text) {
  const words = []
  const paragraphStarts = []
  for (const paragraph of parseParagraphs(text)) {
    paragraphStarts.push(words.length)
    words.push(...splitWords(paragraph))
  }
  return { words, paragraphStarts }
}

/** Split sentences of an over-long paragraph into ~PARAGRAPH_TARGET_WORDS pieces. */
function splitLongParagraph(paragraph) {
  if (splitWords(paragraph).length <= LONG_PARAGRAPH_WORDS) return [paragraph]
  const sentences = paragraph.match(/[^.!?]+(?:[.!?]+["')\]]*|$)\s*/g) || [paragraph]
  const out = []
  let current = ''
  for (const sentence of sentences) {
    current += sentence
    if (splitWords(current).length >= PARAGRAPH_TARGET_WORDS) {
      out.push(current.trim())
      current = ''
    }
  }
  if (current.trim()) out.push(current.trim())
  return out
}

/**
 * Rebuild paragraphs from hard-wrapped text (PDF text layers put a newline
 * at the end of every printed line). A line ends a paragraph when:
 *  - it's followed by a blank line, or
 *  - it ends a sentence and is clearly shorter than a full line, or
 *  - it's a short heading-like line.
 * Words hyphenated across lines ("photo-\nsynthesis") are rejoined.
 * Returns paragraphs joined by "\n\n".
 */
export function reflowText(raw) {
  const lines = String(raw || '').replace(/\r\n?/g, '\n').split('\n')
  const lengths = lines.map(l => l.trim().length).filter(Boolean).sort((a, b) => a - b)
  if (!lengths.length) return ''
  // Typical full-line length (90th percentile) — robust to a few long lines.
  const fullLine = lengths[Math.floor(lengths.length * 0.9)] || lengths[lengths.length - 1]
  const allShort = fullLine < 45 // lists / typed notes: keep every line

  const paragraphs = []
  let current = ''
  const flush = () => {
    if (current.trim()) paragraphs.push(...splitLongParagraph(current.trim()))
    current = ''
  }

  lines.forEach((rawLine, i) => {
    const line = rawLine.trim()
    if (!line) { flush(); return }

    const next = (lines[i + 1] || '').trim()
    const endsSentence = /[.!?:]["')\]]*$/.test(line)
    const shortLine = line.length < fullLine * 0.8
    const heading = line.length < fullLine * 0.5 && !/[.,;]$/.test(line) && /^[A-Z0-9]/.test(next)

    // A heading after a finished sentence, or a bullet / numbered item,
    // starts its own paragraph.
    if (heading && /[.!?:]["')\]]*$/.test(current)) flush()
    if (/^([•●▪◦‣*–-]\s|\d{1,2}[.)]\s)/.test(line)) flush()

    if (current.endsWith('-') && /^[a-z]/.test(line)) {
      current = current.slice(0, -1) + line          // re-join hyphenated word
    } else {
      current = current ? `${current} ${line}` : line
    }

    if (allShort || !next || (endsSentence && shortLine) || heading) flush()
  })
  flush()
  return paragraphs.join('\n\n')
}

/** Group paragraphs into pages of roughly `target` words, breaking only
 *  between paragraphs. */
export function paginateText(text, target = PAGE_TARGET_WORDS) {
  const pages = []
  let current = []
  let count = 0
  for (const paragraph of parseParagraphs(text)) {
    const n = splitWords(paragraph).length
    if (count > 0 && count + n > target * 1.25) {
      pages.push(current.join('\n\n'))
      current = []
      count = 0
    }
    current.push(paragraph)
    count += n
  }
  if (current.length) pages.push(current.join('\n\n'))
  return pages.map((t, i) => ({ number: i + 1, text: t }))
}

/**
 * Remove running headers/footers: a line among the first/last two lines of
 * a page that repeats on at least 60% of pages (digits ignored, so
 * "Page 3 of 9" matches "Page 4 of 9").
 */
function stripRunningHeaders(pageTexts) {
  if (pageTexts.length < 3) return pageTexts
  const key = line => line.trim().replace(/\d+/g, '#')
  const edgeLines = text => {
    const lines = text.split('\n').filter(l => l.trim())
    return [...new Set([...lines.slice(0, 2), ...lines.slice(-2)].map(key))]
  }
  const counts = new Map()
  pageTexts.forEach(t => edgeLines(t).forEach(k => counts.set(k, (counts.get(k) || 0) + 1)))
  const repeated = new Set(
    [...counts].filter(([k, n]) => k && n >= pageTexts.length * 0.6).map(([k]) => k)
  )
  if (!repeated.size) return pageTexts
  return pageTexts.map(text => {
    const lines = text.split('\n')
    const edge = new Set()
    const nonBlank = lines.map((l, i) => (l.trim() ? i : -1)).filter(i => i >= 0)
    ;[...nonBlank.slice(0, 2), ...nonBlank.slice(-2)].forEach(i => edge.add(i))
    return lines.filter((l, i) => !(edge.has(i) && repeated.has(key(l)))).join('\n')
  })
}

/** PDF: one page per PDF page (blank pages dropped, real numbers kept). */
export function pagesFromPdf(pageTexts) {
  return stripRunningHeaders(pageTexts)
    .map((raw, i) => ({ number: i + 1, text: reflowText(raw) }))
    .filter(p => p.text.trim())
}

/** Pasted text / photo OCR: reflow, then auto-split into pages. */
export function pagesFromText(raw) {
  return paginateText(reflowText(raw))
}

export function normalizeWord(word) {
  return String(word || '').toLowerCase().replace(/[^\w']/g, '')
}

/** Same cleaning rule as the backend's clean_word() — the key syllable
 *  breakdowns are stored under ("Cells," → "cells"). */
export function syllableKey(word) {
  return String(word || '').toLowerCase().replace(/[^a-z']/g, '').replace(/^'+|'+$/g, '')
}

/**
 * Map a syllable breakdown back onto the original token so capitals and
 * punctuation survive: "Sunlight," + [sun, light] → ["Sun", "light,"].
 * Returns [word] when there's no breakdown.
 */
export function splitWordBySyllables(word, syllables) {
  const text = String(word || '')
  if (!syllables || syllables.length < 2) return [text]

  const key = syllableKey(text)
  if (key.length !== syllables.join('').length) return [text]

  const pieces = []
  let current = ''
  let keyIndex = 0          // position within the cleaned word
  let syllableIndex = 0
  let remaining = syllables[0].length

  for (const char of text) {
    current += char
    if (keyIndex < key.length && char.toLowerCase() === key[keyIndex]) {
      keyIndex++
      remaining--
      // Close this syllable, but keep trailing punctuation with it.
      if (remaining === 0 && syllableIndex < syllables.length - 1) {
        pieces.push(current)
        current = ''
        syllableIndex++
        remaining = syllables[syllableIndex].length
      }
    }
  }
  if (current) pieces.push(current)

  // Punctuation between syllables belongs to the piece before it
  // ("Es|tates|-Gen" → "Es|tates-|Gen").
  for (let i = 1; i < pieces.length; i++) {
    const lead = pieces[i].match(/^[^a-z']+/i)
    if (lead) {
      pieces[i - 1] += lead[0]
      pieces[i] = pieces[i].slice(lead[0].length)
    }
  }
  return pieces.filter(Boolean).length ? pieces.filter(Boolean) : [text]
}

/** Normalized search terms of a query ("Calvin cycle!" → ["calvin", "cycle"]). */
export function searchTerms(query) {
  return splitWords(query).map(normalizeWord).filter(Boolean)
}

/**
 * Case-insensitive phrase search across pages. Returns up to `limit` hits:
 * { pageIndex, number, before, match, after } with short context snippets.
 */
export function searchPages(pages, query, limit = 50) {
  const q = query.trim()
  if (q.length < 2) return []
  const escaped = q.replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/\s+/g, '\\s+')
  const re = new RegExp(escaped, 'gi')
  const hits = []
  pages.forEach((page, pageIndex) => {
    const flat = page.text.replace(/\s+/g, ' ')
    for (const m of flat.matchAll(re)) {
      const start = m.index
      const end = start + m[0].length
      hits.push({
        pageIndex,
        number: page.number,
        before: (start > 40 ? '…' : '') + flat.slice(Math.max(0, start - 40), start),
        match: m[0],
        after: flat.slice(end, end + 40) + (end + 40 < flat.length ? '…' : ''),
      })
      if (hits.length >= limit) return
    }
  })
  return hits.slice(0, limit)
}
