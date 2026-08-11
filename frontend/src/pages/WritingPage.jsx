import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'

import HighlightedEditor from '../components/HighlightedEditor.jsx'
import SuggestionBar from '../components/SuggestionBar.jsx'
import WritingChecks from '../components/WritingChecks.jsx'
import { api } from '../utils/api'
import { useAutosave } from '../hooks/useAutosave'
import { useNLP } from '../hooks/useNLP'
import { usePrefs } from '../context/PreferencesContext'
import { useWordPredict } from '../hooks/useWordPredict'

/** Matches MAX_CONTENT_CHARS in backend/routers/writing.py. */
const MAX_CONTENT_CHARS = 50_000
const MAX_TITLE_CHARS = 150
/** Matches DEFAULT_TITLE in backend/routers/writing.py. */
const DEFAULT_TITLE = 'Untitled'

// The load is what arms autosave, so a failure has to be recoverable without a
// page reload. Retry on our own first — the backend is often just still booting
// — then fall back to a button the user drives.
const AUTO_LOAD_RETRIES = 2
const RETRY_BACKOFF_MS = 2000

function countWords(text) {
  const trimmed = text.trim()
  return trimmed ? trimmed.split(/\s+/).length : 0
}

function formatTime(date) {
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

/**
 * F25 writing notepad · F31 autosave.
 *
 * The document loads before autosave is armed, so an empty textarea can never
 * overwrite saved text. Typography comes from the shared reading preferences,
 * so the notepad matches the reading workspace.
 */
export default function WritingPage() {
  const { prefs } = usePrefs()

  const [title, setTitle] = useState(DEFAULT_TITLE)
  const [content, setContent] = useState('')
  // 'loading' → still fetching (including automatic retries)
  // 'ready'   → baseline known, autosave armed
  // 'failed'  → out of automatic retries, waiting on the user
  const [loadState, setLoadState] = useState('loading')
  const [attempt, setAttempt] = useState(0)

  const ready = loadState === 'ready'

  const { dirty, saving, error, lastSavedAt, saveNow, adoptDocument } = useAutosave({
    title,
    content,
    enabled: ready,
  })

  /* ── Restore the last saved draft ── */
  // adoptDocument is stable, so this refetches only when a retry bumps
  // `attempt` — never on its own after a successful load.
  useEffect(() => {
    let cancelled = false
    let retryTimer = null

    api
      .get('/writing/autosave')
      .then(({ document: doc }) => {
        if (cancelled) return
        if (doc) {
          setTitle(doc.title)
          setContent(doc.content)
        }
        // With nothing saved yet the baseline is the blank page as rendered,
        // otherwise the default title alone would read as an unsaved change.
        adoptDocument(doc ?? { id: null, title: DEFAULT_TITLE, content: '' })
        setLoadState('ready')
      })
      .catch(() => {
        if (cancelled) return
        // Autosave stays disarmed either way: without knowing what the server
        // holds, a save would push this empty page over whatever is stored.
        if (attempt < AUTO_LOAD_RETRIES) {
          retryTimer = setTimeout(
            () => setAttempt(n => n + 1),
            RETRY_BACKOFF_MS * (attempt + 1)
          )
        } else {
          setLoadState('failed')
        }
      })

    return () => {
      cancelled = true
      if (retryTimer) clearTimeout(retryTimer)
    }
  }, [attempt, adoptDocument])

  const retryLoad = useCallback(() => {
    setLoadState('loading')
    setAttempt(n => n + 1)
  }, [])

  const checks = useNLP(content, { enabled: ready })

  const notepadRef = useRef(null)

  // Prediction is about the caret, not the document, so it has to follow the
  // cursor as well as the text — clicking into the middle of a sentence should
  // predict from there rather than from the end.
  const [caret, setCaret] = useState(0)
  const trackCaret = useCallback(event => setCaret(event.target.selectionStart), [])
  const pendingCaretRef = useRef(null)

  const predictions = useWordPredict(content.slice(0, caret), { enabled: ready })

  /* ── Insert a suggestion at the caret (F29) ── */
  const insertSuggestion = useCallback(
    suggestion => {
      const textarea = notepadRef.current
      if (!textarea) return

      const position = textarea.selectionStart
      const before = content.slice(0, position)
      const after = content.slice(position)

      // Mid-word, the pill completes the word being typed, so the partial word
      // it replaces has to come out first.
      const partial = before.match(/[A-Za-z']+$/)
      const head = partial ? before.slice(0, before.length - partial[0].length) : before

      // At a boundary the caret may sit straight after punctuation ("The end.")
      // where an inserted word would otherwise collide with it.
      const needsSpace = !partial && head.length > 0 && !/\s$/.test(head)
      // A trailing space saves a keystroke and is what word prediction
      // elsewhere does — but not if the following text already starts with one.
      const trailing = after && /^\s/.test(after) ? '' : ' '
      const insertion = `${needsSpace ? ' ' : ''}${suggestion}${trailing}`

      const nextCaret = head.length + insertion.length
      setContent(head + insertion + after)
      setCaret(nextCaret)
      // Applied by the layout effect below, once the new value is in the DOM.
      pendingCaretRef.current = nextCaret
    },
    [content]
  )

  // Put the cursor back after an insertion so typing continues where the
  // suggestion left off. This has to happen after React commits the new value,
  // and in a layout effect rather than requestAnimationFrame — rAF is throttled
  // in a background tab, which left the caret stranded and focus on <body>.
  useLayoutEffect(() => {
    const position = pendingCaretRef.current
    if (position == null) return
    pendingCaretRef.current = null

    const textarea = notepadRef.current
    if (!textarea) return
    textarea.focus()
    textarea.setSelectionRange(position, position)
  }, [content])

  // Shared by the textarea and the highlight mirror behind it — any difference
  // in these would wrap the two differently and slide the marks off the words.
  const editorStyle = useMemo(
    () => ({
      fontFamily: `'${prefs.font}', Arial, Verdana, sans-serif`,
      fontSize: `${prefs.fontSize}px`,
      lineHeight: String(prefs.lineSpacing),
      wordSpacing: `${prefs.wordSpacing}px`,
      scrollbarGutter: 'stable',
    }),
    [prefs.font, prefs.fontSize, prefs.lineSpacing, prefs.wordSpacing]
  )

  /* ── Jump to a flagged word (F26-F28) ── */
  const locateIssue = useCallback(issue => {
    const textarea = notepadRef.current
    if (!textarea) return
    textarea.focus()
    textarea.setSelectionRange(issue.start, issue.end)
  }, [])

  /* ── Apply a suggestion (F26-F28) ── */
  const applySuggestion = useCallback((issue, suggestion) => {
    setContent(current => {
      // Guard against a race with a keystroke that landed between the check
      // and the click: only replace when the span still holds what was flagged.
      if (current.slice(issue.start, issue.end) !== issue.text) return current
      return current.slice(0, issue.start) + suggestion + current.slice(issue.end)
    })
  }, [])

  const wordCount = useMemo(() => countWords(content), [content])

  let status = content ? 'All changes saved' : 'Nothing written yet'
  let statusTone = 'text-gray-400 dark:text-gray-500'
  if (loadState === 'loading') {
    status = 'Loading…'
  } else if (loadState === 'failed') {
    status = 'Not connected'
    statusTone = 'text-red-600 dark:text-red-400'
  } else if (error) {
    status = "Couldn't save — will retry"
    statusTone = 'text-red-600 dark:text-red-400'
  } else if (saving) {
    status = 'Saving…'
    statusTone = 'text-blue-600 dark:text-blue-300'
  } else if (dirty) {
    status = 'Unsaved changes'
    statusTone = 'text-amber-600 dark:text-amber-400'
  } else if (lastSavedAt) {
    status = `Saved at ${formatTime(lastSavedAt)}`
    statusTone = 'text-green-700 dark:text-green-400'
  }

  return (
    <main className="min-h-screen bg-gray-50/70 px-4 py-8 dark:bg-[#1E1E1E] sm:px-6">
      <section className="mx-auto max-w-6xl">
        {/* Page header */}
        <div className="mb-8">
          <p className="text-sm font-semibold uppercase tracking-wide text-blue-600 dark:text-blue-300">
            Writing workspace
          </p>
          <h1 className="mt-2 text-3xl font-bold tracking-tight text-gray-950 dark:text-white sm:text-4xl">
            Write without losing your place.
          </h1>
          <p className="mt-3 max-w-2xl text-base leading-relaxed text-gray-600 dark:text-gray-300">
            Your work saves itself every 30 seconds and comes back exactly as you
            left it — in the same typography you read with.
          </p>
        </div>

        <div className="grid gap-6 lg:grid-cols-[1fr_20rem]">
        <div
          className="rounded-3xl border border-gray-200 bg-white p-5 shadow-sm
                     dark:border-gray-800 dark:bg-[#2A2A2A] sm:p-6"
        >
          {/* Load failure — the notepad stays read-only until a load succeeds,
              because autosave can't safely run without knowing what's stored. */}
          {loadState === 'failed' && (
            <div
              className="mb-4 flex flex-wrap items-center gap-3 rounded-2xl border
                         border-red-200 bg-red-50 p-4 text-sm text-red-800
                         dark:border-red-900 dark:bg-red-950/40 dark:text-red-200"
              role="alert"
            >
              <span className="flex-1">
                Couldn't reach your saved writing. Editing is paused so nothing
                already saved gets overwritten.
              </span>
              <button
                type="button"
                onClick={retryLoad}
                className="rounded-xl bg-red-600 px-4 py-2 text-xs font-bold text-white
                           hover:bg-red-700 focus-visible:outline-2
                           focus-visible:outline-offset-2 focus-visible:outline-red-500"
              >
                Try again
              </button>
            </div>
          )}

          {/* Title + save controls */}
          <div className="mb-4 flex flex-wrap items-center gap-3">
            <label className="sr-only" htmlFor="doc-title">
              Document title
            </label>
            <input
              id="doc-title"
              type="text"
              value={title}
              maxLength={MAX_TITLE_CHARS}
              onChange={e => setTitle(e.target.value)}
              onBlur={() => setTitle(t => t.trim() || DEFAULT_TITLE)}
              placeholder="Untitled"
              disabled={!ready}
              className="min-w-0 flex-1 rounded-xl border border-transparent bg-transparent
                         px-2 py-1.5 text-lg font-semibold text-gray-900
                         hover:border-gray-200 focus:border-blue-400 focus:outline-none
                         disabled:opacity-60 dark:text-white dark:hover:border-gray-700"
            />

            <span className={`text-xs font-semibold ${statusTone}`} aria-live="polite">
              {status}
            </span>

            <button
              type="button"
              onClick={saveNow}
              disabled={!ready || saving || !dirty}
              className="rounded-xl bg-blue-600 px-4 py-2 text-xs font-bold text-white
                         shadow-sm hover:bg-blue-700 disabled:opacity-50
                         focus-visible:outline-2 focus-visible:outline-offset-2
                         focus-visible:outline-blue-500"
            >
              Save now
            </button>
          </div>

          {/* The notepad */}
          <label className="sr-only" htmlFor="notepad">
            Your writing
          </label>
          <HighlightedEditor
            id="notepad"
            textareaRef={notepadRef}
            value={content}
            maxLength={MAX_CONTENT_CHARS}
            onChange={e => {
              setContent(e.target.value)
              trackCaret(e)
            }}
            onSelect={trackCaret}
            disabled={!ready}
            placeholder={
              { ready: 'Start writing…', loading: 'Loading your writing…', failed: '' }[loadState]
            }
            issues={checks.issues}
            // Offsets only line up with the text that was checked, so marks are
            // hidden the moment it changes and return with the next check.
            showHighlights={!checks.stale}
            style={editorStyle}
            background={prefs.darkMode ? '#1E1E1E' : prefs.overlay}
          />

          {/* Word + phrase prediction, kept next to the caret rather than in
              the sidebar — these are for reaching for mid-sentence. */}
          {ready && (
            <SuggestionBar
              words={predictions.words}
              phrase={predictions.phrase}
              predicting={predictions.predicting}
              onInsert={insertSuggestion}
            />
          )}

          {/* Counters */}
          <div className="mt-3 flex flex-wrap items-center gap-4 text-xs text-gray-500 dark:text-gray-400">
            <span>{wordCount.toLocaleString()} {wordCount === 1 ? 'word' : 'words'}</span>
            <span>
              {content.length.toLocaleString()} / {MAX_CONTENT_CHARS.toLocaleString()} characters
            </span>
            {content.length >= MAX_CONTENT_CHARS && (
              <span className="font-semibold text-amber-600 dark:text-amber-400">
                Character limit reached.
              </span>
            )}
          </div>
        </div>

          <div className="lg:sticky lg:top-24 lg:self-start">
            <WritingChecks
              issues={checks.issues}
              counts={checks.counts}
              checking={checks.checking}
              stale={checks.stale}
              grammarAvailable={checks.grammarAvailable}
              checksAvailable={checks.checksAvailable}
              error={checks.error}
              hasText={Boolean(content.trim())}
              text={content}
              onApply={applySuggestion}
              onLocate={locateIssue}
            />
          </div>
        </div>
      </section>
    </main>
  )
}
