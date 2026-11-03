const configuredApiOrigin = import.meta.env.VITE_API_BASE_URL?.trim().replace(/\/+$/, '')
const API_BASE = configuredApiOrigin ? `${configuredApiOrigin}/api` : '/api'

export function apiUrl(path: string) {
  return `${API_BASE}${path.startsWith('/') ? path : `/${path}`}`
}

export function apiWebSocketUrl(path: string) {
  const url = new URL(apiUrl(path), window.location.origin)
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
  return url.toString()
}
