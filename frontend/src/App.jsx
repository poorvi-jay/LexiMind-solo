import { lazy, Suspense } from 'react'
import { Navigate, Route, Routes, useLocation } from 'react-router-dom'

import HomePage from './pages/HomePage.jsx'
import NavBar from './components/NavBar.jsx'
import { useAuthContext } from './context/AuthContext.jsx'

/**
 * Routes are split per page, so opening the app downloads the landing page and
 * nothing else. Before this the whole product was one 709 kB chunk: someone
 * reading a PDF paid to download the analytics charts and the writing editor
 * first, on whatever connection they had.
 *
 * NavBar and HomePage stay eager because they ARE the first paint — deferring
 * them would only add a round trip before anything appears. Everything behind
 * a route is fetched when that route is opened.
 *
 * AnalyticsPage matters most here: recharts is used by nothing else in the
 * app, so splitting this one route takes the entire charting library off the
 * path of every other page.
 */
const AnalyticsPage = lazy(() => import('./pages/AnalyticsPage.jsx'))
const AuthPage = lazy(() => import('./pages/AuthPage.jsx'))
const ReadingPage = lazy(() => import('./pages/ReadingPage.jsx'))
const ResetPasswordPage = lazy(() => import('./pages/ResetPasswordPage.jsx'))
const SettingsPage = lazy(() => import('./pages/SettingsPage.jsx'))
const WordBankDrillPage = lazy(() => import('./pages/WordBankDrillPage.jsx'))
const WritingPage = lazy(() => import('./pages/WritingPage.jsx'))

/** The one loading state, shared by the auth check and the route chunks. */
function Loading() {
  return (
    <p
      className="px-6 py-12 text-center text-sm text-gray-500 dark:text-gray-400"
      role="status"
    >
      Loading…
    </p>
  )
}

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
    return <Loading />
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
      {/* One boundary around the whole table: a route's chunk is fetched when
          it is navigated to, and this is what shows while it arrives. */}
      <Suspense fallback={<Loading />}>
        <Routes>
          <Route path="/auth" element={<AuthPage />} />
          {/* Public: whoever follows a reset link is by definition locked out. */}
          <Route path="/auth/reset" element={<ResetPasswordPage />} />
          <Route path="/" element={<HomePage />} />
          <Route path="/reading" element={<RequireAuth><ReadingPage /></RequireAuth>} />
          <Route path="/writing" element={<RequireAuth><WritingPage /></RequireAuth>} />
          <Route path="/analytics" element={<RequireAuth><AnalyticsPage /></RequireAuth>} />
          <Route path="/wordbank/drill" element={<RequireAuth><WordBankDrillPage /></RequireAuth>} />
          <Route path="/settings" element={<RequireAuth><SettingsPage /></RequireAuth>} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Suspense>
    </div>
  )
}
