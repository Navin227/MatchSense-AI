import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Activity, AlertCircle, ArrowLeft, BarChart3, LoaderCircle, Pause, Play, RotateCcw, Shield, Zap } from 'lucide-react'
import {
  buildScorecardsFromDeliveries,
  FullMatchAnalysis,
  type Delivery,
  type Innings,
  type PlayerSelection,
  type Team,
} from './MatchAnalysisSections'
import { useLocation, useNavigate, useParams } from 'react-router-dom'
import {
  playerAnalysisPath,
  playerRouteId,
  readReplayRouteCache,
  savePlayerAnalysisCache,
  saveReplayRouteCache,
} from './playerNavigation'

type ReplayDelivery = Delivery & {
  innings_runs: number
  innings_wickets?: number
  innings_overs?: string
}
type Batter = {
  player_id: number | string | null
  name: string
  runs: number
  balls: number
  fours: number
  sixes: number
  strike_rate: number
  is_current: boolean
}
type Bowler = {
  player_id: number | string | null
  name: string
  overs: string
  legal_balls: number
  maidens: number
  runs: number
  wickets: number
  economy: number
  is_current: boolean
}
type MatchState = {
  id: number | string | null
  match_format: string | null
  localteam: Team | null
  visitorteam: Team | null
  innings: Innings[]
  batting: Batter[]
  bowling: Bowler[]
  current_batters: Batter[]
  current_bowler: Bowler | null
  deliveries: ReplayDelivery[]
  progression: { innings: string; over_ball: string; runs: number }[]
  momentum: { innings: string; over: string; runs: number }[]
  current_score: Innings | null
  delivery_index: number
  total_deliveries: number
}
type Replay = {
  session_id: string
  mode: string
  status: 'ready' | 'playing' | 'paused' | 'finished'
  speed: number
  available_speeds: number[]
  state: MatchState
}
type ReplayEvent = {
  type: string
  state?: MatchState
  status?: Replay['status']
  speed?: number
  message?: string
}
type ReplaySeason = { id: number; name: string }
type ReplayCompetition = { id: number; name: string; seasons: ReplaySeason[] }
type ReplayLibraryMatch = {
  fixture_id: number
  starting_at: string | null
  localteam: string
  visitorteam: string
  status: string
  result: string | null
  innings: { team: string; runs: number | null; wickets: number | null; overs: number | null }[]
  delivery_count: number
  replay_available: boolean
  data_quality_status: 'ok' | 'warning' | 'unavailable'
  data_quality_issues: string[]
}
type Section = 'overview' | 'scorecard' | 'intelligence'
const API_BASE = import.meta.env.VITE_API_BASE_URL ?? '/api'

function show(value: number | string | null | undefined) {
  return value === null || value === undefined || value === '' ? '—' : value
}

function displayDate(value: string | null) {
  if (!value) return 'Date unavailable'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString()
}

function initials(team: Team | null) {
  return team?.short_name || team?.name?.split(/\s+/).map((part) => part[0]).join('').slice(0, 3) || '—'
}

function socketAddress(sessionId: string) {
  const url = new URL(API_BASE, window.location.origin)
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
  url.pathname = `${url.pathname.replace(/\/$/, '')}/replay/${encodeURIComponent(sessionId)}/ws`
  return url.toString()
}

