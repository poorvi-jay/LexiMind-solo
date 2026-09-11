export const BASE_URL = import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000'

export const TOKEN_KEY = 'leximind-token'

export function getToken() {
  try {
    return localStorage.getItem(TOKEN_KEY)
  } catch {
    return null // storage disabled (private mode)
  }
}

export function setToken(token) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token)
    else localStorage.removeItem(TOKEN_KEY)
  } catch {
    /* storage full / disabled */
  }
}

/**
 * Error carrying the HTTP status, so callers can distinguish an expired
 * session (401) from a genuine failure.
 */
export class ApiError extends Error {
  constructor(message, status) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

async function request(method, path, body = null, isFormData = false, { keepalive = false } = {}) {
  const headers = isFormData ? {} : { 'Content-Type': 'application/json' }

  // Every protected endpoint expects a bearer token; read it per-request so a
  // login or logout takes effect immediately without re-creating the client.
  const token = getToken()
  if (token) headers.Authorization = `Bearer ${token}`

  const res = await fetch(`${BASE_URL}${path}`, {
    method,
    headers,
    body: isFormData ? body : body ? JSON.stringify(body) : null,
    // Lets a request outlive the page that sent it, for reports fired as the
    // tab closes. Browsers cap keepalive bodies at 64 KB.
    keepalive,
  })

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Unknown error' }))
    let detail = err.detail || 'Request failed'
    // FastAPI validation errors arrive as an array of issue objects
    if (Array.isArray(detail)) {
      detail = detail.map(d => d.msg).filter(Boolean).join('. ') || 'Request failed'
    }
    throw new ApiError(detail, res.status)
  }

  return res.json()
}

export const api = {
  get:  path => request('GET', path),
  post: (path, body, options) => request('POST', path, body, false, options),
  postForm: (path, formData) => request('POST', path, formData, true),
  patch: (path, body) => request('PATCH', path, body),
  delete: path => request('DELETE', path),
}
