/**
 * ‹ Prev · Page 3 of 12 · Next › bar for the reading panel.
 * `pages` are { number, text }; `number` is the real (PDF) page number.
 */
export default function PageNav({ pages, pageIndex, onChange }) {
  if (pages.length <= 1) return null

  const current = pages[pageIndex]
  const isFirst = pageIndex === 0
  const isLast  = pageIndex === pages.length - 1
  const buttonClass =
    'min-h-[44px] rounded-xl border border-gray-200 bg-white px-4 text-sm font-semibold ' +
    'text-gray-700 shadow-sm hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-40 ' +
    'focus-visible:outline-2 focus-visible:outline-blue-500 ' +
    'dark:border-gray-700 dark:bg-[#2A2A2A] dark:text-gray-200 dark:hover:bg-gray-800'

  return (
    <nav
      className="reading-content-area flex items-center justify-between gap-3"
      aria-label="Page navigation"
    >
      <button
        type="button"
        onClick={() => onChange(pageIndex - 1)}
        disabled={isFirst}
        className={buttonClass}
        aria-label="Previous page"
      >
        ‹ Prev
      </button>

      <p className="m-0 text-sm font-semibold text-gray-600 dark:text-gray-300" aria-live="polite">
        Page {current.number}
        <span className="font-normal text-gray-400">
          {' '}· {pageIndex + 1} of {pages.length}
        </span>
      </p>

      <button
        type="button"
        onClick={() => onChange(pageIndex + 1)}
        disabled={isLast}
        className={buttonClass}
        aria-label="Next page"
      >
        Next ›
      </button>
    </nav>
  )
}
