import { useEffect, useMemo, useRef } from 'react'

import { searchPages, splitWords } from '../utils/document'

/**
 * Sidebar for multi-page documents: search across every page, and a page
 * list with a short preview of each page. Clicking a result or page jumps
 * there.
 */
export default function DocumentNavigator({
  pages,
  pageIndex,
  onSelectPage,
  query,
  onQueryChange,
}) {
  const hits = useMemo(() => searchPages(pages, query), [pages, query])
  const activeItemRef = useRef(null)

  // Keep the current page visible in the list as the user pages through.
  useEffect(() => {
    activeItemRef.current?.scrollIntoView({ block: 'nearest' })
  }, [pageIndex])

  const hitsPerPage = useMemo(() => {
    const counts = new Map()
    hits.forEach(h => counts.set(h.pageIndex, (counts.get(h.pageIndex) || 0) + 1))
    return counts
  }, [hits])

  const searching = query.trim().length >= 2

  return (
    <div
      className="flex min-h-0 flex-col rounded-2xl border border-gray-200 bg-white shadow-sm
                  dark:border-gray-800 dark:bg-[#2A2A2A]"
    >
      {/* ── Search ── */}
      <div className="border-b border-gray-100 p-3 dark:border-gray-700">
        <label htmlFor="doc-search" className="sr-only">Search the document</label>
        <div className="relative">
          <input
            id="doc-search"
            type="search"
            value={query}
            onChange={e => onQueryChange(e.target.value)}
            placeholder="Search all pages…"
            className="min-h-[44px] w-full rounded-xl border border-gray-200 bg-gray-50 px-3 pr-9
                        text-sm focus:border-blue-400 focus:outline-none
                        dark:border-gray-700 dark:bg-[#333] dark:text-gray-100"
          />
          <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-gray-400" aria-hidden="true">
            ⌕
          </span>
        </div>
        {searching && (
          <p className="mt-2 text-xs text-gray-500 dark:text-gray-400" aria-live="polite">
            {hits.length === 0
              ? 'No matches'
              : `${hits.length}${hits.length === 50 ? '+' : ''} match${hits.length === 1 ? '' : 'es'} on ${hitsPerPage.size} page${hitsPerPage.size === 1 ? '' : 's'}`}
          </p>
        )}
      </div>

      {/* ── Search results OR page list ── */}
      <div className="max-h-[calc(100vh-18rem)] min-h-0 overflow-y-auto p-2">
        {searching ? (
          <ul className="space-y-1" aria-label="Search results">
            {hits.map((hit, i) => (
              <li key={`${hit.pageIndex}-${i}`}>
                <button
                  type="button"
                  onClick={() => onSelectPage(hit.pageIndex)}
                  className={`w-full rounded-lg px-3 py-2 text-left text-xs leading-relaxed
                    hover:bg-blue-50 dark:hover:bg-blue-950/40
                    ${hit.pageIndex === pageIndex ? 'bg-blue-50/70 dark:bg-blue-950/30' : ''}`}
                >
                  <span className="block font-semibold text-blue-700 dark:text-blue-300">
                    Page {hit.number}
                  </span>
                  <span className="text-gray-600 dark:text-gray-300">
                    {hit.before}
                    <mark className="rounded bg-blue-100 px-0.5 font-semibold text-blue-900 dark:bg-blue-900/60 dark:text-blue-100">
                      {hit.match}
                    </mark>
                    {hit.after}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <ol className="space-y-1" aria-label="Pages">
            {pages.map((page, i) => {
              const isCurrent = i === pageIndex
              const preview = splitWords(page.text).slice(0, 14).join(' ')
              return (
                <li key={page.number} ref={isCurrent ? activeItemRef : null}>
                  <button
                    type="button"
                    onClick={() => onSelectPage(i)}
                    aria-current={isCurrent ? 'page' : undefined}
                    className={`w-full rounded-lg border px-3 py-2 text-left text-xs leading-relaxed
                      ${isCurrent
                        ? 'border-blue-300 bg-blue-50 dark:border-blue-700 dark:bg-blue-950/40'
                        : 'border-transparent hover:bg-gray-50 dark:hover:bg-gray-800'}`}
                  >
                    <span className={`block font-semibold ${isCurrent ? 'text-blue-700 dark:text-blue-300' : 'text-gray-700 dark:text-gray-200'}`}>
                      Page {page.number}
                    </span>
                    <span className="line-clamp-2 text-gray-500 dark:text-gray-400">{preview}…</span>
                  </button>
                </li>
              )
            })}
          </ol>
        )}
      </div>
    </div>
  )
}
