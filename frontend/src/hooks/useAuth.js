import { useCallback, useEffect, useState } from 'react'

import { ApiError, api, getToken, setToken } from '../utils/api'

/**
 * Core auth hook — owns the JWT and the current user.
 * Consumed by AuthContext; components should use useAuthContext() instead.
 *
 * The token lives in localStorage (AC-02: session survives refresh) and is
 * attached to every request by api.js.
 */
export function useAuth() {
  const [token, setTokenState] = useState(() => getToken())
  const [user, setUser] = useState(null)
  // Starts true when a token exists, so route guards wait for /auth/me instead
  // of bouncing an already-logged-in user to the login page on first paint.
  const [loading, setLoading] = useState(() => Boolean(getToken()))

  /* ── Hydrate the user from a stored token on mount ── */
  useEffect(() => {
    // Only fires for a token with no user attached yet — a session restored
    // from localStorage on page load. login()/register() set the user directly,
    // so this does not re-fetch after a fresh sign-in.
    if (!token || user) return

    let cancelled = false

    api.get('/auth/me')
      .then(profile => {
        if (!cancelled) setUser(profile)
      })
      .catch(err => {
        if (cancelled) return
        // Expired or revoked token — drop it rather than loop on 401s.
        if (err instanceof ApiError && err.status === 401) {
          setToken(null)
          setTokenState(null)
        }
        setUser(null)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => { cancelled = true }
  }, [token, user])

  const applySession = useCallback(data => {
    setToken(data.access_token)
    setTokenState(data.access_token)
    setUser(data.user)
    setLoading(false)
    return data.user
  }, [])

  const login = useCallback(
    (email, password) =>
      api.post('/auth/login', { email, password }).then(applySession),
    [applySession]
  )

  const register = useCallback(
    (name, email, password) =>
      api.post('/auth/register', { name, email, password }).then(applySession),
    [applySession]
  )

  const logout = useCallback(() => {
    setToken(null)
    setTokenState(null)
    setUser(null)
    setLoading(false)
  }, [])

  return {
    token,
    user,
    isAuthenticated: Boolean(token && user),
    loading,
    login,
    register,
    logout,
  }
}
