/**
 * F29 word prediction · v5.0 phrase completion.
 *
 * Sits directly under the notepad so the pills are near the caret rather than
 * off in the sidebar with the corrections — these are things to reach for while
 * writing, not things to review afterwards.
 *
 * The three word pills hold their places even while empty, so the bar doesn't
 * change height on every keystroke and shove the notepad around. The phrase pill
 * only appears when there is a phrase worth offering.
 */
export default function SuggestionBar({ words, phrase, predicting, onInsert }) {
  const slots = [0, 1, 2]

  return (
    <div
      className="mt-3 flex flex-wrap items-center gap-2"
      role="group"
      aria-label="Writing suggestions"
    >
      <span className="text-[11px] font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500">
        Next
      </span>

      {slots.map(slot => {
        const word = words[slot]
        return word ? (
          <button
            key={slot}
            type="button"
            onClick={() => onInsert(word)}
            className="rounded-full border border-blue-200 bg-blue-50 px-3 py-1.5 text-sm
                       font-semibold text-blue-800 transition-colors hover:bg-blue-100
                       focus-visible:outline-2 focus-visible:outline-offset-2
                       focus-visible:outline-blue-500
                       dark:border-blue-900 dark:bg-blue-950/40 dark:text-blue-200
                       dark:hover:bg-blue-900/50"
          >
            {word}
          </button>
        ) : (
          // Keeps the row's height and rhythm steady between predictions.
          <span
            key={slot}
            aria-hidden="true"
            className="h-[34px] w-20 rounded-full border border-dashed border-gray-200
                       dark:border-gray-800"
          />
        )
      })}

      {phrase && (
        <button
          type="button"
          onClick={() => onInsert(phrase)}
          title="Insert this whole phrase"
          className="max-w-full truncate rounded-full border border-purple-200 bg-purple-50
                     px-3 py-1.5 text-sm font-medium text-purple-800 transition-colors
                     hover:bg-purple-100 focus-visible:outline-2
                     focus-visible:outline-offset-2 focus-visible:outline-purple-500
                     dark:border-purple-900 dark:bg-purple-950/40 dark:text-purple-200
                     dark:hover:bg-purple-900/50"
        >
          {phrase}
        </button>
      )}

      {predicting && (
        <span className="text-xs text-gray-400 dark:text-gray-500" aria-live="polite">
          thinking…
        </span>
      )}
    </div>
  )
}
