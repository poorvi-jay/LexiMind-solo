import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { useTTSPlayer } from '../hooks/useTTSPlayer'
import { api } from '../utils/api'

/**
 * F50 — the spaced-repetition drill.
 *
 * One word at a time: hear it, try to read it, then say how it went. The grade
 * goes straight to SM-2 on the server, which decides when the word comes back.
 *
 * The five grades map onto SM-2's 0-5 scale, where anything below 3 counts as a
 * lapse and puts the word back to tomorrow. That cliff sits between "Almost"
 * and "Got it", so the wording has to make which side you are on obvious.
 */
const RATINGS = [
  { quality: 1, label: 'No idea', hint: 'Could not read it' },
  { quality: 2, label: 'Almost', hint: 'Got some of it' },
  { quality: 3, label: 'Got it', hint: 'Slowly, with effort' },
  { quality: 4, label: 'Good', hint: 'Read it comfortably' },
  { quality: 5, label: 'Easy', hint: 'Knew it instantly' },
]

const LABEL_COLORS = {
  Easy: 'bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-200',
  Medium: 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/40 dark:text-yellow-200',
  Hard: 'bg-orange-100 text-orange-700 dark:bg-orange-900/40 dark:text-orange-200',
}

// The drill reuses M1's word playback and never follows the sync position.
const ignoreWordChange = () => {}

/** "tomorrow" / "in 6 days" — a date is less useful here than the gap. */
function formatNextReview(iso) {
  const days = Math.round((new Date(iso) - new Date().setHours(0, 0, 0, 0)) / 86_400_000)
  if (days <= 0) return 'again today'
  if (days === 1) return 'tomorrow'
  if (days < 30) return `in ${days} days`
  const months = Math.round(days / 30)
  return months === 1 ? 'in about a month' : `in about ${months} months`
}

