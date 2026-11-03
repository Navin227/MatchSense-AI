import { useEffect, useMemo, useState } from 'react'
import { Activity, ArrowLeft, LoaderCircle } from 'lucide-react'
import { useLocation, useNavigate, useParams } from 'react-router-dom'
import { apiUrl } from './api'
import {
  buildScorecardsFromDeliveries,
  PlayerAnalysisDetail,
  type AnalysisProps,
  type Delivery,
  type Innings,
  type PlayerSelection,
  type Team,
} from './MatchAnalysisSections'
import {
  playerRouteId,
  readPlayerAnalysisCache,
  type PlayerAnalysisNavigationState,
} from './playerNavigation'

type MatchResponse = {
  id: number | string | null
  match_format: string | null
  is_finished: boolean
  localteam: Team | null
  visitorteam: Team | null
  innings: Innings[]
  scorecards: AnalysisProps['scorecards']
  deliveries: Delivery[]
}

type ReplayResponse = {
  state: {
    id: number | string | null
    match_format: string | null
    localteam: Team | null
    visitorteam: Team | null
    innings: Innings[]
    deliveries: Delivery[]
    delivery_index: number
    total_deliveries: number
  }
}

type LoadedAnalysis = {
  props: AnalysisProps
  player: PlayerSelection
}

function isNavigationState(value: unknown): value is PlayerAnalysisNavigationState {
  if (!value || typeof value !== 'object') return false
  const state = value as Partial<PlayerAnalysisNavigationState>
  return Boolean(
    state.player
    && state.analysisProps
    && Array.isArray(state.analysisProps.deliveries)
    && typeof state.returnTo === 'string',
  )
}

function matchesRoute(
  candidate: PlayerAnalysisNavigationState,
  fixtureId: string,
  role: PlayerSelection['role'],
  routePlayerId: string,
  innings: string | null,
) {
  return String(candidate.analysisProps.fixtureId) === fixtureId
    && candidate.player.role === role
    && playerRouteId(candidate.player) === routePlayerId
    && (innings === null || candidate.player.innings === innings)
}

function findPlayer(
  props: AnalysisProps,
  role: PlayerSelection['role'],
  routePlayerId: string,
  innings: string | null,
): PlayerSelection | null {
  const cards = innings
    ? props.scorecards.filter((card) => card.innings === innings)
    : props.scorecards
  for (const card of cards) {
    const players = role === 'batter' ? card.batting : card.bowling
    const player = players.find((item) => {
      if (item.player_id != null) return String(item.player_id) === routePlayerId
      return playerRouteId({
        role,
        playerId: null,
        name: item.name,
        innings: card.innings,
      }) === routePlayerId
    })
    if (player) {
      return {
        role,
        playerId: player.player_id,
        name: player.name,
        innings: card.innings,
      }
    }
  }
  return null
}

async function fetchMatchAnalysis(fixtureId: string): Promise<AnalysisProps> {
  const response = await fetch(apiUrl(`/matches/${encodeURIComponent(fixtureId)}`))
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: string } | null
    throw new Error(body?.detail ?? `Unable to load fixture ${fixtureId} (${response.status}).`)
  }
  const match = await response.json() as MatchResponse
  return {
    teams: [match.localteam, match.visitorteam],
    innings: match.innings,
    scorecards: match.scorecards,
    deliveries: match.deliveries,
    matchFormat: match.match_format,
    isFinished: match.is_finished,
    fixtureId: match.id ?? fixtureId,
    totalDeliveries: match.is_finished ? match.deliveries.length : match.deliveries.length + 1,
    narrativeSource: 'live',
  }
}

async function fetchReplayAnalysis(sessionId: string, checkpoint: number): Promise<AnalysisProps> {
  const response = await fetch(apiUrl(`/replay/${encodeURIComponent(sessionId)}`))
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: string } | null
    throw new Error(body?.detail ?? 'The replay session is no longer available. Return to the replay library and reload it.')
  }
  const replay = await response.json() as ReplayResponse
  const state = replay.state
  if (state.delivery_index !== checkpoint) {
    throw new Error('The saved replay checkpoint is no longer available. Return to the replay and select the checkpoint again.')
  }
  return {
    teams: [state.localteam, state.visitorteam],
    innings: state.innings,
    scorecards: buildScorecardsFromDeliveries(state.deliveries, state.innings),
    deliveries: state.deliveries,
    matchFormat: state.match_format,
    isFinished: state.delivery_index >= state.total_deliveries,
    fixtureId: state.id,
    totalDeliveries: state.total_deliveries,
    narrativeSource: 'replay',
  }
}

