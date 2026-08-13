import { useEffect, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'

import { api } from '../utils/api'

const MIN_PASSWORD_LENGTH = 8 // must match backend/routers/auth.py

/**
 * The page a reset link lands on: /auth/reset?token=…
 *
 * The link is checked before anything is typed. Finding out a link has expired
 * only after choosing and confirming a new password is a small cruelty in any
 * app, and more so in one built for people who find typing effortful.
 */
export default function ResetPasswordPage() {
  const [params] = useSearchParams()
  const token = params.get('token') || ''
  const navigate = useNavigate()

  // 'checking' → asking the server · 'ready' → link is good · 'dead' → expired,
  // used or nonsense · 'done' → password changed
  //
  // A missing token needs no request, and it is known during render, so it is
  // the initial state rather than something an effect sets (eslint
  // react-hooks/set-state-in-effect, and it saves a wasted render besides).
  const [state, setState] = useState(() => (token ? 'checking' : 'dead'))
  const [password, setPassword] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    if (!token) return
    let cancelled = false
    api
      .get(`/auth/reset-password/${encodeURIComponent(token)}`)
      .then(({ valid }) => {
        if (!cancelled) setState(valid ? 'ready' : 'dead')
      })
      .catch(() => {
        if (!cancelled) setState('dead')
      })
    return () => {
      cancelled = true
    }
  }, [token])

  const handleSubmit = async event => {
    event.preventDefault()
    setError('')

    if (password.length < MIN_PASSWORD_LENGTH) {
      setError(`Password must be at least ${MIN_PASSWORD_LENGTH} characters.`)
      return
    }
    if (password !== confirmation) {
      setError('The two passwords do not match.')
      return
    }

    setSubmitting(true)
    try {
      await api.post('/auth/reset-password', { token, password })
      setState('done')
    } catch (err) {
      // A 400 here means the link died between the check and the submit.
      setError(err.message || 'Could not change your password. Please try again.')
    } finally {
      setSubmitting(false)
    }
  }

  const inputClass =
    `w-full rounded-xl border border-gray-300 bg-white px-4 py-3 text-base
     text-gray-900 placeholder:text-gray-400
     focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-500
     dark:border-gray-700 dark:bg-[#2A2A2A] dark:text-white dark:placeholder:text-gray-500`

  return (
    <main className="mx-auto flex max-w-md flex-col gap-6 px-6 py-12">
      <header className="text-center">
        <h1 className="text-2xl font-semibold text-gray-950 dark:text-white">
          Choose a new password
        </h1>
      </header>

      {state === 'checking' && (
        <p className="text-center text-sm text-gray-500 dark:text-gray-400">
          Checking your link…
        </p>
      )}

      {state === 'dead' && (
        <div
          className="flex flex-col gap-3 rounded-xl bg-red-50 px-4 py-4 text-sm text-red-800
                     dark:bg-red-950/40 dark:text-red-200"
          role="alert"
        >
          <p className="m-0">
            This reset link has expired or has already been used. Reset links last
            30 minutes and work once.
          </p>
          <Link to="/auth" className="font-semibold underline">
            Ask for a new one
          </Link>
        </div>
      )}

      {state === 'done' && (
        <div
          className="flex flex-col gap-3 rounded-xl bg-green-50 px-4 py-4 text-sm text-green-800
                     dark:bg-green-950/40 dark:text-green-200"
          role="status"
        >
          <p className="m-0">
            Your password has been changed. For safety, anywhere you were already
            signed in has been signed out.
          </p>
          <button
            type="button"
            onClick={() => navigate('/auth', { replace: true })}
            className="self-start rounded-xl bg-green-700 px-4 py-2 text-xs font-bold text-white
                       hover:bg-green-800 focus-visible:outline-2
                       focus-visible:outline-offset-2 focus-visible:outline-green-600"
          >
            Log in
          </button>
        </div>
      )}

      {state === 'ready' && (
        <form onSubmit={handleSubmit} className="flex flex-col gap-4" noValidate>
          <div>
            <label
              htmlFor="reset-password"
              className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-200"
            >
              New password
            </label>
            <input
              id="reset-password"
              type="password"
              required
              autoComplete="new-password"
              value={password}
              onChange={e => setPassword(e.target.value)}
              className={inputClass}
              placeholder={`At least ${MIN_PASSWORD_LENGTH} characters`}
              aria-describedby="reset-password-hint"
            />
            <p
              id="reset-password-hint"
              className="mt-1 text-xs text-gray-500 dark:text-gray-400"
            >
              At least {MIN_PASSWORD_LENGTH} characters.
            </p>
          </div>

          <div>
            <label
              htmlFor="reset-confirm"
              className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-200"
            >
              Type it again
            </label>
            <input
              id="reset-confirm"
              type="password"
              required
              autoComplete="new-password"
              value={confirmation}
              onChange={e => setConfirmation(e.target.value)}
              className={inputClass}
              placeholder="The same password"
            />
          </div>

          {error && (
            <p
              role="alert"
              className="rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700
                         dark:bg-red-950/40 dark:text-red-200"
            >
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={submitting}
            className="mt-2 rounded-xl bg-blue-600 px-4 py-3 text-base font-semibold text-white
                       shadow-sm transition-colors hover:bg-blue-700 disabled:opacity-60
                       focus-visible:outline-2 focus-visible:outline-offset-2
                       focus-visible:outline-blue-500"
          >
            {submitting ? 'Please wait…' : 'Save new password'}
          </button>
        </form>
      )}
    </main>
  )
}
