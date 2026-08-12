import { useState } from 'react'

/**
 * F32 — the library panel above the notepad.
 *
 * Deleting is two-step and inline rather than a `window.confirm`: the row
 * itself turns into the question, so what is about to be deleted stays visible
 * while the choice is made, and the rest of the page keeps working.
 */

const TEMPLATE_LABELS = { essay: 'Essay', email: 'Email', report: 'Report' }

function savedWhen(iso) {
  const saved = new Date(iso)
  const today = new Date()
  const sameDay = saved.toDateString() === today.toDateString()
  return sameDay
    ? `today at ${saved.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`
    : saved.toLocaleDateString([], { day: 'numeric', month: 'short', year: 'numeric' })
}

export default function DocumentList({
  documents,
  loading,
  error,
  currentId,
  busy,
  onOpen,
  onDelete,
  onRefresh,
  onClose,
}) {
  const [confirming, setConfirming] = useState(null)

  return (
    <section
      className="mb-4 rounded-2xl border border-gray-200 bg-gray-50/80 p-4
                 dark:border-gray-800 dark:bg-black/20"
      aria-label="Your saved documents"
    >
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <h2 className="flex-1 text-sm font-bold text-gray-900 dark:text-white">
          Your documents
          {documents.length > 0 && (
            <span className="ml-2 font-medium text-gray-400 dark:text-gray-500">
              {documents.length}
            </span>
          )}
        </h2>
        <button
          type="button"
          onClick={onRefresh}
          disabled={loading}
          className="rounded-xl border border-gray-200 px-3 py-1.5 text-xs font-bold
                     text-gray-600 hover:bg-white disabled:opacity-50
                     focus-visible:outline-2 focus-visible:outline-offset-2
                     focus-visible:outline-blue-500 dark:border-gray-700
                     dark:text-gray-300 dark:hover:bg-gray-800"
        >
          {loading ? 'Loading…' : 'Refresh'}
        </button>
        <button
          type="button"
          onClick={onClose}
          className="rounded-xl px-3 py-1.5 text-xs font-bold text-gray-500 underline
                     focus-visible:outline-2 focus-visible:outline-offset-2
                     focus-visible:outline-blue-500 dark:text-gray-400"
        >
          Close
        </button>
      </div>

      {error && (
        <p
          className="rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-800
                     dark:border-red-900 dark:bg-red-950/40 dark:text-red-200"
          role="alert"
        >
          {error}
        </p>
      )}

      {!error && !loading && documents.length === 0 && (
        <p className="text-sm text-gray-500 dark:text-gray-400">
          Nothing saved yet. Whatever you write here is kept automatically, and
          "Save as new" keeps a separate copy you can come back to.
        </p>
      )}

      <ul className="flex flex-col gap-2">
        {documents.map(doc => {
          const isCurrent = doc.id === currentId
          return (
            <li
              key={doc.id}
              className={`flex flex-wrap items-center gap-3 rounded-xl border p-3
                          ${
                            isCurrent
                              ? 'border-blue-300 bg-blue-50 dark:border-blue-800 dark:bg-blue-950/30'
                              : 'border-gray-200 bg-white dark:border-gray-800 dark:bg-[#2A2A2A]'
                          }`}
            >
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-semibold text-gray-900 dark:text-white">
                  {doc.title}
                  {isCurrent && (
                    <span className="ml-2 text-xs font-bold uppercase tracking-wide text-blue-700 dark:text-blue-300">
                      open now
                    </span>
                  )}
                </p>
                <p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">
                  {doc.word_count.toLocaleString()}{' '}
                  {doc.word_count === 1 ? 'word' : 'words'} · saved {savedWhen(doc.updated_at)}
                  {doc.template && ` · ${TEMPLATE_LABELS[doc.template] || doc.template}`}
                </p>
              </div>

              {confirming === doc.id ? (
                <div className="flex items-center gap-2" role="alert">
                  <span className="text-xs font-semibold text-red-700 dark:text-red-300">
                    Delete for good?
                  </span>
                  <button
                    type="button"
                    onClick={() => {
                      setConfirming(null)
                      onDelete(doc)
                    }}
                    disabled={busy}
                    className="rounded-xl bg-red-600 px-3 py-1.5 text-xs font-bold text-white
                               hover:bg-red-700 disabled:opacity-50 focus-visible:outline-2
                               focus-visible:outline-offset-2 focus-visible:outline-red-500"
                  >
                    Delete
                  </button>
                  <button
                    type="button"
                    onClick={() => setConfirming(null)}
                    className="rounded-xl px-3 py-1.5 text-xs font-bold underline
                               focus-visible:outline-2 focus-visible:outline-offset-2
                               focus-visible:outline-blue-500"
                  >
                    Keep
                  </button>
                </div>
              ) : (
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => onOpen(doc)}
                    disabled={busy || isCurrent}
                    aria-label={`Open ${doc.title}`}
                    className="rounded-xl bg-blue-600 px-3 py-1.5 text-xs font-bold text-white
                               hover:bg-blue-700 disabled:opacity-40 focus-visible:outline-2
                               focus-visible:outline-offset-2 focus-visible:outline-blue-500"
                  >
                    Open
                  </button>
                  <button
                    type="button"
                    onClick={() => setConfirming(doc.id)}
                    disabled={busy}
                    aria-label={`Delete ${doc.title}`}
                    className="rounded-xl border border-gray-200 px-3 py-1.5 text-xs font-bold
                               text-gray-600 hover:border-red-300 hover:text-red-700
                               disabled:opacity-50 focus-visible:outline-2
                               focus-visible:outline-offset-2 focus-visible:outline-red-500
                               dark:border-gray-700 dark:text-gray-300"
                  >
                    Delete
                  </button>
                </div>
              )}
            </li>
          )
        })}
      </ul>
    </section>
  )
}
