import { Navigate, Route, Routes, useLocation } from 'react-router-dom'

import AuthPage from './pages/AuthPage.jsx'
import HomePage from './pages/HomePage.jsx'
import NavBar from './components/NavBar.jsx'
import ReadingPage from './pages/ReadingPage.jsx'
import SettingsPage from './pages/SettingsPage.jsx'
import WritingPage from './pages/WritingPage.jsx'
import { useAuthContext } from './context/AuthContext.jsx'

/**
 * Gates the pages that call token-protected endpoints. Home stays public so
 * visitors land on it and choose to log in — it makes no API calls of its own.
 *
 * Waits for the /auth/me hydration to finish before redirecting, otherwise a
 * refresh would bounce a logged-in user to the login page.
 */
function RequireAuth({ children }) {
  const { isAuthenticated, loading } = useAuthContext()
  const location = useLocation()

  if (loading) {
    return (
      <p className="px-6 py-12 text-center text-sm text-gray-500 dark:text-gray-400">
        Loading…
      </p>
    )
  }

  if (!isAuthenticated) {
    return <Navigate to="/auth" replace state={{ from: location.pathname }} />
  }

  return children
}

export default function App() {
  return (
    <div className="min-h-screen">
      <NavBar />
      <Routes>
        <Route path="/auth" element={<AuthPage />} />
        <Route path="/" element={<HomePage />} />
        <Route path="/reading" element={<RequireAuth><ReadingPage /></RequireAuth>} />
        <Route path="/writing" element={<RequireAuth><WritingPage /></RequireAuth>} />
        <Route path="/settings" element={<RequireAuth><SettingsPage /></RequireAuth>} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </div>
  )
}
