import { useEffect, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'

import { useAuthContext } from '../context/AuthContext.jsx'

const MIN_PASSWORD_LENGTH = 8 // must match backend/routers/auth.py

export default function AuthPage() {
  const [mode, setMode] = useState('login') // 'login' | 'register'
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)

  const { login, register, isAuthenticated } = useAuthContext()
  const navigate = useNavigate()
  const location = useLocation()

  // Where the guard bounced them from, so they land back there after login.
  const redirectTo = location.state?.from || '/'

  useEffect(() => {
    if (isAuthenticated) navigate(redirectTo, { replace: true })
  }, [isAuthenticated, navigate, redirectTo])

  const switchMode = next => {
    setMode(next)
    setError('')
  }

  const handleSubmit = async event => {
    event.preventDefault()
    setError('')

    if (mode === 'register' && !name.trim()) {
      setError('Please enter your name.')
      return
    }
    if (password.length < MIN_PASSWORD_LENGTH) {
      setError(`Password must be at least ${MIN_PASSWORD_LENGTH} characters.`)
      return
    }

    setSubmitting(true)
    try {
      if (mode === 'login') await login(email.trim(), password)
      else await register(name.trim(), email.trim(), password)
      // The effect above handles the redirect once isAuthenticated flips.
    } catch (err) {
      setError(err.message || 'Something went wrong. Please try again.')
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
          {mode === 'login' ? 'Welcome back' : 'Create your account'}
        </h1>
        <p className="mt-2 text-sm text-gray-500 dark:text-gray-400">
          {mode === 'login'
            ? 'Sign in to reach your reading tools and saved settings.'
            : 'Your reading preferences follow you across every device.'}
        </p>
      </header>

      {/* ── Login / Register tabs ── */}
      <div
        className="flex rounded-full border border-gray-200 bg-gray-50 p-1
                   dark:border-gray-800 dark:bg-[#2A2A2A]"
        role="tablist"
        aria-label="Authentication mode"
      >
        {[
          { key: 'login', label: 'Log in' },
          { key: 'register', label: 'Sign up' },
        ].map(tab => (
          <button
            key={tab.key}
            type="button"
            role="tab"
            aria-selected={mode === tab.key}
            onClick={() => switchMode(tab.key)}
            className={`flex-1 rounded-full px-4 py-2 text-sm font-medium transition-colors
              focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-500
              ${
                mode === tab.key
                  ? 'bg-white text-blue-700 shadow-sm dark:bg-gray-800 dark:text-blue-200'
                  : 'text-gray-500 hover:text-gray-900 dark:text-gray-300 dark:hover:text-white'
              }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      <form onSubmit={handleSubmit} className="flex flex-col gap-4" noValidate>
        {mode === 'register' && (
          <div>
            <label
              htmlFor="auth-name"
              className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-200"
            >
              Name
            </label>
            <input
              id="auth-name"
              type="text"
              autoComplete="name"
              value={name}
              onChange={e => setName(e.target.value)}
              className={inputClass}
              placeholder="Your name"
            />
          </div>
        )}

        <div>
          <label
            htmlFor="auth-email"
            className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-200"
          >
            Email
          </label>
          <input
            id="auth-email"
            type="email"
            required
            autoComplete="email"
            value={email}
            onChange={e => setEmail(e.target.value)}
            className={inputClass}
            placeholder="you@example.com"
          />
        </div>

        <div>
          <label
            htmlFor="auth-password"
            className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-200"
          >
            Password
          </label>
          <input
            id="auth-password"
            type="password"
            required
            autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
            value={password}
            onChange={e => setPassword(e.target.value)}
            className={inputClass}
            placeholder={`At least ${MIN_PASSWORD_LENGTH} characters`}
            aria-describedby={mode === 'register' ? 'auth-password-hint' : undefined}
          />
          {mode === 'register' && (
            <p
              id="auth-password-hint"
              className="mt-1 text-xs text-gray-500 dark:text-gray-400"
            >
              At least {MIN_PASSWORD_LENGTH} characters.
            </p>
          )}
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
          {submitting
            ? 'Please wait…'
            : mode === 'login'
              ? 'Log in'
              : 'Create account'}
        </button>
      </form>
    </main>
  )
}