export default function ReplayMode({ onBack }: { onBack: () => void }) {
  const navigate = useNavigate()
  const location = useLocation()
  const { fixtureId: routeFixtureId } = useParams()
  const [fixtureId, setFixtureId] = useState('')
  const [replay, setReplay] = useState<Replay | null>(null)
  const [section, setSection] = useState<Section>('overview')
  const [loading, setLoading] = useState(false)
  const [connected, setConnected] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [libraryError, setLibraryError] = useState<string | null>(null)
  const [competitions, setCompetitions] = useState<ReplayCompetition[]>([])
  const [competitionsLoading, setCompetitionsLoading] = useState(true)
  const [selectedLeague, setSelectedLeague] = useState('')
  const [selectedSeason, setSelectedSeason] = useState('')
  const [libraryMatches, setLibraryMatches] = useState<ReplayLibraryMatch[]>([])
  const [libraryLoading, setLibraryLoading] = useState(false)
  const [matchesError, setMatchesError] = useState<string | null>(null)
  const [checkpointDraft, setCheckpointDraft] = useState(0)
  const [socketVersion, setSocketVersion] = useState(0)
  const socketRef = useRef<WebSocket | null>(null)

  const loadReplay = useCallback(async (fixtureIdValue: string) => {
    const id = Number(fixtureIdValue.trim())
    if (!Number.isSafeInteger(id) || id < 1) {
      setError('Enter a valid Sportmonks fixture ID.')
      return
    }
    setLoading(true)
    setError(null)
    setReplay(null)
    setSection('overview')
    try {
      const response = await fetch(`${API_BASE}/replay`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ fixture_id: id }),
      })
      if (!response.ok) {
        const body = await response.json().catch(() => null) as { detail?: string } | null
        throw new Error(body?.detail ?? `Unable to load fixture ${id} (${response.status}).`)
      }
      const result = await response.json() as Replay
      setReplay(result)
      setCheckpointDraft(result.state.delivery_index)
      setSocketVersion((value) => value + 1)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to load this Sportmonks replay.')
    } finally {
      setLoading(false)
    }
  }, [])

  const openReplay = (fixtureIdValue: string) => {
    const id = Number(fixtureIdValue.trim())
    if (!Number.isSafeInteger(id) || id < 1) {
      setError('Enter a valid Sportmonks fixture ID.')
      return
    }
    setFixtureId(String(id))
    try {
      sessionStorage.removeItem('matchsense:replay-route')
    } catch (reason) {
      console.warn('Old replay route state could not be cleared.', reason)
    }
    navigate(`/replay/${encodeURIComponent(String(id))}`)
  }

  useEffect(() => {
    if (routeFixtureId) return
    setReplay(null)
    setConnected(false)
    setLoading(false)
    setError(null)
  }, [routeFixtureId])

  useEffect(() => {
    if (!routeFixtureId) return
    setFixtureId(routeFixtureId)
    const saved: unknown = readReplayRouteCache()
    if (saved && typeof saved === 'object' && 'replay' in saved) {
      const candidate = saved as { replay?: Replay; section?: Section; checkpointDraft?: number }
      if (
        String(candidate.replay?.state?.id ?? '') === routeFixtureId
        && candidate.replay?.session_id
        && candidate.replay.state
      ) {
        setReplay(candidate.replay)
        setSection(candidate.section === 'intelligence' ? 'intelligence' : 'overview')
        setCheckpointDraft(
          typeof candidate.checkpointDraft === 'number'
            ? candidate.checkpointDraft
            : candidate.replay.state.delivery_index,
        )
        setSocketVersion((value) => value + 1)
        return
      }
    }
    void loadReplay(routeFixtureId)
  }, [loadReplay, routeFixtureId])

  useEffect(() => {
    if (routeFixtureId) {
      setCompetitionsLoading(false)
      return
    }
    let active = true
    const loadLibrary = async () => {
      try {
        const response = await fetch(`${API_BASE}/replay/library`)
        if (!response.ok) {
          const body = await response.json().catch(() => null) as { detail?: string } | null
          throw new Error(body?.detail ?? `Unable to load replay competitions (${response.status}).`)
        }
        const result = await response.json() as { competitions?: ReplayCompetition[] }
        const available = result.competitions ?? []
        if (!active) return
        setCompetitions(available)
        const firstCompetition = available[0]
        setSelectedLeague(firstCompetition ? String(firstCompetition.id) : '')
        setSelectedSeason(firstCompetition?.seasons[0] ? String(firstCompetition.seasons[0].id) : '')
      } catch (reason) {
        if (active) setLibraryError(reason instanceof Error ? reason.message : 'Unable to load replay competitions.')
      } finally {
        if (active) setCompetitionsLoading(false)
      }
    }
    void loadLibrary()
    return () => { active = false }
  }, [routeFixtureId])

  useEffect(() => {
    if (routeFixtureId) return
    if (!selectedLeague || !selectedSeason) {
      setLibraryMatches([])
      setLibraryLoading(false)
      return
    }
    let active = true
    const loadMatches = async () => {
      setLibraryLoading(true)
      setMatchesError(null)
      try {
        const query = new URLSearchParams({ league_id: selectedLeague, season_id: selectedSeason })
        const response = await fetch(`${API_BASE}/replay/library/matches?${query}`)
        if (!response.ok) {
          const body = await response.json().catch(() => null) as { detail?: string } | null
          throw new Error(body?.detail ?? `Unable to load completed matches (${response.status}).`)
        }
        const result = await response.json() as { matches?: ReplayLibraryMatch[] }
        if (active) setLibraryMatches(result.matches ?? [])
      } catch (reason) {
        if (active) {
          setLibraryMatches([])
          setMatchesError(reason instanceof Error ? reason.message : 'Unable to load completed matches.')
        }
      } finally {
        if (active) setLibraryLoading(false)
      }
    }
    void loadMatches()
    return () => { active = false }
  }, [routeFixtureId, selectedLeague, selectedSeason])

  useEffect(() => {
    if (!routeFixtureId || !replay) return
    saveReplayRouteCache({ replay, section, checkpointDraft })
  }, [checkpointDraft, replay, routeFixtureId, section])

  useEffect(() => {
    if (!replay?.session_id) return
    const socket = new WebSocket(socketAddress(replay.session_id))
    socketRef.current = socket
    socket.onopen = () => setConnected(true)
    socket.onclose = () => {
      if (socketRef.current === socket) socketRef.current = null
      setConnected(false)
    }
    socket.onerror = () => setError('Replay event stream disconnected. Retry the fixture to reconnect.')
    socket.onmessage = (message) => {
      try {
        const event = JSON.parse(message.data) as ReplayEvent & Partial<Replay>
        if (event.type === 'replay_state') {
          setReplay(event as Replay)
          setCheckpointDraft(event.state?.delivery_index ?? 0)
        } else if (event.type === 'delivery_update' && event.state) {
          setReplay((current) => current && ({
            ...current,
            state: event.state!,
            status: event.status ?? current.status,
            speed: event.speed ?? current.speed,
          }))
          setCheckpointDraft(event.state.delivery_index)
        } else if (event.type === 'replay_error') {
          setError(event.message ?? 'Replay command failed.')
        }
      } catch {
        setError('Received an invalid replay event.')
      }
    }
    return () => {
      socketRef.current = null
      socket.close()
    }
  }, [replay?.session_id, socketVersion])

  const state = replay?.state
  const scorecards = useMemo(
    () => buildScorecardsFromDeliveries(state?.deliveries ?? [], state?.innings ?? []),
    [state?.deliveries, state?.innings],
  )
  const currentInnings = state?.current_score ?? null
  const activeCard = scorecards.find((card) => card.innings === String(currentInnings?.innings))
  const recent = state?.deliveries.slice(-10).reverse() ?? []
  const analysisProps = useMemo(() => ({
    teams: [state?.localteam ?? null, state?.visitorteam ?? null],
    innings: state?.innings ?? [],
    scorecards,
    deliveries: state?.deliveries ?? [],
    matchFormat: state?.match_format ?? null,
    isFinished: replay?.status === 'finished'
      || Boolean(state && state.delivery_index >= state.total_deliveries),
    fixtureId: state?.id ?? null,
    totalDeliveries: state?.total_deliveries ?? 0,
    narrativeSource: 'replay' as const,
  }), [replay?.status, scorecards, state])

  const send = (action: string, value?: number) => {
    if (socketRef.current?.readyState !== WebSocket.OPEN) return
    socketRef.current.send(JSON.stringify(value === undefined
      ? { action }
      : { action, ...(action === 'seek' ? { delivery_index: value } : { speed: value }) }))
  }
  const selectPlayer = (player: PlayerSelection) => {
    if (!replay || state?.id == null) {
      setError('Player analysis is unavailable until a replay fixture is loaded.')
      return
    }
    const returnTo = `/replay/${encodeURIComponent(String(state.id))}`
    const routePlayerId = playerRouteId(player)
    const routeState = { player, analysisProps, returnTo, replaySnapshot: replay }
    savePlayerAnalysisCache({
      ...routeState,
      fixtureId: String(state.id),
      role: player.role,
      routePlayerId,
      source: 'replay',
    })
    saveReplayRouteCache({
      replay,
      section: 'intelligence',
      checkpointDraft: state.delivery_index,
    })
    navigate(playerAnalysisPath(state.id, player, 'replay', {
      session: replay.session_id,
      checkpoint: String(state.delivery_index),
    }), { state: routeState })
  }
  const seek = () => send('seek', checkpointDraft)

  return (
    <div className="replay-shell">
      <header className="replay-topbar">
        <button className="replay-back" onClick={() => routeFixtureId ? navigate('/replay') : onBack()}>
          <ArrowLeft size={15} /> {routeFixtureId ? 'Replay library' : 'Match centre'}
        </button>
        <div><Activity size={17} /><strong>MatchSense</strong><span>REPLAY</span></div>
        <span className={`replay-connection ${connected ? 'connected' : ''}`}><i />{connected ? 'REPLAY DATA' : 'DISCONNECTED'}</span>
      </header>
      <main className="replay-main">
        {!routeFixtureId && <section className="replay-page-heading">
          <div><span className="section-kicker">HISTORICAL MATCH DATA · SPORTMONKS</span><h1>Ball-by-ball replay</h1>
            <p>Load a completed fixture only when Sportmonks provides delivery records. Replay state is limited to the selected checkpoint.</p></div>
        </section>}

        {!routeFixtureId && <><section className="replay-library panel replay-browser">
          <div className="replay-browser-heading">
            <div><span className="section-kicker">SPORTMONKS ARCHIVE</span><h2>Browse replay matches</h2></div>
            <small>Competitions and seasons shown here are those available to the configured provider account.</small>
          </div>
          <div className="replay-browser-filters">
            <label>COMPETITION
              <select value={selectedLeague} onChange={(event) => {
                const competition = competitions.find((item) => String(item.id) === event.target.value)
                setSelectedLeague(event.target.value)
                setSelectedSeason(competition?.seasons[0] ? String(competition.seasons[0].id) : '')
              }} disabled={!competitions.length}>
                {competitions.map((competition) => <option key={competition.id} value={competition.id}>{competition.name}</option>)}
              </select>
            </label>
            <label>SEASON / EDITION
              <select value={selectedSeason} onChange={(event) => setSelectedSeason(event.target.value)} disabled={!selectedLeague}>
                {(competitions.find((item) => String(item.id) === selectedLeague)?.seasons ?? []).map((season) =>
                  <option key={season.id} value={season.id}>{season.name}</option>)}
              </select>
            </label>
          </div>
          {competitionsLoading && <p className="replay-library-message"><LoaderCircle className="spin" size={14} /> Loading available competitions</p>}
          {libraryError && <p className="replay-library-message replay-library-error">{libraryError}</p>}
          {matchesError && <p className="replay-library-message replay-library-error">{matchesError}</p>}
          {libraryLoading && <p className="replay-library-message"><LoaderCircle className="spin" size={14} /> Checking completed fixtures and delivery coverage</p>}
          {!libraryLoading && !matchesError && selectedSeason && libraryMatches.length === 0 &&
            <p className="replay-library-message">No completed fixtures were returned for this provider season.</p>}
          {!libraryLoading && libraryMatches.length > 0 && (
            <div className="replay-match-list">
              {libraryMatches.map((match) => (
                <article className="replay-library-match" key={match.fixture_id}>
                  <div className="replay-library-match-info">
                    <span className="replay-match-date">{displayDate(match.starting_at)} · FIXTURE {match.fixture_id}</span>
                    <strong>{match.localteam} <span>vs</span> {match.visitorteam}</strong>
                    <small>{match.result ?? 'Provider result unavailable'}{match.innings.length > 0 &&
                      ` · ${match.innings.map((inning) => `${inning.team} ${show(inning.runs)}/${show(inning.wickets)}`).join(' · ')}`}</small>
                  </div>
                  <div className="replay-library-match-action">
                    <span
                      className={!match.replay_available ? 'coverage-missing' : match.data_quality_status === 'warning' ? 'coverage-warning' : 'coverage-available'}
                      title={match.data_quality_issues.join('\n') || undefined}
                    >
                      {match.replay_available
                        ? `${match.delivery_count} deliveries · ${match.data_quality_status === 'warning' ? 'data warning' : 'reconciled'}`
                        : 'Ball-by-ball unavailable'}
                    </span>
                    <button className="replay-secondary" disabled={!match.replay_available || loading} onClick={() => openReplay(String(match.fixture_id))}>Load replay</button>
                  </div>
                </article>
              ))}
            </div>
          )}
          {competitions.length === 0 && !competitionsLoading && !libraryError && <p className="replay-library-message">No competitions are currently exposed by Sportmonks.</p>}
        </section>

        <section className="replay-library panel replay-manual-fixture">
          <div className="replay-manual-input">
            <label htmlFor="replay-fixture-id">SPORTMONKS FIXTURE ID</label>
            <input id="replay-fixture-id" inputMode="numeric" value={fixtureId} onChange={(event) => setFixtureId(event.target.value)} onKeyDown={(event) => {
              if (event.key === 'Enter') openReplay(fixtureId)
            }} placeholder="Enter a completed fixture ID" />
          </div>
          <button className="replay-primary" disabled={loading || !fixtureId.trim()} onClick={() => openReplay(fixtureId)}>
            Load replay
          </button>
          <small>Only real provider fixtures with a finished status and available ball-by-ball records are replayable. Coverage depends on provider subscription and fixture.</small>
        </section></>}
        {error && <div className="replay-error"><AlertCircle size={16} /><span>{error}</span><button onClick={() => routeFixtureId ? void loadReplay(routeFixtureId) : openReplay(fixtureId)}>Retry</button></div>}
        {!routeFixtureId && !replay && !loading && !error && <div className="replay-state replay-empty"><Shield size={22} /><p>Enter a fixture ID to check its actual delivery coverage.</p></div>}
        {loading && <div className="replay-state"><LoaderCircle className="spin" size={22} /> Checking provider fixture and delivery coverage</div>}
        {routeFixtureId && replay && state && (
          <>
            <section className="replay-match-heading">
              <span className="section-kicker">{state.localteam?.name ?? 'Team unavailable'} vs {state.visitorteam?.name ?? 'Team unavailable'} · FIXTURE {state.id}</span>
              <h2>{currentInnings
                ? `${state.innings.find((inning) => String(inning.innings) === String(currentInnings.innings))?.runs ?? 0}/${currentInnings.wickets ?? 0}`
                : 'Replay ready'}</h2>
              <p>{currentInnings
                ? `Innings ${currentInnings.innings} · ${currentInnings.overs} overs · RR ${currentInnings.run_rate}`
                : 'No deliveries processed yet'} · Replay data · checkpoint {state.delivery_index}/{state.total_deliveries}</p>
            </section>

            <section className="replay-controls">
              <div className="control-actions">
                {replay.status === 'ready' && <button className="replay-primary" disabled={!connected} onClick={() => send('play')}><Play size={14} fill="currentColor" /> Play</button>}
                {replay.status === 'playing' && <button className="replay-secondary" disabled={!connected} onClick={() => send('pause')}><Pause size={14} /> Pause</button>}
                {replay.status === 'paused' && <button className="replay-primary" disabled={!connected} onClick={() => send('resume')}><Play size={14} fill="currentColor" /> Resume</button>}
                {replay.status === 'finished' && <span className="replay-finished">Replay complete</span>}
                <button className="replay-reset" disabled={!connected || replay.status === 'ready'} onClick={() => send('reset')}><RotateCcw size={14} /> Reset</button>
              </div>
              <div className="speed-control"><span>PLAYBACK SPEED</span>
                {replay.available_speeds.map((speed) => <button key={speed} className={replay.speed === speed ? 'speed-selected' : ''} disabled={!connected} onClick={() => send('set_speed', speed)}>{speed}x</button>)}
              </div>
              <div className="replay-checkpoint">
                <label htmlFor="replay-checkpoint">DELIVERY CHECKPOINT {state.delivery_index}/{state.total_deliveries}</label>
                <input id="replay-checkpoint" type="range" min={0} max={state.total_deliveries} value={Math.min(checkpointDraft, state.total_deliveries)} disabled={!connected || replay.status === 'playing'} onChange={(event) => setCheckpointDraft(Number(event.target.value))} />
                <button className="replay-secondary" disabled={!connected || checkpointDraft === state.delivery_index} onClick={seek}>Go to checkpoint</button>
              </div>
            </section>

            <nav className="intelligence-nav" aria-label="Match sections">
              {([
                ['overview', 'Match Overview', Activity],
                ['scorecard', 'Scorecard', BarChart3],
                ['intelligence', 'Match Intelligence', Zap],
              ] as const).map(([id, label, Icon]) => (
                <button key={id} className={section === id ? 'intelligence-tab active' : 'intelligence-tab'} onClick={() => setSection(id)}><Icon size={14} /> {label}</button>
              ))}
              <span className="checkpoint-label">CHECKPOINT {state.delivery_index}/{state.total_deliveries}</span>
            </nav>

            {section === 'overview' && (
              <>
                <section className="replay-scoreboard">
                  <div className="replay-score-teams">
                    {[state.localteam, state.visitorteam].map((team) => {
                      const teamInnings = state.innings.filter((inning) => team?.id != null && String(inning.team_id) === String(team.id))
                      const score = teamInnings[teamInnings.length - 1]
                      return <div className="replay-team" key={String(team?.id ?? team?.name)}>
                        <span className="replay-team-badge">{initials(team)}</span>
                        <div><strong>{team?.name ?? 'Team unavailable'}</strong><small>{team?.short_name ?? 'TEAM'}</small>
                          {score && <span className="replay-team-score">{score.runs}/{score.wickets} · {score.overs} ov</span>}
                        </div>
                      </div>
                    })}
                  </div>
                </section>
                <section className="replay-columns">
                  <div className="replay-panel">
                    <div className="replay-panel-head"><div><span>CHECKPOINT STATE</span><h2>Players in this innings</h2></div></div>
                    <div className="replay-table-scroll"><table><thead><tr><th>BATTER</th><th>R</th><th>B</th><th>4s</th><th>6s</th><th>SR</th></tr></thead><tbody>
                      {(activeCard?.batting ?? []).map((batter) => {
                        const batterInnings = String(currentInnings?.innings ?? '1')
                        return <tr key={batter.player_id ?? batter.name}>
                          <td><button className="player-link" onClick={() => selectPlayer({ role: 'batter', playerId: batter.player_id, name: batter.name, innings: String(batterInnings) })}>{batter.name}</button></td>
                          <td>{show(batter.runs)}</td><td>{show(batter.balls)}</td><td>{show(batter.fours)}</td><td>{show(batter.sixes)}</td><td>{batter.strike_rate == null ? '—' : batter.strike_rate.toFixed(1)}</td>
                        </tr>
                      })}
                    </tbody></table></div>
                    {!state.deliveries.length && <p className="replay-placeholder">Player figures update after the selected checkpoint.</p>}
                    {state.current_bowler && <button className="player-analysis-links" onClick={() => selectPlayer({
                      role: 'bowler',
                      playerId: state.current_bowler?.player_id ?? null,
                      name: state.current_bowler?.name ?? '',
                      innings: String(currentInnings?.innings ?? '1'),
                    })}>Analyze {state.current_bowler.name} · bowling</button>}
                  </div>
                  <div className="replay-panel replay-deliveries">
                    <div className="replay-panel-head"><div><span>PROCESSED DELIVERY RECORDS</span><h2>Recent deliveries</h2></div><b>{state.delivery_index}/{state.total_deliveries}</b></div>
                    {recent.length ? recent.map((delivery) => <div className="replay-ball" key={delivery.source_id}>
                      <span>{delivery.over_ball ?? '—'}</span>
                      <b className={delivery.is_wicket ? 'ball-wicket' : delivery.four || delivery.six ? 'ball-boundary' : ''}>{delivery.is_wicket ? 'W' : delivery.six ? '6' : delivery.four ? '4' : delivery.runs_total}</b>
                      <span>{delivery.is_wicket ? delivery.dismissal ?? 'Wicket' : `${delivery.batter ?? 'Batter'} · ${delivery.bowler ?? 'Bowler'}`}</span>
                      <strong>{delivery.innings_runs}</strong>
                    </div>) : <p className="replay-placeholder">No deliveries processed yet.</p>}
                  </div>
                </section>
              </>
            )}

            {section === 'scorecard' && (
              <div className="scorecard-list">
                {scorecards.length ? scorecards.map((card) => {
                  const team = [state.localteam, state.visitorteam].find((item) => item?.id != null && String(item.id) === String(card.team_id)) ?? null
                  return <section className="panel scorecard-panel" key={card.innings}>
                    <div className="scorecard-title"><div><span className="section-kicker">INNINGS {card.innings} · CHECKPOINT</span><h2>{team?.name ?? 'Team unavailable'}</h2></div>
                      <div className="scorecard-total"><strong>{show(card.runs)}/{show(card.wickets)}</strong><span>{show(card.overs)} ov · RR {show(card.run_rate)}</span></div>
                    </div>
                    <div className="scorecard-table-wrap"><table className="scorecard-table">
                      <thead><tr><th>BATTER</th><th>DISMISSAL</th><th>R</th><th>B</th><th>4s</th><th>6s</th><th>SR</th></tr></thead>
                      <tbody>{card.batting.map((batter) => <tr key={batter.player_id ?? batter.name}>
                        <td><button className="player-link" onClick={() => selectPlayer({ role: 'batter', playerId: batter.player_id, name: batter.name, innings: card.innings })}>{batter.name}</button></td>
                        <td>{batter.dismissal ?? 'not out'}</td><td>{show(batter.runs)}</td><td>{show(batter.balls)}</td><td>{show(batter.fours)}</td><td>{show(batter.sixes)}</td><td>{show(batter.strike_rate)}</td>
                      </tr>)}</tbody>
                    </table></div>
                    <div className="scorecard-table-wrap"><table className="scorecard-table bowling-scorecard">
                      <thead><tr><th>BOWLER</th><th>O</th><th>M</th><th>R</th><th>W</th><th>ECON</th></tr></thead>
                      <tbody>{card.bowling.map((bowler) => <tr key={bowler.player_id ?? bowler.name}>
                        <td><button className="player-link" onClick={() => selectPlayer({ role: 'bowler', playerId: bowler.player_id, name: bowler.name, innings: card.innings })}>{bowler.name}</button></td>
                        <td>{bowler.overs}</td><td>{bowler.maidens}</td><td>{show(bowler.runs)}</td><td>{bowler.wickets}</td><td>{show(bowler.economy)}</td>
                      </tr>)}</tbody>
                    </table></div>
                  </section>
                }) : <section className="panel scorecard-empty-panel">Scorecard is unavailable because no delivery records have reached this checkpoint.</section>}
              </div>
            )}

            {section === 'intelligence' && (
              <FullMatchAnalysis {...analysisProps} onPlayerSelect={selectPlayer} />
            )}
          </>
        )}
      </main>
    </div>
  )
}
