import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'

import DocumentList from '../components/DocumentList.jsx'
import HighlightedEditor from '../components/HighlightedEditor.jsx'
import SuggestionBar from '../components/SuggestionBar.jsx'
import TemplateSelector from '../components/TemplateSelector.jsx'
import WritingChecks from '../components/WritingChecks.jsx'
import { Toast } from '../components/Toast.jsx'
import { api } from '../utils/api'
import { useAutosave } from '../hooks/useAutosave'
import { useDocuments } from '../hooks/useDocuments'
import { useNLP } from '../hooks/useNLP'
import { usePrefs } from '../context/PreferencesContext'
import { useToast } from '../hooks/useToast.js'
import { useTTSPlayer } from '../hooks/useTTSPlayer'
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

/**
 * F32 — what to call a "Save as new" copy.
 *
 * Two rows with the same name are the one thing a document list must not have:
 * the whole point of the library is telling them apart. Copying a saved
 * document therefore marks the copy, while a page that was never saved keeps
 * the name as typed, and an untouched default hands naming to the server (which
 * dates it).
 */
function copyTitle(title, existingDocumentId) {
  const base = title.trim()
  if (!base || base === DEFAULT_TITLE) return null
  if (!existingDocumentId) return base

  const suffix = ' (copy)'
  const room = MAX_TITLE_CHARS - suffix.length
  return `${base.length > room ? base.slice(0, room).trimEnd() : base}${suffix}`
}

/**
 * F47 — the last sentence that actually finished. Anything after the final
 * terminator is still being written, so reading it back would cut off
 * mid-thought; with nothing finished yet the whole text is the best we have.
 */
