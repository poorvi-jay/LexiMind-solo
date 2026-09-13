/**
 * CHANGE #8: Descriptive stat labels instead of bare numbers
 * CHANGE #9: Estimated reading time clearly shown
 *
 * The difficulty level and hard-word percentage were removed deliberately —
 * a difficulty score can discourage a struggling reader. Hard words are still
 * highlighted in the text itself.
 */
export default function ComplexityBadge({ complexity, title = 'This page' }) {
  if (!complexity) return null

  const mins = Math.ceil(complexity.est_reading_time_s / 60)

  const stats = [
    {
      label: 'Estimated time',
      value: mins === 1 ? '~1 minute' : `~${mins} minutes`,
      description: 'to read at a comfortable pace',
    },
    {
      label: 'Word count',
      value: complexity.word_count.toLocaleString(),
      description: 'total words in this text',
    },
  ]

  return (
    <div
      className="surface flex flex-col gap-4 rounded-xl border border-gray-200 bg-white
                  p-4 shadow-sm dark:border-gray-700"
      role="status"
      aria-label={`${title}: ${complexity.word_count} words`}
    >
      <p className="text-xs font-medium uppercase tracking-wide text-gray-400">
        {title}
      </p>

      {/* ── CHANGE #8 + #9: Descriptive stat rows ── */}
      <div className="space-y-2.5">
        {stats.map(stat => (
          <div key={stat.label} className="flex items-baseline justify-between gap-2">
            <div>
              <p className="text-xs font-semibold text-gray-600 dark:text-gray-300">
                {stat.label}
              </p>
              <p className="text-[10px] text-gray-400 dark:text-gray-500">
                {stat.description}
              </p>
            </div>
            <p className="shrink-0 text-sm font-bold tabular-nums text-gray-800 dark:text-gray-100">
              {stat.value}
            </p>
          </div>
        ))}
      </div>
    </div>
  )
}