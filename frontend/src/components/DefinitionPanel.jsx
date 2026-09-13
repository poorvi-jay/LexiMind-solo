import { useCallback, useEffect, useState } from 'react'

import { api } from '../utils/api'

/**
 * Enhanced side panel — shows definition, pronunciation, syllable breakdown,
 * example sentence, and synonyms for a selected word.
 *
 * API response fields (from existing backend):
 *   definition, phonetic, syllables, example
 * We also handle optional: synonyms, pronunciation (alias for phonetic)
 */
export default function DefinitionPanel({ word, onClose, onPlayWord }) {
  const [definition, setDefinition] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  /* ── Fetch definition when word changes ── */
  useEffect(() => {
    if (!word) return

    let cancelled = false

    async function fetchDefinition() {
      setLoading(true)
      setError(null)
      setDefinition(null)
      try {
        const data = await api.post('/reading/define', { word })
        if (!cancelled) setDefinition(data)
      } catch (err) {
        if (!cancelled) setError(err.message)
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    fetchDefinition()

    return () => {
      cancelled = true
    }
  }, [word])

  /* ── Escape key closes panel ── */
  const handleKeyDown = useCallback(
    e => { if (e.key === 'Escape') onClose() },
    [onClose],
  )

  useEffect(() => {
    if (word) {
      document.addEventListener('keydown', handleKeyDown)
      return () => document.removeEventListener('keydown', handleKeyDown)
    }
  }, [word, handleKeyDown])

  if (!word) return null

  return (
    <>
      {/* Semi-transparent backdrop */}
      <div
        className="fixed inset-0 z-40 bg-black/20 dark:bg-black/40"
        onClick={onClose}
        aria-hidden="true"
      />

      {/* Panel */}
      <aside
        className="definition-panel-enter fixed inset-y-0 right-0 z-50 flex w-full
                    max-w-sm flex-col border-l border-gray-200 bg-white shadow-2xl
                    dark:border-gray-700 dark:bg-[#2A2A2A]"
        role="dialog"
        aria-modal="true"
        aria-labelledby="def-panel-title"
      >
        {/* ── Header ── */}
        <div className="flex items-start justify-between gap-4 border-b border-gray-100 px-6 py-5 dark:border-gray-700">
          <div>
            <p className="text-xs font-semibold uppercase tracking-wide text-blue-600 dark:text-blue-300">
              Dictionary
            </p>
            <h3
              id="def-panel-title"
              className="mt-1 text-2xl font-semibold text-gray-900 dark:text-white"
            >
              {word}
            </h3>
            {definition?.phonetic && (
              <p className="mt-1 font-mono text-sm text-gray-500 dark:text-gray-300">
                {definition.phonetic}
              </p>
            )}

            {onPlayWord && (
              <button
                type="button"
                onClick={() => onPlayWord(word)}
                className="mt-3 inline-flex min-h-[44px] items-center gap-2 rounded-xl
                            bg-blue-600 px-4 text-sm font-semibold text-white
                            hover:bg-blue-700 focus-visible:outline-2
                            focus-visible:outline-offset-2 focus-visible:outline-blue-500"
              >
                <span aria-hidden="true">🔊</span> Hear it again
              </button>
            )}
          </div>
          <button
            type="button"
            onClick={onClose}
            className="grid h-9 w-9 place-items-center rounded-full border border-gray-200
                        text-gray-500 hover:bg-gray-50
                        focus-visible:outline-2 focus-visible:outline-blue-500
                        dark:border-gray-700 dark:text-gray-300 dark:hover:bg-gray-700"
            aria-label="Close definition panel"
          >
            ✕
          </button>
        </div>

        {/* ── Body ── */}
        <div className="flex-1 overflow-y-auto px-6 py-5">
          {/* Loading */}
          {loading && (
            <div className="rounded-xl border border-blue-100 bg-blue-50 p-4 text-sm text-blue-700 dark:border-blue-900 dark:bg-blue-950/40 dark:text-blue-200">
              Looking up "{word}"…
            </div>
          )}

          {/* Error */}
          {error && (
            <div className="rounded-xl border border-red-100 bg-red-50 p-4 text-sm text-red-700 dark:border-red-900 dark:bg-red-950/40 dark:text-red-200">
              {error}
            </div>
          )}

          {/* Definition data */}
          {definition && (
            <div className="space-y-5">
              {/* Syllable breakdown — "chlo · ro · plasts" */}
              {(definition.syllable_parts?.length > 0 || !!definition.syllables) && (
                <section className="rounded-xl border border-gray-200 bg-gray-50 p-4 dark:border-gray-700 dark:bg-[#333]">
                  <p className="text-xs font-semibold uppercase tracking-wide text-gray-400">
                    Syllables
                  </p>
                  {definition.syllable_parts?.length > 0 ? (
                    <p className="mt-2 text-xl font-semibold tracking-wide text-gray-800 dark:text-gray-100">
                      {definition.syllable_parts.map((part, i) => (
                        <span key={i}>
                          {i > 0 && <span className="syllable-sep" aria-hidden="true">·</span>}
                          {part}
                        </span>
                      ))}
                      <span className="ml-2 align-middle text-xs font-normal text-gray-400">
                        {definition.syllable_parts.length}{' '}
                        {definition.syllable_parts.length === 1 ? 'syllable' : 'syllables'}
                      </span>
                    </p>
                  ) : (
                    <p className="mt-2 text-base tracking-wider text-gray-800 dark:text-gray-100">
                      {definition.syllables}
                    </p>
                  )}
                </section>
              )}

              {/* Definition / Meaning */}
              {definition.definition && (
                <section className="rounded-xl border border-gray-200 bg-gray-50 p-4 dark:border-gray-700 dark:bg-[#333]">
                  <p className="text-xs font-semibold uppercase tracking-wide text-gray-400">
                    Meaning
                  </p>
                  <p className="mt-2 text-base leading-relaxed text-gray-800 dark:text-gray-100">
                    {definition.definition}
                  </p>
                </section>
              )}

              {/* Example sentence */}
              {definition.example && (
                <section className="rounded-xl border border-gray-200 bg-gray-50 p-4 dark:border-gray-700 dark:bg-[#333]">
                  <p className="text-xs font-semibold uppercase tracking-wide text-gray-400">
                    Example
                  </p>
                  <p className="mt-2 text-sm italic leading-relaxed text-gray-600 dark:text-gray-300">
                    "{definition.example}"
                  </p>
                </section>
              )}

              {/* Synonyms (if returned by API) */}
              {definition.synonyms && definition.synonyms.length > 0 && (
                <section className="rounded-xl border border-gray-200 bg-gray-50 p-4 dark:border-gray-700 dark:bg-[#333]">
                  <p className="text-xs font-semibold uppercase tracking-wide text-gray-400">
                    Synonyms
                  </p>
                  <div className="mt-2 flex flex-wrap gap-2">
                    {definition.synonyms.map(s => (
                      <span
                        key={s}
                        className="rounded-full bg-blue-50 px-3 py-1 text-xs font-medium text-blue-700 dark:bg-blue-900/40 dark:text-blue-200"
                      >
                        {s}
                      </span>
                    ))}
                  </div>
                </section>
              )}

              {/* Fallback when API returns minimal data */}
              {!definition.definition && !definition.phonetic && !definition.syllables && (
                <p className="text-sm text-gray-500 dark:text-gray-400">
                  No detailed definition available for "{word}".
                </p>
              )}
            </div>
          )}
        </div>
      </aside>
    </>
  )
}