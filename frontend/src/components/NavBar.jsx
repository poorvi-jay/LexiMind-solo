import { Link, useLocation, useNavigate } from 'react-router-dom'

import { startTransition } from 'react'
import { useAuthContext } from '../context/AuthContext.jsx'

const links = [
  { to: '/', label: 'Home' },
  { to: '/reading', label: 'Reading' },
  { to: '/writing', label: 'Writing' },
  { to: '/analytics', label: 'Analytics' },
  { to: '/wordbank/drill', label: 'Word bank' },
  { to: '/settings', label: 'Settings' },
]

export default function NavBar() {
  const { pathname } = useLocation()
  const { isAuthenticated, user, logout } = useAuthContext()
  const navigate = useNavigate()

  // Home is public, so it's the natural place to land after logging out.
  // Both updates go in one transition: the router runs navigation as a
  // transition (v7_startTransition in main.jsx), so an urgent logout() would
  // otherwise commit first and let the current page's guard bounce the user
  // to /auth before the navigation home lands.
  const handleLogout = () => {
    startTransition(() => {
      logout()
      navigate('/', { replace: true })
    })
  }

  return (
    <nav
      className="sticky top-0 z-40 border-b border-white/60 bg-white/85 px-6 py-4
                 shadow-sm backdrop-blur dark:border-gray-800 dark:bg-[#1E1E1E]/90"
      role="navigation"
      aria-label="Main navigation"
    >
      <div className="mx-auto flex max-w-6xl items-center justify-between gap-4">
        {/* ── Logo ── */}
        <Link
          to="/"
          className="flex items-center gap-3 rounded-xl
                     focus-visible:outline-2 focus-visible:outline-offset-2
                     focus-visible:outline-blue-500"
          aria-label="LexiMind home"
        >
          <span
            className="grid h-10 w-10 place-items-center rounded-2xl bg-blue-600
                        text-lg font-bold text-white shadow-md shadow-blue-200
                        dark:shadow-none"
            aria-hidden="true"
          >
            L
          </span>
          <div>
            <span className="block text-lg font-bold tracking-tight text-gray-950 dark:text-white">
              LexiMind
            </span>
            <span className="hidden text-xs text-gray-500 dark:text-gray-400 sm:block">
              Assistive reading platform
            </span>
          </div>
        </Link>

        {/* ── Tab group ── */}
        {/* Always visible. Reading and Settings are auth-gated, so a logged-out
            visitor who picks one is sent to /auth and returned there afterwards. */}
        <div
          className="flex rounded-full border border-gray-200 bg-gray-50 p-1
                      dark:border-gray-800 dark:bg-[#2A2A2A]"
          role="tablist"
          aria-label="Site sections"
        >
          {links.map(link => {
            const active = pathname === link.to
            return (
              <Link
                key={link.to}
                to={link.to}
                role="tab"
                aria-selected={active}
                aria-current={active ? 'page' : undefined}
                className={`rounded-full px-4 py-2 text-sm font-medium transition-colors
                  focus-visible:outline-2 focus-visible:outline-offset-2
                  focus-visible:outline-blue-500
                  ${
                    active
                      ? 'bg-white text-blue-700 shadow-sm dark:bg-gray-800 dark:text-blue-200'
                      : 'text-gray-500 hover:text-gray-900 dark:text-gray-300 dark:hover:text-white'
                  }`}
              >
                {link.label}
              </Link>
            )
          })}
        </div>

        {/* ── Auth state ── */}
        <div className="flex items-center gap-3">
          {isAuthenticated ? (
            <>
              <span className="hidden text-sm text-gray-500 dark:text-gray-400 sm:block">
                {user?.name}
              </span>
              <button
                type="button"
                onClick={handleLogout}
                className="rounded-full border border-gray-200 px-4 py-2 text-sm font-medium
                           text-gray-600 transition-colors hover:text-gray-900
                           focus-visible:outline-2 focus-visible:outline-offset-2
                           focus-visible:outline-blue-500
                           dark:border-gray-800 dark:text-gray-300 dark:hover:text-white"
              >
                Log out
              </button>
            </>
          ) : (
            pathname !== '/auth' && (
              <Link
                to="/auth"
                className="rounded-full bg-blue-600 px-4 py-2 text-sm font-semibold text-white
                           transition-colors hover:bg-blue-700
                           focus-visible:outline-2 focus-visible:outline-offset-2
                           focus-visible:outline-blue-500"
              >
                Log in
              </Link>
            )
          )}
        </div>
      </div>
    </nav>
  )
}