function lastCompleteSentence(text) {
  const sentences = text.match(/[^.!?]+[.!?]+/g)
  return (sentences ? sentences[sentences.length - 1] : text).trim()
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

  const { toast, showToast, hideToast } = useToast()

  const [title, setTitle] = useState(DEFAULT_TITLE)
  const [content, setContent] = useState('')
  // F48 — which scaffold this document was started from, saved alongside it.
  const [template, setTemplate] = useState(null)
  // 'loading' → still fetching (including automatic retries)
  // 'ready'   → baseline known, autosave armed
  // 'failed'  → out of automatic retries, waiting on the user
  const [loadState, setLoadState] = useState('loading')
  const [attempt, setAttempt] = useState(0)

  const ready = loadState === 'ready'

  const { documentId, dirty, saving, error, lastSavedAt, saveNow, adoptDocument } = useAutosave({
    title,
    content,
    template,
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
          setTemplate(doc.template ?? null)
        }
        // With nothing saved yet the baseline is the blank page as rendered,
        // otherwise the default title alone would read as an unsaved change.
        adoptDocument(doc ?? { id: null, title: DEFAULT_TITLE, content: '', template: null })
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

  const notepadRef = useRef(null)

  // Prediction is about the caret, not the document, so it has to follow the
  // cursor as well as the text — clicking into the middle of a sentence should
  // predict from there rather than from the end.
  const [caret, setCaret] = useState(0)
  const trackCaret = useCallback(event => setCaret(event.target.selectionStart), [])
  const pendingCaretRef = useRef(null)

  /* ── Named documents (F32) ── */
  const library = useDocuments()
  const { refresh: refreshLibrary } = library
  const [libraryOpen, setLibraryOpen] = useState(false)
  // True while a document is being opened, created or deleted — the editor
  // content is about to change, so the library's buttons stand down.
  const [switching, setSwitching] = useState(false)

  const toggleLibrary = useCallback(() => {
    setLibraryOpen(open => {
      if (!open) refreshLibrary()
      return !open
    })
  }, [refreshLibrary])

  /** Point the notepad at a document (or at a blank page when given null). */
  const loadIntoNotepad = useCallback(
    doc => {
      const next = doc ?? { id: null, title: DEFAULT_TITLE, content: '', template: null }
      setTitle(next.title)
      setContent(next.content)
      setTemplate(next.template ?? null)
      setCaret(0)
      adoptDocument(next)
    },
    [adoptDocument]
  )

  // Anything that replaces what's in the notepad has to get the current work to
  // the server first — autosave's own tick may be up to 30s away.
  const flushBeforeSwitching = useCallback(async () => {
    const saved = await saveNow()
    if (!saved) {
      showToast("Couldn't save what's open, so nothing was changed. Try again.", 'error')
    }
    return saved
  }, [saveNow, showToast])

  const openDocument = useCallback(
    async doc => {
      if (doc.id === documentId) return
      setSwitching(true)
      try {
        if (!(await flushBeforeSwitching())) return
        loadIntoNotepad(await library.fetchDocument(doc.id))
        setLibraryOpen(false)
        showToast(`Opened “${doc.title}”.`, 'success')
      } catch (err) {
        showToast(err?.message || 'Could not open that document.', 'error')
        refreshLibrary() // it may have been deleted from another tab
      } finally {
        setSwitching(false)
      }
    },
    [documentId, flushBeforeSwitching, library, loadIntoNotepad, refreshLibrary, showToast]
  )

  const startNewDocument = useCallback(async () => {
    setSwitching(true)
    try {
      if (!(await flushBeforeSwitching())) return
      loadIntoNotepad(null)
      setLibraryOpen(false)
      showToast('Started a new document.', 'info')
    } finally {
      setSwitching(false)
    }
  }, [flushBeforeSwitching, loadIntoNotepad, showToast])

  /** "Save as new" — keep a separate copy and carry on editing that copy. */
  const saveAsNewDocument = useCallback(async () => {
    if (!content.trim()) {
      showToast('Write something first — an empty document has nothing to save.', 'info')
      return
    }
    setSwitching(true)
    try {
      // The document being edited keeps whatever it had; only the copy is new.
      if (!(await flushBeforeSwitching())) return
      const copy = await library.createDocument({ title: copyTitle(title, documentId), content, template })
      loadIntoNotepad(copy)
      await refreshLibrary()
      showToast(`Saved as “${copy.title}”.`, 'success')
    } catch (err) {
      showToast(err?.message || 'Could not save a copy.', 'error')
    } finally {
      setSwitching(false)
    }
  }, [
    content,
    documentId,
    flushBeforeSwitching,
    library,
    loadIntoNotepad,
    refreshLibrary,
    showToast,
    template,
    title,
  ])

  const deleteDocument = useCallback(
    async doc => {
      setSwitching(true)
      try {
        await library.deleteDocument(doc.id)
        // Deleting what's open would otherwise leave the page saving into a row
        // that no longer exists; start it on a clean page instead.
        if (doc.id === documentId) loadIntoNotepad(null)
        showToast(`Deleted “${doc.title}”.`, 'info')
      } catch (err) {
        showToast(err?.message || 'Could not delete that document.', 'error')
        refreshLibrary()
      } finally {
        setSwitching(false)
      }
    },
    [documentId, library, loadIntoNotepad, refreshLibrary, showToast]
  )

  const checks = useNLP(content, { enabled: ready })

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

  /* ── Apply a structure template (F48) ── */
  // `mode` comes from TemplateSelector, which asks the writer before it can be
  // 'replace' on a page that already has text.
  const applyTemplate = useCallback(
    (id, scaffold, mode) => {
      const keep = mode === 'append' && content.trim() ? `${content.replace(/\s+$/, '')}\n\n` : ''
      const next = keep + scaffold

      setContent(next)
      setTemplate(id)
      // Land the caret at the end of the scaffold's first line — the title or
      // subject — rather than at the very end, which is the bottom of the page.
      const firstLineEnd = scaffold.indexOf('\n')
      const caretAt = keep.length + (firstLineEnd >= 0 ? firstLineEnd : scaffold.length)
      setCaret(caretAt)
      pendingCaretRef.current = caretAt
    },
    [content]
  )

  /* ── Read aloud · F30 selection, F47 Alt+R ── */
  // Playback only, so the word-by-word callback the reading page uses to move
  // its highlight has nothing to do here.
  const {
    play: playTTS,
    stop: stopTTS,
    isPlaying: reading,
    isLoading: preparingAudio,
    error: ttsError,
  } = useTTSPlayer(useCallback(() => {}, []))

  const readAloud = useCallback(() => {
    const textarea = notepadRef.current
    // A selection is an explicit "read this"; without one, fall back to the
    // last finished sentence (AC-37).
    const selected =
      textarea && textarea.selectionStart !== textarea.selectionEnd
        ? content.slice(textarea.selectionStart, textarea.selectionEnd).trim()
        : ''
    const passage = selected || lastCompleteSentence(content)

    if (!passage) {
      showToast('Write something first, then press Alt+R to hear it.', 'info')
      return
    }
    playTTS(passage, 1.0, prefs.phrasePauses)
  }, [content, playTTS, prefs.phrasePauses, showToast])

  useEffect(() => {
    const handler = event => {
      // e.key is layout-dependent (and not always 'r' with Alt held), so accept
      // the physical key too.
      if (!event.altKey || event.ctrlKey || event.metaKey) return
      if (event.code !== 'KeyR' && event.key?.toLowerCase() !== 'r') return
      event.preventDefault()
      readAloud()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [readAloud])

  // Leaving the page must not leave a voice talking over the next one.
  useEffect(() => stopTTS, [stopTTS])

  useEffect(() => {
    if (ttsError) showToast(`Could not read that aloud: ${ttsError}`, 'error')
  }, [ttsError, showToast])

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

          {/* Document library (F32) */}
          {libraryOpen && (
            <DocumentList
              documents={library.documents}
              loading={library.loading}
              error={library.error}
              currentId={documentId}
              busy={switching}
              onOpen={openDocument}
              onDelete={deleteDocument}
              onRefresh={refreshLibrary}
              onClose={() => setLibraryOpen(false)}
            />
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

          {/* Document actions (F32) */}
          <div className="mb-4 flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={toggleLibrary}
              disabled={!ready}
              aria-expanded={libraryOpen}
              className="rounded-xl border border-gray-200 px-4 py-2 text-xs font-bold
                         text-gray-700 hover:border-gray-300 hover:bg-gray-50
                         disabled:opacity-50 focus-visible:outline-2
                         focus-visible:outline-offset-2 focus-visible:outline-blue-500
                         dark:border-gray-700 dark:text-gray-200 dark:hover:bg-gray-800"
            >
              {libraryOpen ? 'Hide documents' : 'My documents'}
            </button>
            <button
              type="button"
              onClick={startNewDocument}
              disabled={!ready || switching}
              className="rounded-xl border border-gray-200 px-4 py-2 text-xs font-bold
                         text-gray-700 hover:border-gray-300 hover:bg-gray-50
                         disabled:opacity-50 focus-visible:outline-2
                         focus-visible:outline-offset-2 focus-visible:outline-blue-500
                         dark:border-gray-700 dark:text-gray-200 dark:hover:bg-gray-800"
            >
              New document
            </button>
            <button
              type="button"
              onClick={saveAsNewDocument}
              disabled={!ready || switching || !content.trim()}
              title="Keep a separate copy under its own name"
              className="rounded-xl border border-gray-200 px-4 py-2 text-xs font-bold
                         text-gray-700 hover:border-gray-300 hover:bg-gray-50
                         disabled:opacity-50 focus-visible:outline-2
                         focus-visible:outline-offset-2 focus-visible:outline-blue-500
                         dark:border-gray-700 dark:text-gray-200 dark:hover:bg-gray-800"
            >
              Save as new
            </button>
          </div>

          {/* Structure templates (F48) + read-back (F30, F47) */}
          <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
            <TemplateSelector
              value={template}
              hasContent={Boolean(content.trim())}
              disabled={!ready}
              onApply={applyTemplate}
            />

            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={reading ? stopTTS : readAloud}
                disabled={!ready || preparingAudio}
                aria-keyshortcuts="Alt+R"
                title="Reads your selection, or the last complete sentence (Alt+R)"
                className="rounded-xl border border-gray-200 px-4 py-2 text-xs font-bold
                           text-gray-700 hover:border-gray-300 hover:bg-gray-50
                           disabled:opacity-50 focus-visible:outline-2
                           focus-visible:outline-offset-2 focus-visible:outline-blue-500
                           dark:border-gray-700 dark:text-gray-200 dark:hover:bg-gray-800"
              >
                {preparingAudio ? 'Preparing…' : reading ? 'Stop reading' : 'Read aloud'}
              </button>
              <span className="text-[11px] text-gray-400 dark:text-gray-500">Alt+R</span>
            </div>
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

      {toast && <Toast message={toast.message} type={toast.type} onClose={hideToast} />}
    </main>
  )
}