export default function WordBankDrillPage() {
  const [state, setState] = useState({ status: 'loading', queue: [] })
  const [index, setIndex] = useState(0)
  const [revealed, setRevealed] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState(null)
  const [done, setDone] = useState([])

  const { playWord } = useTTSPlayer(ignoreWordChange)

  useEffect(() => {
    let active = true
    api
      .get('/wordbank/drill')
      .then(queue => { if (active) setState({ status: 'ready', queue }) })
      .catch(() => { if (active) setState({ status: 'error', queue: [] }) })
    return () => { active = false }
  }, [])

  const current = state.queue[index] ?? null

  // Hearing the word is the point of the drill, so it plays on arrival rather
  // than waiting to be asked.
  useEffect(() => {
    if (current) playWord(current.word)
  }, [current, playWord])

  const grade = useCallback(
    async quality => {
      if (!current || saving) return
      setSaving(true)
      setSaveError(null)
      try {
        const result = await api.post('/wordbank/drill/result', { word: current.word, quality })
        setDone(previous => [...previous, result])
        setIndex(previous => previous + 1)
        setRevealed(false)
      } catch {
        setSaveError('Could not save that answer. Try again.')
      } finally {
        setSaving(false)
      }
    },
    [current, saving],
  )

  const finished = state.status === 'ready' && index >= state.queue.length

  return (
    <main className="min-h-screen bg-gray-50/70 px-4 py-8 dark:bg-[#1E1E1E] sm:px-6">
      <div className="mx-auto max-w-2xl space-y-6">
        <header>
          <p className="text-sm font-semibold uppercase tracking-wide text-blue-600 dark:text-blue-300">
            Word bank
          </p>
          <h1 className="mt-2 text-3xl font-bold tracking-tight text-gray-950 dark:text-white sm:text-4xl">
            Practice the words you replay most.
          </h1>
          <p className="mt-3 text-base leading-relaxed text-gray-600 dark:text-gray-300">
            Words you ask to hear three times join your bank. Each one comes back
            until you know it, then less and less often.
          </p>
        </header>

        {state.status === 'loading' && (
          <p className="rounded-2xl border border-gray-200 bg-white p-6 text-center text-sm
                        text-gray-500 shadow-sm dark:border-gray-800 dark:bg-[#2A2A2A] dark:text-gray-400"
             role="status">
            Loading…
          </p>
        )}

        {state.status === 'error' && (
          <p className="rounded-2xl border border-gray-200 bg-white p-6 text-center text-sm
                        text-gray-500 shadow-sm dark:border-gray-800 dark:bg-[#2A2A2A] dark:text-gray-400"
             role="alert">
            Could not load your drill right now.
          </p>
        )}

        {state.status === 'ready' && state.queue.length === 0 && (
          <div className="rounded-2xl border border-gray-200 bg-white p-8 text-center shadow-sm
                          dark:border-gray-800 dark:bg-[#2A2A2A]">
            <p className="text-lg font-semibold text-gray-900 dark:text-white">
              Nothing to practise today.
            </p>
            <p className="mt-2 text-sm text-gray-500 dark:text-gray-400">
              Words join your bank once you have tapped them three times while reading.
            </p>
            <Link
              to="/reading"
              className="mt-5 inline-block rounded-full bg-blue-600 px-5 py-2.5 text-sm font-semibold
                         text-white transition-colors hover:bg-blue-700
                         focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-500"
            >
              Go to Reading
            </Link>
          </div>
        )}

        {/* ── the card ── */}
        {current && (
          <section
            className="rounded-2xl border border-gray-200 bg-white p-8 shadow-sm
                       dark:border-gray-800 dark:bg-[#2A2A2A]"
            aria-label="Word to practise"
          >
            <div className="flex items-center justify-between gap-3">
              <p className="text-xs font-medium text-gray-500 dark:text-gray-400" aria-live="polite">
                Word {index + 1} of {state.queue.length}
              </p>
              {current.difficulty_label && (
                <span
                  className={`rounded-full px-3 py-1 text-xs font-semibold
                              ${LABEL_COLORS[current.difficulty_label] || 'bg-gray-100 text-gray-700'}`}
                >
                  {current.difficulty_label}
                </span>
              )}
            </div>

            <p className="mt-6 break-words text-center text-4xl font-bold tracking-tight
                          text-gray-950 dark:text-white">
              {revealed ? current.word : '• • •'}
            </p>

            <div className="mt-6 flex flex-wrap justify-center gap-3">
              <button
                type="button"
                onClick={() => playWord(current.word)}
                className="rounded-full border border-gray-200 px-5 py-2.5 text-sm font-semibold
                           text-gray-700 transition-colors hover:text-gray-950
                           focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-500
                           dark:border-gray-700 dark:text-gray-200 dark:hover:text-white"
              >
                ▶ Hear it again
              </button>
              {!revealed && (
                <button
                  type="button"
                  onClick={() => setRevealed(true)}
                  className="rounded-full bg-blue-600 px-5 py-2.5 text-sm font-semibold text-white
                             transition-colors hover:bg-blue-700
                             focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-500"
                >
                  Show the word
                </button>
              )}
            </div>

            {revealed && (
              <div className="mt-8">
                <p className="text-center text-sm font-medium text-gray-600 dark:text-gray-300">
                  How did that go?
                </p>
                <div className="mt-3 grid gap-2 sm:grid-cols-5">
                  {RATINGS.map(rating => (
                    <button
                      key={rating.quality}
                      type="button"
                      disabled={saving}
                      onClick={() => grade(rating.quality)}
                      className="rounded-xl border border-gray-200 px-3 py-3 text-center transition-colors
                                 hover:border-blue-500 hover:bg-blue-50 disabled:opacity-50
                                 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-500
                                 dark:border-gray-700 dark:hover:border-blue-400 dark:hover:bg-blue-900/30"
                    >
                      <span className="block text-sm font-semibold text-gray-900 dark:text-white">
                        {rating.label}
                      </span>
                      <span className="mt-0.5 block text-[11px] leading-tight text-gray-500 dark:text-gray-400">
                        {rating.hint}
                      </span>
                    </button>
                  ))}
                </div>
                {saveError && (
                  <p className="mt-3 text-center text-sm text-red-600 dark:text-red-300" role="alert">
                    {saveError}
                  </p>
                )}
              </div>
            )}
          </section>
        )}

        {/* ── the summary ── */}
        {finished && done.length > 0 && (
          <section
            className="rounded-2xl border border-gray-200 bg-white p-6 shadow-sm
                       dark:border-gray-800 dark:bg-[#2A2A2A]"
            aria-label="Drill finished"
          >
            <h2 className="text-lg font-semibold text-gray-900 dark:text-white">
              Done — {done.length} {done.length === 1 ? 'word' : 'words'} practised.
            </h2>
            <ul className="mt-4 divide-y divide-gray-100 dark:divide-gray-800">
              {done.map(result => (
                <li key={result.word} className="flex items-baseline justify-between gap-3 py-2">
                  <span className="font-semibold text-gray-900 dark:text-white">{result.word}</span>
                  <span className="text-sm text-gray-500 dark:text-gray-400">
                    {result.mastered ? 'mastered · ' : ''}back {formatNextReview(result.next_review)}
                  </span>
                </li>
              ))}
            </ul>
            <Link
              to="/analytics"
              className="mt-5 inline-block text-sm font-semibold text-blue-700 underline-offset-2
                         hover:underline dark:text-blue-300"
            >
              See your progress
            </Link>
          </section>
        )}
      </div>
    </main>
  )
}
