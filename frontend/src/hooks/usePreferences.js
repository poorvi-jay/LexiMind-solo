import { useCallback, useEffect, useRef, useState } from 'react'

import { api } from '../utils/api'
import { useAuthContext } from '../context/AuthContext.jsx'

const STORAGE_KEY = 'leximind-prefs'
// User ids whose local settings have already been pushed to the backend, so the
// one-time migration doesn't overwrite their saved preferences on every login.
const MIGRATED_KEY = 'leximind-prefs-migrated'
// Sliders fire on every tick; coalesce them into one PATCH.
const SYNC_DEBOUNCE_MS = 600

const DEFAULTS = {
  font: 'Lexend',
  fontSize: 18,
  lineSpacing: 2.0,
  wordSpacing: 2,
  overlay: '#FFFFFF',
  highlightColor: '#FFD700',
  focusRuler: true,
  darkMode: false,
  phrasePauses: true,
}

/**
 * Reading presets — each one sets multiple preferences at once.
 */
export const PRESETS = {
  comfort: {
    label: 'Comfort Reading',
    description: 'Relaxed spacing and warm background for extended reading.',
    icon: '☕',
    values: {
      font: 'Lexend',
      fontSize: 20,
      lineSpacing: 2.2,
      wordSpacing: 3,
      overlay: '#FFF9E6',
      highlightColor: '#FFD700',
      focusRuler: true,
      phrasePauses: true,
    },
  },
  academic: {
    label: 'Academic Reading',
    description: 'Tighter layout for study materials and textbooks.',
    icon: '🎓',
    values: {
      font: 'Lexend',
      fontSize: 18,
      lineSpacing: 1.8,
      wordSpacing: 2,
      overlay: '#FFFFFF',
      highlightColor: '#87CEEB',
      focusRuler: true,
      phrasePauses: true,
    },
  },
  focus: {
    label: 'Maximum Focus',
    description: 'Large text, high spacing, focus ruler, minimal distractions.',
    icon: '🎯',
    values: {
      font: 'Lexend',
      fontSize: 24,
      lineSpacing: 2.6,
      wordSpacing: 5,
      overlay: '#E8F5E9',
      highlightColor: '#90EE90',
      focusRuler: true,
      phrasePauses: true,
    },
  },
  dyslexic: {
    label: 'OpenDyslexic Mode',
    description: 'OpenDyslexic font with optimised contrast and spacing.',
    icon: '✦',
    values: {
      font: 'OpenDyslexic',
      fontSize: 20,
      lineSpacing: 2.4,
      wordSpacing: 4,
      overlay: '#FFF9E6',
      highlightColor: '#FFD700',
      focusRuler: true,
      phrasePauses: true,
    },
  },
}

/* ── localStorage helpers ── */
function readStored() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? JSON.parse(raw) : null
  } catch {
    return null // corrupt data or storage disabled
  }
}

/** Drop anything the API doesn't know about — it rejects unknown keys. */
function syncable(values) {
  const out = {}
  for (const key of Object.keys(DEFAULTS)) {
    if (values[key] !== undefined) out[key] = values[key]
  }
  return out
}

/**
 * The keys this device actually customised.
 *
 * The persist effect below writes the current values on first paint, so a
 * device that has never been touched still has a full set of defaults in
 * localStorage. Migrating those blindly would overwrite the account's real
 * saved settings with defaults on every first login from a new device, so the
 * migration only ever pushes values that differ from the defaults.
 */
function customised(values) {
  const out = {}
  for (const [key, fallback] of Object.entries(DEFAULTS)) {
    if (values[key] !== undefined && values[key] !== fallback) out[key] = values[key]
  }
  return out
}

function migratedUsers() {
  try {
    const raw = JSON.parse(localStorage.getItem(MIGRATED_KEY))
    return Array.isArray(raw) ? raw : []
  } catch {
    return []
  }
}

function markMigrated(userId) {
  try {
    const list = migratedUsers()
    if (!list.includes(userId)) {
      localStorage.setItem(MIGRATED_KEY, JSON.stringify([...list, userId]))
    }
  } catch {
    /* storage full / disabled */
  }
}

/**
 * Core preferences hook — manages state, backend sync, and CSS variable sync.
 * Consumed by PreferencesContext.
 *
 * Signed in, the backend is the source of truth (PRD 2.3): preferences load
 * from /auth/me and save through PATCH /auth/preferences, so they follow the
 * user across devices. localStorage is still written on every change — it
 * serves logged-out visitors on the public home page and acts as the offline
 * cache that seeds the UI before the profile arrives.
 */
