import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  LabelList,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import { usePrefs } from '../context/PreferencesContext'
import { useTTSPlayer } from '../hooks/useTTSPlayer'
import { api } from '../utils/api'

/**
 * Chart colours, one set per theme. Every chart on this page plots a single
 * series, so there is one data hue and no legend — each card's title names
 * what is plotted.
 *
 * The hue is the app's own blue, checked with the dataviz palette validator
 * against each theme's card surface. blue-600 passes on white. Its usual dark
 * partner, blue-400, fails on #2A2A2A — too light for the lightness band — so
 * dark mode steps to blue-500, which passes. Text never wears the series
 * colour; labels use the text tokens.
 */
const THEMES = {
  light: { series: '#2563eb', surface: '#ffffff', grid: '#e5e7eb', axis: '#6b7280', text: '#374151' },
  dark:  { series: '#3b82f6', surface: '#2a2a2a', grid: '#3a3a3a', axis: '#9ca3af', text: '#e5e7eb' },
}

const LOAD_ERROR = 'Could not load this right now.'

// The chart reuses M1's word playback; nothing here follows the sync position.
const ignoreWordChange = () => {}

/** One endpoint's state. Each section loads alone, so one failure can't blank the page. */
function useEndpoint(path) {
  const [state, setState] = useState({ status: 'loading', data: null })

  useEffect(() => {
    let active = true
    api
      .get(path)
      .then(data => { if (active) setState({ status: 'ready', data }) })
      .catch(() => { if (active) setState({ status: 'error', data: null }) })
    return () => { active = false }
  }, [path])

  return state
}

function formatDay(iso) {
  return new Date(iso).toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
}

function formatWhen(iso) {
  return new Date(iso).toLocaleString(undefined, {
    day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit',
  })
}

function formatCount(n) {
  return n >= 10_000
    ? new Intl.NumberFormat(undefined, { notation: 'compact', maximumFractionDigits: 1 }).format(n)
    : n.toLocaleString()
}

function truncate(word, max = 14) {
  return word.length > max ? `${word.slice(0, max - 1)}…` : word
}

/** Clean ticks (0, 50, 100…) for a zero-based axis: at most five steps, top tick >= max. */
function niceTicks(max, minTop) {
  const top = Math.max(max, minTop)
  const step = [1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500].find(s => top / s <= 5) ?? Math.ceil(top / 5)
  const ticks = []
  for (let tick = 0; tick < top + step; tick += step) ticks.push(tick)
  return ticks
}

/** Label each day once, at its first session. Several sessions a day would otherwise
    repeat the same date along the axis; the tooltip and table still give each one's time. */
function withDayTicks(points) {
  return points.map((point, i) => ({
    ...point,
    tick: i > 0 && points[i - 1].day === point.day ? '' : point.day,
  }))
}

/* ── page chrome ─────────────────────────────────────────────────────── */
function Card({ title, subtitle, children, className = '' }) {
  return (
    <section
      className={`rounded-2xl border border-gray-200 bg-white p-5 shadow-sm
                  dark:border-gray-800 dark:bg-[#2A2A2A] ${className}`}
      aria-label={title}
    >
      <h2 className="text-base font-semibold text-gray-900 dark:text-white">{title}</h2>
      {subtitle && (
        <p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">{subtitle}</p>
      )}
      <div className="mt-4">{children}</div>
    </section>
  )
}

