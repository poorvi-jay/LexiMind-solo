/**
 * F26-F28 — the writing notepad's suggestion sidebar.
 *
 * Applying a suggestion is a plain span replacement, so it is only offered
 * while the results match the text on screen; once the user types again the
 * offsets are stale and the buttons wait for the next check.
 */

const TYPE_STYLES = {
  spelling: {
    label: 'Spelling',
    chip: 'bg-rose-100 text-rose-800 dark:bg-rose-950/60 dark:text-rose-200',
    border: 'border-rose-200 dark:border-rose-900',
  },
  grammar: {
    label: 'Grammar',
    chip: 'bg-violet-100 text-violet-800 dark:bg-violet-950/60 dark:text-violet-200',
    border: 'border-violet-200 dark:border-violet-900',
  },
  homophone: {
    label: 'Word choice',
    chip: 'bg-amber-100 text-amber-900 dark:bg-amber-950/60 dark:text-amber-200',
    border: 'border-amber-200 dark:border-amber-900',
  },
}

function Summary({ counts }) {
  const parts = [
    ['spelling', counts.spelling],
    ['grammar', counts.grammar],
    ['homophone', counts.homophone],
  ].filter(([, n]) => n > 0)

  if (parts.length === 0) return null

  return (
    <div className="flex flex-wrap gap-2">
      {parts.map(([type, n]) => (
        <span
          key={type}
          className={`rounded-full px-2.5 py-1 text-xs font-semibold ${TYPE_STYLES[type].chip}`}
        >
          {n} {TYPE_STYLES[type].label.toLowerCase()}
        </span>
      ))}
    </div>
  )
}

/**
 * The 1-based line the issue sits on, counted the way an editor counts them —
 * by newlines, not by where the text happens to wrap at this window width.
 */
function lineNumberOf(text, offset) {
  let line = 1
  for (let i = 0; i < offset && i < text.length; i++) {
    if (text[i] === '\n') line++
  }
  return line
}

export default function WritingChecks({
  issues,
  counts,
  checking,
  stale,
  grammarAvailable,
  checksAvailable,
  error,
  hasText,
  text,
  onApply,
  onLocate,
}) {
  return (
    <aside
      className="space-y-4 rounded-3xl border border-gray-200 bg-white p-5 shadow-sm
                 dark:border-gray-800 dark:bg-[#2A2A2A]"
      aria-label="Writing suggestions"
    >
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-sm font-bold text-gray-900 dark:text-white">
          Suggestions
        </h2>
        <span
          className="text-xs font-semibold text-gray-400 dark:text-gray-500"
          aria-live="polite"
        >
          {checking ? 'Checking…' : stale && hasText ? 'Waiting…' : ''}
        </span>
      </div>

      <Summary counts={counts} />

      {checksAvailable === false ? (
        <p className="rounded-xl bg-amber-50 p-3 text-xs text-amber-800 dark:bg-amber-950/40 dark:text-amber-200">
          Writing checks are unavailable right now. Your work still saves
          normally, and word suggestions keep working.
        </p>
      ) : (
        !grammarAvailable && (
          <p className="rounded-xl bg-gray-50 p-3 text-xs text-gray-500 dark:bg-[#333] dark:text-gray-400">
            Grammar checking is unavailable — spelling and word choice are still
            being checked.
          </p>
        )
      )}

      {error && (
        <p
          className="rounded-xl bg-red-50 p-3 text-xs text-red-700
                     dark:bg-red-950/40 dark:text-red-300"
          role="alert"
        >
          {error}
        </p>
      )}

      {!hasText && (
        <p className="text-xs text-gray-400 dark:text-gray-500">
          Start writing and suggestions will appear here.
        </p>
      )}

      {hasText && issues.length === 0 && !checking && !stale && !error && (
        <p className="text-xs font-semibold text-green-700 dark:text-green-400">
          ✓ Nothing to fix — this reads cleanly.
        </p>
      )}

      <ul className="space-y-3">
        {issues.map(issue => {
          const style = TYPE_STYLES[issue.type]
          return (
            <li
              key={`${issue.type}-${issue.start}-${issue.end}`}
              className={`rounded-2xl border p-3 ${style.border}`}
            >
              <div className="mb-1.5 flex items-center gap-2">
                <span className={`rounded-full px-2 py-0.5 text-[11px] font-bold ${style.chip}`}>
                  {style.label}
                </span>
                {/* Selects the word in the notepad, so a long document doesn't
                    leave the reader hunting for the highlight. */}
                <button
                  type="button"
                  onClick={() => onLocate(issue)}
                  disabled={stale}
                  title={stale ? 'Waiting for the next check' : 'Find this in your writing'}
                  className="truncate text-xs font-semibold text-gray-900 underline
                             decoration-dotted underline-offset-2 hover:text-blue-700
                             disabled:cursor-not-allowed disabled:no-underline
                             disabled:opacity-60 focus-visible:outline-2
                             focus-visible:outline-offset-2 focus-visible:outline-blue-500
                             dark:text-white dark:hover:text-blue-300"
                >
                  {issue.text}
                </button>
                <span className="ml-auto shrink-0 text-[11px] font-semibold text-gray-400 dark:text-gray-500">
                  Line {lineNumberOf(text, issue.start)}
                </span>
              </div>

              <p className="text-xs leading-relaxed text-gray-600 dark:text-gray-300">
                {issue.message}
              </p>

              {issue.suggestions.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {issue.suggestions.map(suggestion => (
                    <button
                      key={suggestion}
                      type="button"
                      onClick={() => onApply(issue, suggestion)}
                      disabled={stale}
                      title={stale ? 'Waiting for the next check' : `Replace with "${suggestion}"`}
                      className="rounded-lg border border-gray-200 bg-gray-50 px-2.5 py-1
                                 text-xs font-semibold text-gray-700 transition-colors
                                 hover:border-blue-300 hover:bg-blue-50 hover:text-blue-800
                                 disabled:cursor-not-allowed disabled:opacity-40
                                 focus-visible:outline-2 focus-visible:outline-offset-2
                                 focus-visible:outline-blue-500
                                 dark:border-gray-700 dark:bg-[#333] dark:text-gray-200
                                 dark:hover:border-blue-600 dark:hover:bg-blue-950/40"
                    >
                      {suggestion}
                    </button>
                  ))}
                </div>
              )}
            </li>
          )
        })}
      </ul>
    </aside>
  )
}
