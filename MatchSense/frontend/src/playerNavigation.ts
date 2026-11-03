import type { AnalysisProps, PlayerSelection } from './MatchAnalysisSections'

export type PlayerAnalysisNavigationState = {
  player: PlayerSelection
  analysisProps: AnalysisProps
  returnTo: string
  replaySnapshot?: unknown
}

export type CachedPlayerAnalysis = PlayerAnalysisNavigationState & {
  fixtureId: string
  role: PlayerSelection['role']
  routePlayerId: string
  source: 'live' | 'replay'
}

const PLAYER_CACHE_KEY = 'matchsense:player-analysis-route'
export const REPLAY_CACHE_KEY = 'matchsense:replay-route'

export function playerRouteId(player: PlayerSelection) {
  if (player.playerId != null && String(player.playerId).trim()) return String(player.playerId)
  const slug = player.name
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '')
  return `name-${slug || 'unavailable'}`
}

export function playerAnalysisPath(
  fixtureId: number | string,
  player: PlayerSelection,
  source: 'live' | 'replay',
  extraQuery: Record<string, string> = {},
) {
  const query = new URLSearchParams({ innings: player.innings, source, ...extraQuery })
  return `/match/${encodeURIComponent(String(fixtureId))}/player/${player.role}/${encodeURIComponent(playerRouteId(player))}?${query}`
}

export function savePlayerAnalysisCache(value: CachedPlayerAnalysis) {
  try {
    sessionStorage.setItem(PLAYER_CACHE_KEY, JSON.stringify(value))
  } catch (error) {
    console.warn('Player analysis route cache could not be saved.', error)
  }
}

export function readPlayerAnalysisCache(): CachedPlayerAnalysis | null {
  try {
    const raw = sessionStorage.getItem(PLAYER_CACHE_KEY)
    if (!raw) return null
    const parsed: unknown = JSON.parse(raw)
    if (
      typeof parsed !== 'object'
      || parsed === null
      || !('fixtureId' in parsed)
      || !('role' in parsed)
      || !('routePlayerId' in parsed)
      || !('player' in parsed)
      || !('analysisProps' in parsed)
    ) return null
    const cache = parsed as CachedPlayerAnalysis
    if (
      typeof cache.fixtureId !== 'string'
      || (cache.role !== 'batter' && cache.role !== 'bowler')
      || typeof cache.routePlayerId !== 'string'
      || !cache.player
      || !Array.isArray(cache.analysisProps?.deliveries)
    ) return null
    return cache
  } catch (error) {
    console.warn('Player analysis route cache could not be read.', error)
    return null
  }
}

export function saveReplayRouteCache(value: unknown) {
  try {
    sessionStorage.setItem(REPLAY_CACHE_KEY, JSON.stringify(value))
  } catch (error) {
    console.warn('Replay route state could not be saved.', error)
  }
}

export function readReplayRouteCache(): unknown {
  try {
    const raw = sessionStorage.getItem(REPLAY_CACHE_KEY)
    return raw ? JSON.parse(raw) as unknown : null
  } catch (error) {
    console.warn('Replay route state could not be restored.', error)
    return null
  }
}
