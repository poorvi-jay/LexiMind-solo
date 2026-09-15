import { useMemo } from 'react'

export default function ReadingProgress({ activeIndex, totalWords, durationMs = 0 }) {
  const progress = useMemo(() => {
    if (totalWords <= 0 || activeIndex < 0) return 0
    return Math.min(100, Math.round(((activeIndex + 1) / totalWords) * 100))
  }, [activeIndex, totalWords])

  const remainingLabel = useMemo(() => {
    if (totalWords <= 0 || activeIndex < 0) return null

    const fractionDone = (activeIndex + 1) / totalWords
    if (fractionDone >= 0.98) return 'Almost done'

    let remainMins
    if (durationMs > 0) {
      remainMins = Math.max(0, Math.ceil((durationMs * (1 - fractionDone)) / 60000))
    } else {
      const wordsLeft = totalWords - (activeIndex + 1)
      remainMins = Math.max(0, Math.ceil(wordsLeft / 200))
    }

    if (remainMins <= 0) return 'Almost done'
    return remainMins === 1 ? '~1 min left' : `~${remainMins} mins left`
  }, [activeIndex, totalWords, durationMs])

  if (totalWords <= 0) return null

  return (
    <div
      className="reading-content-area flex items-center gap-4"
      role="status"
      aria-label={`Reading progress: ${progress}% complete`}
    >
      <div className="reading-progress-track flex-1">
        <div
          className="reading-progress-fill"
          style={{ width: `${progress}%` }}
        />
      </div>

      <div className="flex shrink-0 items-center gap-2 text-xs font-medium text-gray-500 dark:text-gray-400">
        <span>{progress}%</span>
        {remainingLabel && (
          <>
            <span aria-hidden="true">·</span>
            <span>{remainingLabel}</span>
          </>
        )}
      </div>
    </div>
  )
}

