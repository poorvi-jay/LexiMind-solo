import { useCallback, useMemo, useRef } from 'react'

/**
 * The notepad textarea with its flagged words marked underneath.
 *
 * A textarea can't render styled spans, so this is the standard mirror trick: a
 * div behind the textarea holds the same text in the same typography, drawing
 * tints and wavy underlines, while the textarea itself is transparent and sits
 * on top. Every box property that affects line wrapping has to match between
 * the two or the marks drift away from the words:
 *   - the shared `style` carries font, size, line height and word spacing
 *   - both boxes use the same padding and a 1px border (transparent on the
 *     mirror), so their content boxes are identical
 *   - `scrollbar-gutter: stable` reserves the scrollbar's width on both, so a
 *     long document doesn't narrow the textarea and re-wrap it out of step
 * Scrolling is forwarded from the textarea to the mirror on every scroll event.
 */

const HIGHLIGHT_CLASSES = {
  spelling:
    'rounded-sm bg-rose-200/60 underline decoration-rose-500 decoration-wavy decoration-2 underline-offset-4 dark:bg-rose-500/30 dark:decoration-rose-300',
  grammar:
    'rounded-sm bg-violet-200/60 underline decoration-violet-500 decoration-wavy decoration-2 underline-offset-4 dark:bg-violet-500/30 dark:decoration-violet-300',
  homophone:
    'rounded-sm bg-amber-200/70 underline decoration-amber-500 decoration-wavy decoration-2 underline-offset-4 dark:bg-amber-500/30 dark:decoration-amber-300',
}

/** Split the text into plain runs and flagged runs, in document order. */
function buildSegments(text, issues) {
  const ordered = [...issues].sort((a, b) => a.start - b.start)
  const segments = []
  let cursor = 0

  for (const issue of ordered) {
    // Overlapping spans would double-mark the same characters; the first one
    // wins, which matches the order the sidebar lists them in.
    if (issue.start < cursor || issue.end > text.length) continue
    if (issue.start > cursor) {
      segments.push({ key: `plain-${cursor}`, text: text.slice(cursor, issue.start) })
    }
    segments.push({
      key: `${issue.type}-${issue.start}`,
      text: text.slice(issue.start, issue.end),
      type: issue.type,
    })
    cursor = issue.end
  }

  if (cursor < text.length) {
    segments.push({ key: `plain-${cursor}`, text: text.slice(cursor) })
  }
  return segments
}

export default function HighlightedEditor({
  id,
  textareaRef,
  value,
  onChange,
  disabled,
  placeholder,
  maxLength,
  issues,
  showHighlights,
  style,
  background,
}) {
  const mirrorRef = useRef(null)

  const segments = useMemo(
    () => buildSegments(value, showHighlights ? issues : []),
    [value, issues, showHighlights]
  )

  const syncScroll = useCallback(event => {
    const mirror = mirrorRef.current
    if (!mirror) return
    mirror.scrollTop = event.target.scrollTop
    mirror.scrollLeft = event.target.scrollLeft
  }, [])

  return (
    <div
      className="relative rounded-2xl shadow-inner"
      style={{ backgroundColor: background }}
    >
      <div
        ref={mirrorRef}
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 overflow-hidden whitespace-pre-wrap
                   break-words rounded-2xl border border-transparent p-5 text-transparent"
        style={style}
      >
        {segments.map(segment =>
          segment.type ? (
            <span key={segment.key} className={HIGHLIGHT_CLASSES[segment.type]}>
              {segment.text}
            </span>
          ) : (
            <span key={segment.key}>{segment.text}</span>
          )
        )}
        {/* A trailing newline would otherwise collapse and shorten the mirror. */}
        {'\n'}
      </div>

      {/* `block` matters: as an inline-block the textarea leaves a baseline gap
          underneath, so the wrapper grows taller than it and shows a strip of
          background below the rounded border. */}
      <textarea
        id={id}
        ref={textareaRef}
        value={value}
        maxLength={maxLength}
        onChange={onChange}
        onScroll={syncScroll}
        disabled={disabled}
        placeholder={placeholder}
        spellCheck="false"
        className="relative block min-h-[24rem] w-full resize-y rounded-2xl border
                   border-gray-200 bg-transparent p-5 text-gray-900
                   focus:border-blue-400 focus:outline-none disabled:opacity-60
                   dark:border-gray-700 dark:text-white"
        style={style}
      />
    </div>
  )
}