export function usePreferences() {
  const { isAuthenticated, user } = useAuthContext()

  const [prefs, setPrefs] = useState(() => ({ ...DEFAULTS, ...(readStored() || {}) }))

  const hydratedRef = useRef(null) // id of the user whose preferences are loaded
  const pendingRef = useRef({})
  const timerRef = useRef(null)

  /* ── Load this user's preferences, migrating local ones the first time ── */
  useEffect(() => {
    if (!isAuthenticated || !user) {
      hydratedRef.current = null
      return
    }
    if (hydratedRef.current === user.id) return
    hydratedRef.current = user.id

    const local = readStored()
    const localChanges = local ? customised(local) : {}
    const shouldMigrate =
      Object.keys(localChanges).length > 0 && !migratedUsers().includes(user.id)
    let cancelled = false

    // useAuth already fetched this profile via /auth/me, so only the migration
    // path needs a request of its own. It sends just the customised keys, so
    // anything this device never touched keeps the account's saved value.
    const request = shouldMigrate
      ? api.patch('/auth/preferences', localChanges)
      : Promise.resolve(user)

    request
      .then(profile => {
        if (cancelled) return
        if (shouldMigrate) markMigrated(user.id)
        setPrefs({ ...DEFAULTS, ...(profile.preferences || {}) })
      })
      .catch(() => {
        // Stay on the local values and allow a later attempt to retry.
        if (!cancelled) hydratedRef.current = null
      })

    return () => { cancelled = true }
  }, [isAuthenticated, user])

  /* ── Persist to localStorage ── */
  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(prefs))
    } catch {
      /* storage full / disabled */
    }
  }, [prefs])

  /* ── Push changes to the backend, debounced and coalesced ── */
  const queueSync = useCallback(updates => {
    if (!isAuthenticated) return

    Object.assign(pendingRef.current, syncable(updates))
    if (timerRef.current) clearTimeout(timerRef.current)

    timerRef.current = setTimeout(() => {
      timerRef.current = null
      const payload = pendingRef.current
      pendingRef.current = {}
      if (Object.keys(payload).length === 0) return
      // On failure the local value stands and the next change retries.
      api.patch('/auth/preferences', payload).catch(() => {})
    }, SYNC_DEBOUNCE_MS)
  }, [isAuthenticated])

  /* ── Sync CSS custom properties + dark mode class ── */
  useEffect(() => {
    const root = document.documentElement
    const body = document.body

    root.style.setProperty('--leximind-font', `'${prefs.font}', Arial, Verdana, sans-serif`)
    root.style.setProperty('--leximind-overlay', prefs.darkMode ? '#1E1E1E' : prefs.overlay)
    root.style.setProperty('--leximind-font-size', `${prefs.fontSize}px`)
    root.style.setProperty('--leximind-line-spacing', String(prefs.lineSpacing))
    root.style.setProperty('--leximind-highlight', prefs.highlightColor)
    root.style.setProperty('--leximind-word-spacing', `${prefs.wordSpacing}px`)

    body.style.fontFamily = `'${prefs.font}', Arial, Verdana, sans-serif`
    body.style.fontSize = `${prefs.fontSize}px`
    body.style.lineHeight = String(prefs.lineSpacing)
    body.style.wordSpacing = `${prefs.wordSpacing}px`
    body.style.backgroundColor = prefs.darkMode ? '#1E1E1E' : prefs.overlay

    if (prefs.darkMode) {
      root.classList.add('dark')
      body.classList.add('dark')
    } else {
      root.classList.remove('dark')
      body.classList.remove('dark')
    }
  }, [prefs])

  /* ── Update a single preference ── */
  const updatePref = useCallback((key, value) => {
    setPrefs(prev => ({ ...prev, [key]: value }))
    queueSync({ [key]: value })
  }, [queueSync])

  /* ── Apply a full preset ── */
  const applyPreset = useCallback(presetKey => {
    const preset = PRESETS[presetKey]
    if (!preset) return
    setPrefs(prev => ({ ...prev, ...preset.values }))
    queueSync(preset.values)
  }, [queueSync])

  /* ── Reset to defaults ── */
  const resetPrefs = useCallback(() => {
    setPrefs({ ...DEFAULTS })
    queueSync(DEFAULTS)
  }, [queueSync])

  return { prefs, updatePref, applyPreset, resetPrefs }
}
