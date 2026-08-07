export const BASE_URL = import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000'

async function request(method, path, body = null, isFormData = false) {
  const headers = isFormData ? {} : { 'Content-Type': 'application/json' }

  const res = await fetch(`${BASE_URL}${path}`, {
    method,
    headers,
    body: isFormData ? body : body ? JSON.stringify(body) : null,
  })

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Unknown error' }))
    throw new Error(err.detail || 'Request failed')
  }

  return res.json()
}

export const api = {
  get:  path => request('GET', path),
  post: (path, body) => request('POST', path, body),
  postForm: (path, formData) => request('POST', path, formData, true),
}