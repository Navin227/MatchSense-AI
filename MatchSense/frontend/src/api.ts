const configuredApiOrigin = import.meta.env.VITE_API_BASE_URL?.trim().replace(/\/+$/, '')
const configuredApiBase = configuredApiOrigin ? `${configuredApiOrigin}/api` : null
const useDevProxy = import.meta.env.DEV && !configuredApiOrigin
const API_ORIGIN = configuredApiOrigin || (
  import.meta.env.DEV ? null : 'https://matchsense-ai.onrender.com'
)
const API_BASE = configuredApiBase || (API_ORIGIN ? `${API_ORIGIN}/api` : '/api')

export function apiUrl(path: string) {
  return `${API_BASE}${path.startsWith('/') ? path : `/${path}`}`
}

export function apiWebSocketUrl(path: string) {
  const socketUrl = `${API_BASE}${path.startsWith('/') ? path : `/${path}`}`
  const url = useDevProxy
    ? new URL(socketUrl, window.location.origin)
    : new URL(socketUrl)
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
  return url.toString()
}
