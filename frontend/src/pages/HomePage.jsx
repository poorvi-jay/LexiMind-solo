import { Link } from 'react-router-dom'
import { useEffect } from 'react'

import { useWordBankStats } from '../hooks/useWordBankStats'

/* ─── Feature cards (user-facing language, not technical) ─── */
const FEATURES = [
  {
    icon: '📷',
    title: 'Scan notes',
    description: 'Upload photos or PDFs of your notes and extract the text automatically.',
  },
  {
    icon: '✨',
    title: 'Simplify text',
    description: 'Rewrite difficult passages in simpler, easier-to-read language.',
  },
  {
    icon: '🔊',
    title: 'Listen while you read',
    description: 'Hear text read aloud with word-by-word highlighting that follows along.',
  },
  {
    icon: '📖',
    title: 'Tap words for meanings',
    description: 'Touch any difficult word to see its definition, pronunciation, and examples.',
  },
]

/* ─── Accessibility benefit cards ─── */
const BENEFITS = [
  {
    icon: '🧘',
    title: 'Reduce reading stress',
    description: 'Calmer layout, comfortable fonts, and adjustable spacing.',
  },
  {
    icon: '🧠',
    title: 'Improve comprehension',
    description: 'Simplification and definitions help you understand more.',
  },
  {
    icon: '🎧',
    title: 'Listen and read together',
    description: 'Audio playback with synced highlighting reinforces learning.',
  },
  {
    icon: '⚙️',
    title: 'Personalise your experience',
    description: 'Choose fonts, colours, spacing, and overlays that work for you.',
  },
]

export default function HomePage() {
  // F51's reminder. Null when logged out, so the card simply never renders.
  const { stats, refresh } = useWordBankStats()
  useEffect(() => {
    refresh()
  }, [refresh])

  const dueCount = stats?.words_due_today ?? 0

  return (
    <main className="min-h-screen bg-gray-50 px-4 py-12 dark:bg-[#1E1E1E] sm:px-6">
      {/* ═══ F51 · practice reminder ═══ */}
      {dueCount > 0 && (
        <section
          className="mx-auto mb-10 flex max-w-6xl flex-wrap items-center justify-between gap-4
                     rounded-2xl border border-blue-200 bg-blue-50 p-5
                     dark:border-blue-900 dark:bg-blue-950/40"
          aria-labelledby="practice-reminder-heading"
        >
          <div>
            <h2
              id="practice-reminder-heading"
              className="text-base font-semibold text-blue-900 dark:text-blue-100"
            >
              {dueCount} {dueCount === 1 ? 'word is' : 'words are'} ready to practise
            </h2>
            <p className="mt-1 text-sm text-blue-800/80 dark:text-blue-200/80">
              {stats.due_words.join(' · ')}
              {dueCount > stats.due_words.length && ' …'}
            </p>
          </div>
          <Link
            to="/wordbank/drill"
            className="rounded-2xl bg-blue-600 px-5 py-2.5 text-sm font-semibold text-white
                       hover:bg-blue-700 focus-visible:outline-2 focus-visible:outline-offset-2
                       focus-visible:outline-blue-500"
          >
            Start drill
          </Link>
        </section>
      )}

      {/* ═══ Hero Section ═══ */}
      <section
        className="mx-auto grid max-w-6xl gap-10 lg:grid-cols-[1.05fr_0.95fr] lg:items-center"
        aria-labelledby="hero-heading"
      >
        <div>
          <p className="text-sm font-semibold uppercase tracking-wide text-blue-600 dark:text-blue-300">
            LexiMind AI
          </p>
          <h1
            id="hero-heading"
            className="mt-4 text-4xl font-bold tracking-tight text-gray-950 dark:text-white sm:text-5xl"
          >
            A calmer way to read difficult text.
          </h1>
          <p className="mt-5 max-w-2xl text-lg leading-relaxed text-gray-600 dark:text-gray-300">
            Scan your notes, simplify dense passages, listen with synced
            highlighting, and look up difficult words — all in one quiet
            reading space designed for the way you learn.
          </p>
          <div className="mt-8 flex flex-wrap gap-3">
            <Link
              to="/reading"
              className="rounded-2xl bg-blue-600 px-5 py-3 text-sm font-semibold text-white
                         shadow-lg shadow-blue-200 hover:bg-blue-700
                         focus-visible:outline-2 focus-visible:outline-offset-2
                         focus-visible:outline-blue-500 dark:shadow-none"
            >
              Start reading
            </Link>
            <Link
              to="/settings"
              className="rounded-2xl border border-gray-200 bg-white px-5 py-3 text-sm
                         font-semibold text-gray-700 hover:bg-gray-50
                         focus-visible:outline-2 focus-visible:outline-offset-2
                         focus-visible:outline-blue-500
                         dark:border-gray-700 dark:bg-[#2A2A2A] dark:text-gray-200"
            >
              Adjust settings
            </Link>
          </div>
        </div>

        {/* ── Feature cards panel ── */}
        <div className="rounded-[2rem] border border-gray-200 bg-white p-6 shadow-xl dark:border-gray-800 dark:bg-[#2A2A2A]">
          <div className="rounded-3xl bg-blue-50 p-5 dark:bg-blue-950/40">
            <p className="text-sm font-semibold text-blue-700 dark:text-blue-200">
              What you can do
            </p>
            <div className="mt-5 space-y-3">
              {FEATURES.map(f => (
                <div
                  key={f.title}
                  className="flex items-start gap-3 rounded-2xl bg-white p-4 text-sm
                              shadow-sm dark:bg-[#333] dark:text-gray-200"
                >
                  <span className="mt-0.5 text-xl" aria-hidden="true">
                    {f.icon}
                  </span>
                  <div>
                    <p className="font-semibold text-gray-800 dark:text-gray-100">
                      {f.title}
                    </p>
                    <p className="mt-0.5 text-xs leading-relaxed text-gray-500 dark:text-gray-400">
                      {f.description}
                    </p>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* ═══ Accessibility Benefits ═══ */}
      <section
        className="mx-auto mt-16 max-w-6xl"
        aria-labelledby="benefits-heading"
      >
        <h2
          id="benefits-heading"
          className="text-center text-sm font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400"
        >
          Designed for accessibility
        </h2>
        <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {BENEFITS.map(b => (
            <div
              key={b.title}
              className="rounded-2xl border border-gray-100 bg-white p-5 text-center
                          shadow-sm dark:border-gray-700 dark:bg-[#2A2A2A]"
            >
              <span className="text-2xl" aria-hidden="true">{b.icon}</span>
              <p className="mt-2 text-sm font-semibold text-gray-800 dark:text-gray-100">
                {b.title}
              </p>
              <p className="mt-1 text-xs leading-relaxed text-gray-500 dark:text-gray-400">
                {b.description}
              </p>
            </div>
          ))}
        </div>
      </section>
    </main>
  )
}