export default function PlayerAnalysisRoute({ forcedRole }: { forcedRole: PlayerSelection['role'] }) {
  const { fixtureId = '', playerId = '' } = useParams()
  const location = useLocation()
  const navigate = useNavigate()
  const query = useMemo(() => new URLSearchParams(location.search), [location.search])
  const requestedInnings = query.get('innings')
  const source = query.get('source') === 'replay' ? 'replay' : 'live'
  const routeRole = forcedRole
  const routeState = isNavigationState(location.state) ? location.state : null
  const cached = useMemo(() => readPlayerAnalysisCache(), [location.key])
  const cachedState = source === 'replay' && cached
    && cached.fixtureId === fixtureId
    && cached.role === routeRole
    && cached.routePlayerId === playerId
    ? cached
    : null
  const initialState = routeState && routeRole && matchesRoute(routeState, fixtureId, routeRole, playerId, requestedInnings)
    ? routeState
    : cachedState
  const [loaded, setLoaded] = useState<LoadedAnalysis | null>(() => initialState
    ? { props: initialState.analysisProps, player: initialState.player }
    : null)
  const [loading, setLoading] = useState(!initialState)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let active = true
    if (!fixtureId || !routeRole) {
      setLoaded(null)
      setLoading(false)
      setError('This player analysis URL is invalid.')
      return () => { active = false }
    }
    if (initialState) {
      setLoaded({ props: initialState.analysisProps, player: initialState.player })
      setLoading(false)
      setError(null)
      return () => { active = false }
    }

    setLoaded(null)
    setLoading(true)
    setError(null)
    const load = async () => {
      try {
        const props = source === 'replay'
          ? await fetchReplayAnalysis(query.get('session') ?? '', Number(query.get('checkpoint')))
          : await fetchMatchAnalysis(fixtureId)
        const player = findPlayer(props, routeRole, playerId, requestedInnings)
        if (!player) {
          throw new Error(`Player ${playerId} was not found in fixture ${fixtureId}${requestedInnings ? `, innings ${requestedInnings}` : ''}.`)
        }
        if (active) setLoaded({ props, player })
      } catch (reason) {
        if (active) setError(reason instanceof Error ? reason.message : 'Unable to load this player analysis.')
      } finally {
        if (active) setLoading(false)
      }
    }
    void load()
    return () => { active = false }
  }, [fixtureId, initialState, playerId, query, requestedInnings, routeRole, source])

  const backToMatch = () => {
    const returnTo = routeState?.returnTo
      ?? cachedState?.returnTo
      ?? `/match/${encodeURIComponent(fixtureId)}?tab=intelligence`
    if (routeState?.returnTo) navigate(-1)
    else navigate(returnTo, { replace: true })
  }

  return (
    <div className="app-shell player-route-shell">
      <header className="topbar">
        <button className="player-back" type="button" onClick={backToMatch}>
          <ArrowLeft size={15} /> Back to Match Intelligence
        </button>
        <div className="topbar-right"><Activity size={17} /><strong>MatchSense AI</strong></div>
      </header>
      <main className="player-route-main">
        {loading && <div className="state-panel loading-state"><LoaderCircle className="spin" size={24} /><span>Loading player analysis</span></div>}
        {!loading && error && (
          <section className="state-panel empty-state" role="alert">
            <h2>Player analysis unavailable</h2>
            <p>{error}</p>
            <button className="player-back" type="button" onClick={backToMatch}><ArrowLeft size={15} /> Back to Match Intelligence</button>
          </section>
        )}
        {!loading && !error && loaded && (
          <PlayerAnalysisDetail
            props={loaded.props}
            player={loaded.player}
            onBack={backToMatch}
          />
        )}
      </main>
    </div>
  )
}
