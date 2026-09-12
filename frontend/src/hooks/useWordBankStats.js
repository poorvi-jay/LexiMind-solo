import { useCallback, useState } from 'react'

import { api } from '../utils/api'
import { useAuthContext } from '../context/AuthContext.jsx'

/**
 * F51 — the word bank's headline numbers, behind the nav badge and the
 * homepage reminder.
 *
 * Fetching is left to the caller rather than run on mount, because both
 * consumers need it at different moments: the NavBar refetches on every
 * navigation, so the badge falls to zero as soon as a drill is finished, while
 * the homepage only needs it when it renders.
 *
 * Returns null stats when logged out — the homepage is public, and calling a
 * token-protected endpoint there would only produce 401s.
 */
export function useWordBankStats() {
  const { isAuthenticated } = useAuthContext()
  const [stats, setStats] = useState(null)
  const [error, setError] = useState(false)

  const refresh = useCallback(async () => {
    if (!isAuthenticated) {
      setStats(null)
      return
    }
    try {
      setStats(await api.get('/wordbank/stats'))
      setError(false)
    } catch {
      // A badge and a reminder card are both ornaments: if this fails they
      // simply do not appear, and nothing else on the page is affected.
      setError(true)
    }
  }, [isAuthenticated])

  return { stats, error, refresh }
}
