import { useState } from 'react'

/**
 * F48 writing structure templates.
 *
 * The scaffolds are deliberately written as prompts rather than headings alone —
 * a blank page under the word "Introduction" is barely less blank than no page
 * at all, and the point of the feature is to tell the writer what goes there.
 *
 * Applying one is the only action on this page that can destroy work, so it
 * never overwrites silently: with text already in the notepad the dropdown asks
 * first and offers to add the scaffold underneath instead.
 */

const ESSAY = `Title:

Introduction
- What is this essay about?
- One line of background.
- My main point is:

First point
- Point:
- Evidence:
- Why this matters:

Second point
- Point:
- Evidence:
- Why this matters:

Conclusion
- My main point again, in different words:
- Why it matters:
`

const EMAIL = `Subject:

Hi ,

Why I am writing:

What I need, and by when:

Thanks,
`

const REPORT = `Title:

Summary
- What this report is about, in one or two sentences.

Findings
1.
2.
3.

Recommendation
- What should happen next:
- Why:
`

// Not exported: the page receives the scaffold it needs through onApply, which
// keeps this file a component module (eslint react-refresh/only-export-components).
const TEMPLATES = {
  essay: { label: 'Essay', hint: 'Introduction, points, conclusion', body: ESSAY },
  email: { label: 'Email', hint: 'Greeting, message, sign-off', body: EMAIL },
  report: { label: 'Report', hint: 'Summary, findings, recommendation', body: REPORT },
}

export default function TemplateSelector({ value, hasContent, disabled, onApply }) {
  // The template the user picked while there was already text to protect.
  const [pending, setPending] = useState(null)

  const choose = id => {
    if (!id) return
    if (hasContent) {
      setPending(id)
      return
    }
    onApply(id, TEMPLATES[id].body, 'replace')
  }

  const resolve = mode => {
    if (mode) onApply(pending, TEMPLATES[pending].body, mode)
    setPending(null)
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2">
        <label
          className="text-[11px] font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500"
          htmlFor="writing-template"
        >
          Structure
        </label>
        <select
          id="writing-template"
          // Bound to the applied template, so cancelling a confirmation snaps
          // the dropdown back on its own — `pending` never shows here.
          value={value ?? ''}
          disabled={disabled}
          onChange={e => choose(e.target.value)}
          className="rounded-xl border border-gray-200 bg-white px-3 py-1.5 text-sm
                     font-semibold text-gray-700 hover:border-gray-300
                     focus:border-blue-400 focus:outline-none disabled:opacity-50
                     dark:border-gray-700 dark:bg-[#2A2A2A] dark:text-gray-200"
        >
          <option value="">No template</option>
          {Object.entries(TEMPLATES).map(([id, template]) => (
            <option key={id} value={id}>
              {template.label} — {template.hint}
            </option>
          ))}
        </select>
      </div>

      {/* Confirmation. Autosave has no undo, so replacing has to be a choice
          the writer makes on purpose. */}
      {pending && (
        <div
          className="flex flex-wrap items-center gap-2 rounded-2xl border border-amber-200
                     bg-amber-50 p-3 text-sm text-amber-900
                     dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-100"
          role="alert"
        >
          <span className="flex-1">
            You've already written something. Where should the{' '}
            {TEMPLATES[pending].label.toLowerCase()} outline go?
          </span>
          <button
            type="button"
            onClick={() => resolve('append')}
            className="rounded-xl bg-amber-600 px-3 py-1.5 text-xs font-bold text-white
                       hover:bg-amber-700 focus-visible:outline-2
                       focus-visible:outline-offset-2 focus-visible:outline-amber-500"
          >
            Add to the end
          </button>
          <button
            type="button"
            onClick={() => resolve('replace')}
            className="rounded-xl border border-amber-300 px-3 py-1.5 text-xs font-bold
                       hover:bg-amber-100 focus-visible:outline-2
                       focus-visible:outline-offset-2 focus-visible:outline-amber-500
                       dark:border-amber-800 dark:hover:bg-amber-900/40"
          >
            Replace everything
          </button>
          <button
            type="button"
            onClick={() => resolve(null)}
            className="rounded-xl px-3 py-1.5 text-xs font-bold underline
                       focus-visible:outline-2 focus-visible:outline-offset-2
                       focus-visible:outline-amber-500"
          >
            Cancel
          </button>
        </div>
      )}
    </div>
  )
}