/** Loading, error and empty states, so each chart only renders real data. */
function SectionBody({ state, isEmpty, emptyMessage, height = 240, children }) {
  if (state.status === 'loading' || state.status === 'error' || isEmpty) {
    const message =
      state.status === 'loading' ? 'Loading…'
        : state.status === 'error' ? LOAD_ERROR
          : emptyMessage
    return (
      <div
        className="grid place-items-center rounded-xl bg-gray-50 px-6 text-center text-sm
                   text-gray-500 dark:bg-[#333] dark:text-gray-400"
        style={{ minHeight: height }}
        role={state.status === 'error' ? 'alert' : 'status'}
      >
        {message}
      </div>
    )
  }
  return children
}

function StatTile({ label, value, hint }) {
  return (
    <div
      className="rounded-2xl border border-gray-200 bg-white p-4 shadow-sm
                 dark:border-gray-800 dark:bg-[#2A2A2A]"
    >
      <p className="text-xs font-medium text-gray-500 dark:text-gray-400">{label}</p>
      <p className="mt-1 text-2xl font-semibold text-gray-950 dark:text-white">{value}</p>
      {hint && <p className="mt-0.5 text-xs text-gray-400 dark:text-gray-500">{hint}</p>}
    </div>
  )
}

/** Values lead, labels follow. */
function ChartTooltip({ active, payload, render }) {
  if (!active || !payload?.length) return null
  const { value, detail } = render(payload[0].payload)
  return (
    <div
      className="rounded-lg border border-gray-200 bg-white px-3 py-2 text-xs shadow-md
                 dark:border-gray-700 dark:bg-[#1E1E1E]"
    >
      <p className="text-sm font-semibold text-gray-950 dark:text-white">{value}</p>
      <p className="mt-0.5 text-gray-500 dark:text-gray-400">{detail}</p>
    </div>
  )
}

function TableView({ caption, columns, rows }) {
  return (
    <details className="mt-3 text-sm">
      <summary
        className="cursor-pointer text-xs font-semibold text-gray-500 hover:text-gray-900
                   dark:text-gray-400 dark:hover:text-white"
      >
        View as table
      </summary>
      <div className="mt-2 overflow-x-auto">
        <table className="w-full text-left text-xs">
          <caption className="sr-only">{caption}</caption>
          <thead>
            <tr className="border-b border-gray-200 text-gray-500 dark:border-gray-700 dark:text-gray-400">
              {columns.map(col => (
                <th key={col.key} scope="col" className={`py-1.5 pr-3 font-medium ${col.numeric ? 'text-right' : ''}`}>
                  {col.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map(row => (
              <tr key={row.key} className="border-b border-gray-100 last:border-0 dark:border-gray-800">
                {columns.map(col => (
                  <td
                    key={col.key}
                    className={`py-1.5 pr-3 text-gray-700 dark:text-gray-200 ${col.numeric ? 'text-right tabular-nums' : ''}`}
                  >
                    {row[col.key]}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  )
}

/* ── the page ────────────────────────────────────────────────────────── */
export default function AnalyticsPage() {
  const { prefs } = usePrefs()
  const theme = prefs.darkMode ? THEMES.dark : THEMES.light

  const summary = useEndpoint('/analytics/summary')
  const reading = useEndpoint('/analytics/reading')
  const writing = useEndpoint('/analytics/writing')
  const words = useEndpoint('/analytics/difficult-words')

  const { playWord } = useTTSPlayer(ignoreWordChange)
  const [playing, setPlaying] = useState(null)

  const handlePlay = useCallback(word => {
    setPlaying(word)
    playWord(word)
  }, [playWord])

  // Endpoints answer newest first; charts read left to right in time.
  const wpmPoints = useMemo(
    () => withDayTicks((reading.data ?? []).slice().reverse().map(s => ({
      key: s.id,
      day: formatDay(s.date),
      when: formatWhen(s.date),
      wpm: s.wpm,
      wordsRead: s.words_read,
      minutes: (s.duration_seconds / 60).toFixed(1),
      source: s.source_type,
    }))),
    [reading.data],
  )

  // A session with no words has no rate; leave it off the chart rather than
  // plotting it as a perfect 0. The table still lists it.
  const writingRows = useMemo(
    () => (writing.data ?? []).slice().reverse().map(s => ({
      key: s.id,
      day: formatDay(s.date),
      when: formatWhen(s.date),
      rate: s.error_rate,
      words: s.word_count,
      spelling: s.spell_error_count,
      grammar: s.grammar_error_count,
      homophones: s.homophone_flag_count,
    })),
    [writing.data],
  )
  const ratePoints = withDayTicks(writingRows.filter(r => r.rate !== null))
  const noErrorsAtAll = ratePoints.length > 0 && ratePoints.every(r => r.rate === 0)

  const speedTicks = niceTicks(Math.max(0, ...wpmPoints.map(p => p.wpm)), 50)
  const rateTicks = niceTicks(Math.max(0, ...ratePoints.map(p => p.rate)), 5)
  const speedTickLabels = Object.fromEntries(wpmPoints.map(p => [p.key, p.tick]))
  const rateTickLabels = Object.fromEntries(ratePoints.map(p => [p.key, p.tick]))

  const wordRows = words.data ?? []
  const s = summary.data

  const axisTick = { fill: theme.axis, fontSize: 12 }

  return (
    <main className="min-h-screen bg-gray-50/70 px-4 py-8 dark:bg-[#1E1E1E] sm:px-6">
      <div className="mx-auto max-w-6xl space-y-6">
        <header>
          <p className="text-sm font-semibold uppercase tracking-wide text-blue-600 dark:text-blue-300">
            Progress
          </p>
          <h1 className="mt-2 text-3xl font-bold tracking-tight text-gray-950 dark:text-white sm:text-4xl">
            How your reading is going.
          </h1>
          <p className="mt-3 max-w-2xl text-base leading-relaxed text-gray-600 dark:text-gray-300">
            Reading speed, writing accuracy, and the words you ask to hear most.
          </p>
        </header>

        {/* ── headline numbers ── */}
        <SectionBody state={summary} isEmpty={false} height={96}>
          {s && (
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
              <StatTile label="Reading sessions" value={s.reading_sessions.toLocaleString()} />
              <StatTile
                label="Words read"
                value={formatCount(s.total_words_read)}
                hint={`over ${Math.round(s.total_minutes_read)} min of listening`}
              />
              <StatTile
                label="Average speed"
                value={s.avg_wpm === null ? '—' : `${Math.round(s.avg_wpm)} wpm`}
                hint="weighted by time spent"
              />
              <StatTile label="Best speed" value={s.best_wpm === null ? '—' : `${Math.round(s.best_wpm)} wpm`} />
              <StatTile label="Writing sessions" value={s.writing_sessions.toLocaleString()} />
            </div>
          )}
        </SectionBody>

        <div className="grid gap-6 lg:grid-cols-2">
          {/* ── F39 · reading speed ── */}
          <Card title="Reading speed" subtitle="Words per minute, last 20 sessions">
            <SectionBody
              state={reading}
              isEmpty={wpmPoints.length === 0}
              emptyMessage="No reading sessions yet. Listen for 30 seconds or more on the Reading page and your speed appears here."
            >
              <ResponsiveContainer width="100%" height={240}>
                {/* Right margin leaves room for the end label ("136 wpm") so it is never clipped. */}
                <LineChart data={wpmPoints} margin={{ top: 12, right: 72, bottom: 0, left: -12 }}>
                  <CartesianGrid vertical={false} stroke={theme.grid} strokeWidth={1} />
                  <XAxis
                    dataKey="key" tick={axisTick} tickLine={false} axisLine={{ stroke: theme.grid }}
                    tickFormatter={key => speedTickLabels[key]} interval="preserveStartEnd" minTickGap={16}
                  />
                  <YAxis
                    tick={axisTick} tickLine={false} axisLine={false}
                    ticks={speedTicks} domain={[0, speedTicks[speedTicks.length - 1]]}
                  />
                  <Tooltip
                    cursor={{ stroke: theme.axis, strokeWidth: 1 }}
                    content={
                      <ChartTooltip
                        render={p => ({
                          value: `${Math.round(p.wpm)} wpm`,
                          detail: `${p.when} · ${p.wordsRead} words in ${p.minutes} min`,
                        })}
                      />
                    }
                  />
                  <Line
                    type="linear"
                    dataKey="wpm"
                    stroke={theme.series}
                    strokeWidth={2}
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    dot={{ r: 4, fill: theme.series, stroke: theme.surface, strokeWidth: 2 }}
                    activeDot={{ r: 6, fill: theme.series, stroke: theme.surface, strokeWidth: 2 }}
                    isAnimationActive={false}
                    // Label the latest session only — the axis, tooltip and table carry the rest.
                    label={({ index, x, y, value }) =>
                      index === wpmPoints.length - 1 ? (
                        <text x={x + 10} y={y} dy={4} fill={theme.text} fontSize={12} fontWeight={600}>
                          {Math.round(value)} wpm
                        </text>
                      ) : null
                    }
                  />
                </LineChart>
              </ResponsiveContainer>
              <TableView
                caption="Reading speed by session"
                columns={[
                  { key: 'when', label: 'Session' },
                  { key: 'wpm', label: 'Words per minute', numeric: true },
                  { key: 'wordsRead', label: 'Words read', numeric: true },
                  { key: 'minutes', label: 'Minutes', numeric: true },
                  { key: 'source', label: 'Source' },
                ]}
                rows={wpmPoints.slice().reverse()}
              />
            </SectionBody>
          </Card>

          {/* ── F40 · writing errors ── */}
          <Card title="Writing errors" subtitle="Errors per 100 words, last 20 sessions">
            <SectionBody
              state={writing}
              isEmpty={writingRows.length === 0}
              emptyMessage="No writing sessions yet. Write in the notepad and each session appears here."
            >
              <ResponsiveContainer width="100%" height={240}>
                <BarChart data={ratePoints} margin={{ top: 12, right: 12, bottom: 0, left: -12 }}>
                  <CartesianGrid vertical={false} stroke={theme.grid} strokeWidth={1} />
                  <XAxis
                    dataKey="key" tick={axisTick} tickLine={false} axisLine={{ stroke: theme.grid }}
                    tickFormatter={key => rateTickLabels[key]} interval="preserveStartEnd" minTickGap={16}
                  />
                  <YAxis
                    tick={axisTick} tickLine={false} axisLine={false}
                    ticks={rateTicks} domain={[0, rateTicks[rateTicks.length - 1]]}
                  />
                  <Tooltip
                    cursor={{ fill: theme.grid, opacity: 0.5 }}
                    content={
                      <ChartTooltip
                        render={p => ({
                          value: `${p.rate} per 100 words`,
                          detail: `${p.when} · ${p.spelling} spelling, ${p.grammar} grammar, ${p.homophones} homophone in ${p.words} words`,
                        })}
                      />
                    }
                  />
                  <Bar dataKey="rate" fill={theme.series} radius={[4, 4, 0, 0]} maxBarSize={24} isAnimationActive={false} />
                </BarChart>
              </ResponsiveContainer>
              {noErrorsAtAll && (
                <p className="mt-2 text-xs text-gray-500 dark:text-gray-400">
                  No errors were flagged in these sessions.
                </p>
              )}
              <TableView
                caption="Writing errors by session"
                columns={[
                  { key: 'when', label: 'Session' },
                  { key: 'words', label: 'Words', numeric: true },
                  { key: 'spelling', label: 'Spelling', numeric: true },
                  { key: 'grammar', label: 'Grammar', numeric: true },
                  { key: 'homophones', label: 'Homophones', numeric: true },
                  { key: 'rateLabel', label: 'Per 100 words', numeric: true },
                ]}
                rows={writingRows.slice().reverse().map(r => ({ ...r, rateLabel: r.rate ?? '—' }))}
              />
            </SectionBody>
          </Card>
        </div>

        {/* ── F41 · difficult words ── */}
        <Card
          title="Words you replay most"
          subtitle="Times you tapped each word to hear it again. Select a bar to hear the word."
        >
          <SectionBody
            state={words}
            isEmpty={wordRows.length === 0}
            height={160}
            emptyMessage="No replayed words yet. Tap a word on the Reading page to hear it again — the ones you replay most show up here."
          >
            <ResponsiveContainer width="100%" height={wordRows.length * 36 + 16}>
              <BarChart data={wordRows} layout="vertical" margin={{ top: 4, right: 40, bottom: 4, left: 8 }}>
                <XAxis type="number" hide allowDecimals={false} />
                <YAxis
                  type="category" dataKey="word" width={120} tick={axisTick} tickLine={false} axisLine={false}
                  tickFormatter={word => truncate(word)}
                />
                <Tooltip
                  cursor={{ fill: theme.grid, opacity: 0.5 }}
                  content={
                    <ChartTooltip
                      render={p => ({
                        value: `Replayed ${p.repeat_count} ${p.repeat_count === 1 ? 'time' : 'times'}`,
                        detail: `${p.word}${p.difficulty_label ? ` · ${p.difficulty_label}` : ''} · select to hear it`,
                      })}
                    />
                  }
                />
                <Bar
                  dataKey="repeat_count"
                  fill={theme.series}
                  radius={[0, 4, 4, 0]}
                  maxBarSize={24}
                  isAnimationActive={false}
                  cursor="pointer"
                  onClick={bar => handlePlay((bar.payload ?? bar).word)}
                >
                  {/* Bars -> value at the tip, so the chart needs no x axis. */}
                  <LabelList dataKey="repeat_count" position="right" fill={theme.text} fontSize={12} fontWeight={600} />
                </Bar>
              </BarChart>
            </ResponsiveContainer>
            <p className="sr-only" aria-live="polite">{playing ? `Playing ${playing}` : ''}</p>
            <TableView
              caption="Most replayed words"
              columns={[
                { key: 'wordButton', label: 'Word' },
                { key: 'repeat_count', label: 'Times replayed', numeric: true },
                { key: 'label', label: 'Difficulty' },
              ]}
              rows={wordRows.map(w => ({
                key: w.word,
                repeat_count: w.repeat_count,
                label: w.difficulty_label ?? '—',
                // The keyboard route to the same playback the bars offer.
                wordButton: (
                  <button
                    type="button"
                    onClick={() => handlePlay(w.word)}
                    className="rounded font-semibold text-blue-700 underline-offset-2 hover:underline
                               focus-visible:outline-2 focus-visible:outline-blue-500 dark:text-blue-300"
                    aria-label={`Hear ${w.word}`}
                  >
                    {w.word}
                  </button>
                ),
              }))}
            />
          </SectionBody>
        </Card>
      </div>
    </main>
  )
}
