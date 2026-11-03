import { useCallback, useEffect, useMemo, useState } from 'react'
import { Navigate, Route, Routes, useLocation, useNavigate, useParams } from 'react-router-dom'
import { apiUrl } from './api'
import {
  Activity,
  AlertCircle,
  ArrowDown,
  ArrowUp,
  BarChart3,
  CircleHelp,
  Clock3,
  Crosshair,
  LoaderCircle,
  Radio,
  RefreshCw,
  Shield,
  Target,
  Trophy,
  Zap,
} from 'lucide-react'
import ReplayMode from './ReplayMode'
import PlayerAnalysisRoute from './PlayerAnalysisRoute'
import { FullMatchAnalysis, type AnalysisProps, type PlayerSelection } from './MatchAnalysisSections'
import { playerAnalysisPath, playerRouteId, savePlayerAnalysisCache } from './playerNavigation'
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

type Team = { id: number | string | null; name: string; short_name: string | null; image_path?: string | null }
type Score = { runs: number | null; wickets: number | null; overs: string | null; run_rate: number | null }
type MatchSummary = {
  id: number | string | null
  status: string | null
  note: string | null
  localteam: Team | null
  visitorteam: Team | null
  score: Score
  localteam_score: Score | null
  visitorteam_score: Score | null
}
type LiveMatchesResponse = { matches: MatchSummary[]; poll_interval_seconds: number; updated_at: string }
type Innings = {
  id: number | string | null
  team_id: number | string | null
  innings: number | string
  runs: number | null
  wickets: number | null
  legal_balls?: number | null
  overs: string | null
  run_rate: number | null
}
type Batter = {
  player_id: number | string | null
  name: string
  runs: number | null
  balls: number | null
  fours: number | null
  sixes: number | null
  strike_rate: number | null
  is_current: boolean
  current_role?: 'striker' | 'non_striker' | null
}
type Bowler = {
  player_id: number | string | null
  name: string
  overs: string | null
  maidens: number | null
  runs: number | null
  wickets: number | null
  economy: number | null
  is_current: boolean
}
type Delivery = {
  source_id: number | string
  over_ball: string | null
  innings: string
  team_id: number | string | null
  batter_id: number | string | null
  non_striker_id: number | string | null
  bowler_id: number | string | null
  dismissed_batter_id: number | string | null
  batter: string | null
  non_striker: string | null
  bowler: string | null
  dismissed_batter: string | null
  runs_batter: number | null
  runs_total: number
  runs_extras: number
  runs_bowler: number | null
  innings_runs: number
  four: boolean
  six: boolean
  is_wicket: boolean
  bowler_wicket: boolean
  is_retired_hurt: boolean
  is_legal: number | boolean | null
  is_wide: boolean
  is_noball: boolean
  extras: { wides: number | null; no_balls: number | null; byes: number | null; leg_byes: number | null; penalty?: number | null } | null
  dismissal: string | null
  fielder_id: number | string | null
  fielder: string | null
  runout_by_id: number | string | null
  runout_by: string | null
}
type MatchValidation = {
  status: 'ok' | 'warning'
  issues: { code: string; message: string }[]
  target: number | null
  required_runs: number | null
  required_balls: number | null
  required_run_rate: number | null
  current_run_rate: number | null
}
type LiveMatchIntelligence = {
  situation: {
    summary: string
    batting_team: Team | null
    bowling_team: Team | null
    score: { runs: number; wickets: number; overs: string; run_rate: number } | null
    pressure: string
    factors: string[]
    strengths: string[]
    weaknesses: string[]
    momentum: { over: string; runs: number }[]
    momentum_shifts: string[]
    recent_over_events: string[]
    measured_metrics: {
      legal_balls: number
      balls_faced: number
      dot_balls: number
      dot_ball_percentage: number | null
      boundary_deliveries: number
      boundary_percentage: number | null
      wickets: number
      balls_per_wicket: number | null
    }
    match_phase: string | null
    current_partnership: {
      batters: string[]
      runs: number
      balls: number
      contributions: { name: string; runs: number; balls: number }[]
    } | null
    available_bowlers: { name: string; overs: string | null; legal_balls: number; runs: number; wickets: number; economy: number }[]
  }
  win_probability: {
    available: boolean
    label: string
    reason?: string
    batting_team_probability: number | null
    bowling_team_probability: number | null
    factors: { name: string; value: string | number | null }[]
  }
}
type MatchAnalytics = {
  id: number | string | null
  match_format: string | null
  status: string | null
  note: string | null
  is_finished: boolean
  localteam: Team | null
  visitorteam: Team | null
  innings: Innings[]
  scorecards: InningsScorecard[]
  validation: MatchValidation
  batting: Batter[]
  bowling: Bowler[]
  current_batters: Batter[]
  current_bowler: Bowler | null
  deliveries: Delivery[]
  progression: { innings: string; over_ball: string; runs: number }[]
  momentum: { innings: string; over: string; runs: number }[]
  intelligence: LiveMatchIntelligence
  poll_interval_seconds: number
  updated_at: string
}
type ScorecardBatter = {
  player_id: number | string | null
  name: string
  dismissal: string | null
  runs: number | null
  balls: number | null
  fours: number | null
  sixes: number | null
  strike_rate: number | null
  is_current: boolean
  current_role?: 'striker' | 'non_striker' | null
}
type ScorecardBowler = {
  player_id: number | string | null
  name: string
  overs: string | null
  maidens: number | null
  runs: number | null
  wickets: number | null
  economy: number | null
  wides: number | null
  no_balls: number | null
}
type InningsScorecard = {
  innings: string
  team_id: number | string | null
  runs: number | null
  wickets: number | null
  legal_balls: number | null
  overs: string | null
  run_rate: number | null
  extras: { wides: number | null; no_balls: number | null; byes: number | null; leg_byes: number | null; penalty: number | null }
  batting: ScorecardBatter[]
  bowling: ScorecardBowler[]
  fall_of_wickets: {
    wicket: number
    score: number
    over: string | null
    batter: string | null
    batter_id: number | string | null
    dismissal: string | null
    fielder_id: number | string | null
    fielder: string | null
    bowler_id: number | string | null
    bowler: string | null
    runout_by_id: number | string | null
    runout_by: string | null
  }[]
  did_not_bat: { player_id: number | string | null; name: string }[]
  partnership: {
    number: number
    wicket: number | null
    completed: boolean
    current: boolean
    batters: string[]
    runs: number
    balls: number
    contributions: { player_id: number | string | null; name: string; runs: number; balls: number }[]
  } | null
  partnerships: {
    number: number
    wicket: number | null
    completed: boolean
    current: boolean
    batters: string[]
    runs: number
    balls: number
    contributions: { player_id: number | string | null; name: string; runs: number; balls: number }[]
  }[]
  powerplay: { label: string; overs: string | null; boundary_legal_balls?: number | null; runs: number; wickets: number; legal_balls: number; run_rate: number | null; complete: boolean } | null
}

const EMPTY_SCORE: Score = { runs: null, wickets: null, overs: null, run_rate: null }

class ApiRequestError extends Error {
  retryable: boolean
  retryAfterSeconds: number | null
  category: string | null

  constructor(message: string, retryable: boolean, retryAfterSeconds: number | null, category: string | null) {
    super(message)
    this.retryable = retryable
    this.retryAfterSeconds = retryAfterSeconds
    this.category = category
  }
}

async function requestJson<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(apiUrl(path), options)
  if (!response.ok) {
    const body = await response.json().catch(() => null) as {
      detail?: string
      retryable?: boolean
      retry_after_seconds?: number | null
      category?: string
    } | null
    const detail = typeof body?.detail === 'string' ? body.detail : `Request failed (${response.status})`
    throw new ApiRequestError(
      detail,
      body?.retryable ?? (response.status === 429 || response.status >= 500),
      body?.retry_after_seconds ?? null,
      body?.category ?? null,
    )
  }
  return response.json() as Promise<T>
}

function getJson<T>(path: string): Promise<T> {
  return requestJson<T>(path)
}

function display(value: number | string | null | undefined, fallback = '—') {
  return value === null || value === undefined || value === '' ? fallback : value
}

function teamInitials(team: Team | null) {
  return team?.short_name || team?.name?.split(/\s+/).map((part) => part[0]).join('').slice(0, 3) || '—'
}

function TeamBadge({ team, large = false }: { team: Team | null; large?: boolean }) {
  return (
    <span className={large ? 'large-team-badge' : 'team-badge'}>
      {team?.image_path
        ? <img src={team.image_path} alt="" loading="lazy" referrerPolicy="no-referrer" />
        : teamInitials(team)}
    </span>
  )
}

function formatUpdated(value: string | null) {
  if (!value) return 'Waiting for first update'
  return new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' }).format(new Date(value))
}

function MatchCentre() {
  const { fixtureId: routeFixtureId } = useParams()
  const location = useLocation()
  const navigate = useNavigate()
  const routeTab = new URLSearchParams(location.search).get('tab')
  const [activeTab, setActiveTab] = useState<'overview' | 'scorecard' | 'intelligence'>(
    routeTab === 'scorecard' || routeTab === 'intelligence' ? routeTab : 'overview',
  )
  const [matches, setMatches] = useState<MatchSummary[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(routeFixtureId ?? null)
  const [analytics, setAnalytics] = useState<MatchAnalytics | null>(null)
  const [pollInterval, setPollInterval] = useState(30)
  const [lastUpdated, setLastUpdated] = useState<string | null>(null)
  const [loadingMatches, setLoadingMatches] = useState(true)
  const [loadingDetail, setLoadingDetail] = useState(false)
  const [refreshKey, setRefreshKey] = useState(0)
  const [matchesError, setMatchesError] = useState<string | null>(null)
  const [detailError, setDetailError] = useState<string | null>(null)
  const [pollingSuspended, setPollingSuspended] = useState(false)

  useEffect(() => {
    if (routeFixtureId) {
      setSelectedId(routeFixtureId)
      setActiveTab(routeTab === 'scorecard' || routeTab === 'intelligence' ? routeTab : 'overview')
    }
  }, [routeFixtureId, routeTab])

  useEffect(() => {
    if (!routeFixtureId || activeTab !== 'intelligence' || !analytics) return
    const key = `matchsense:match-scroll:${routeFixtureId}`
    const savedScroll = sessionStorage.getItem(key)
    if (savedScroll === null) return
    sessionStorage.removeItem(key)
    window.requestAnimationFrame(() => window.scrollTo(0, Number(savedScroll)))
  }, [activeTab, analytics, routeFixtureId])

  const refreshMatches = useCallback(async (): Promise<{ retryable: boolean; retryAfterSeconds: number | null }> => {
    try {
      const result = await getJson<LiveMatchesResponse>('/matches/live')
      setMatches(result.matches)
      setPollInterval(Math.max(5, result.poll_interval_seconds))
      setLastUpdated(result.updated_at)
      setMatchesError(null)
      setSelectedId((current) => current ?? (result.matches[0]?.id == null ? null : String(result.matches[0].id)))
      return { retryable: true, retryAfterSeconds: null }
    } catch (error) {
      setMatchesError(error instanceof Error ? error.message : 'Unable to load live matches.')
      if (error instanceof ApiRequestError && !error.retryable) setPollingSuspended(true)
      return error instanceof ApiRequestError
        ? { retryable: error.retryable, retryAfterSeconds: error.retryAfterSeconds }
        : { retryable: true, retryAfterSeconds: null }
    } finally {
      setLoadingMatches(false)
    }
  }, [])

  useEffect(() => {
    if (pollingSuspended) return
    let active = true
    let timer = 0
    const poll = async () => {
      const result = await refreshMatches()
      if (active && result.retryable) {
        timer = window.setTimeout(() => void poll(), (result.retryAfterSeconds ?? pollInterval) * 1000)
      }
    }
    void poll()
    return () => {
      active = false
      window.clearTimeout(timer)
    }
  }, [pollInterval, pollingSuspended, refreshMatches])

  useEffect(() => {
    if (pollingSuspended) return
    if (!selectedId) {
      setAnalytics(null)
      setLoadingDetail(false)
      setDetailError(null)
      return
    }

    let active = true
    let timer = 0
    const poll = async () => {
      setLoadingDetail(true)
      let finished = false
      let retryable = true
      let retryAfterSeconds: number | null = null
      try {
        const result = await getJson<MatchAnalytics>(`/matches/${encodeURIComponent(selectedId)}`)
        if (active) {
          setAnalytics(result)
          setPollInterval(Math.max(5, result.poll_interval_seconds))
          setLastUpdated(result.updated_at)
          setDetailError(null)
          finished = result.is_finished
        }
      } catch (error) {
        if (active) {
          setDetailError(error instanceof Error ? error.message : 'Unable to load match analytics.')
          if (error instanceof ApiRequestError) {
            retryable = error.retryable
            retryAfterSeconds = error.retryAfterSeconds
            if (!error.retryable) setPollingSuspended(true)
          }
        }
      } finally {
        if (active) {
          setLoadingDetail(false)
          if (!finished && retryable) {
            timer = window.setTimeout(() => void poll(), (retryAfterSeconds ?? pollInterval) * 1000)
          }
        }
      }
    }

    void poll()
    return () => {
      active = false
      window.clearTimeout(timer)
    }
  }, [selectedId, pollInterval, pollingSuspended, refreshKey])

  const selectedSummary = matches.find((match) => String(match.id) === selectedId)
  const currentInnings = analytics?.innings[analytics.innings.length - 1]
  const activeBatters = analytics?.current_batters ?? []
  const intelligence = analytics?.intelligence
  const chartProgression = useMemo(() => {
    const innings = currentInnings ? String(currentInnings.innings) : null
    return (analytics?.progression ?? [])
      .filter((point) => innings === null || point.innings === innings)
      .slice(-80)
  }, [analytics, currentInnings])
  const chartMomentum = useMemo(() => {
    const innings = currentInnings ? String(currentInnings.innings) : null
    return (analytics?.momentum ?? []).filter((point) => innings === null || point.innings === innings).slice(-12)
  }, [analytics, currentInnings])
  const recentDeliveries = analytics?.deliveries.slice(-12).reverse() ?? []
  const selectMatch = (id: string) => {
    setAnalytics(null)
    setActiveTab('overview')
    setLoadingDetail(true)
    setDetailError(null)
    setSelectedId(id)
    navigate(`/match/${encodeURIComponent(id)}?tab=overview`)
  }
  const selectTab = (tab: 'overview' | 'scorecard' | 'intelligence') => {
    setActiveTab(tab)
    if (selectedId) {
      navigate(`/match/${encodeURIComponent(selectedId)}?tab=${tab}`, { replace: true })
    }
  }
  const liveAnalysisProps: AnalysisProps | null = analytics ? {
    teams: [analytics.localteam, analytics.visitorteam],
    innings: analytics.innings,
    scorecards: analytics.scorecards,
    deliveries: analytics.deliveries,
    matchFormat: analytics.match_format,
    isFinished: analytics.is_finished,
    fixtureId: analytics.id,
    totalDeliveries: analytics.is_finished ? analytics.deliveries.length : analytics.deliveries.length + 1,
    narrativeSource: 'live',
  } : null
  const goToPlayer = (player: PlayerSelection, props = liveAnalysisProps) => {
    if (!props?.fixtureId) return
    const fixture = String(props.fixtureId)
    const returnTo = `/match/${encodeURIComponent(fixture)}?tab=intelligence`
    const routePlayerId = playerRouteId(player)
    const state = { player, analysisProps: props, returnTo }
    try {
      sessionStorage.setItem(`matchsense:match-scroll:${fixture}`, String(window.scrollY))
    } catch (error) {
      console.warn('Match scroll position could not be saved.', error)
    }
    savePlayerAnalysisCache({
      ...state,
      fixtureId: fixture,
      role: player.role,
      routePlayerId,
      source: 'live',
    })
    navigate(playerAnalysisPath(props.fixtureId, player, 'live'), { state })
  }
  const refresh = () => {
    if (pollingSuspended) setPollingSuspended(false)
    else void refreshMatches()
    setRefreshKey((current) => current + 1)
  }
  const statusText = detailError
    ? 'DATA UNAVAILABLE'
    : analytics?.deliveries.length
      ? analytics.is_finished ? 'MATCH COMPLETE · PROVIDER DATA' : 'LIVE DATA · PARTIAL'
      : matchesError ? 'CONNECTION ISSUE' : 'LIVE DATA'
  const shownScore = currentInnings ?? selectedSummary?.score ?? EMPTY_SCORE
  const battingTeam = currentInnings?.team_id != null
    ? [analytics?.localteam, analytics?.visitorteam].find((team) => team?.id != null && String(team.id) === String(currentInnings.team_id)) ?? null
    : null
  const bowlingTeam = battingTeam
    ? [analytics?.localteam, analytics?.visitorteam].find((team) => team?.id != null && String(team.id) !== String(battingTeam.id)) ?? null
    : null

  return (
    <div className="app-shell">
      <header className="topbar">
        <a className="brand" href="#" aria-label="MatchSense AI home">
          <span className="brand-mark"><Activity size={19} strokeWidth={2.4} /></span>
          <span>MatchSense</span><span className="brand-ai">AI</span>
        </a>
        <div className="topbar-right">
          <div className={`connection ${matchesError ? 'connection-error' : ''}`}>
            <span className="connection-dot" />{statusText}
          </div>
          <span className="updated"><Clock3 size={13} /> Updated {formatUpdated(lastUpdated)}</span>
          <button className="icon-button" onClick={refresh} aria-label="Refresh match data" title="Refresh now">
            <RefreshCw size={15} />
          </button>
        </div>
      </header>

      <main>
        <section className="page-heading">
          <div>
            <div className="eyebrow"><Radio size={13} /> MATCH CENTRE <span className="eyebrow-divider">/</span> CRICKET</div>
            <h1>Live match <span>intelligence.</span></h1>
            <p>Every delivery, every shift in momentum. In context.</p>
          </div>
          <div className="live-page-actions">
            <button className="replay-launch" onClick={() => navigate('/replay')}><Activity size={14} /> Historical replay</button>
            <div className="poll-indicator"><span className="pulse-ring" /> Auto-refresh <b>{pollInterval}s</b></div>
          </div>
        </section>

        <div className="dashboard-layout">
          <aside className="match-rail">
            <div className="rail-heading">
              <div><span className="section-kicker">ON THE FIELD</span><h2>Live matches</h2></div>
              <span className="match-count">{matches.length.toString().padStart(2, '0')}</span>
            </div>
            {loadingMatches && matches.length === 0 ? (
              <div className="rail-message"><LoaderCircle className="spin" size={18} /> Finding live matches</div>
            ) : matchesError && matches.length === 0 ? (
              <div className="rail-message error-message"><AlertCircle size={17} /><span>{matchesError}</span></div>
            ) : matches.length === 0 ? (
              <div className="rail-message empty-message"><CircleHelp size={18} /><span>No live matches are available right now.</span></div>
            ) : (
              <div className="match-list">
                {matches.map((match) => {
                  const key = String(match.id)
                  const chosen = key === selectedId
                  const homeScore = match.localteam_score
                  const awayScore = match.visitorteam_score
                  return (
                    <button className={`match-item ${chosen ? 'selected' : ''}`} key={key} onClick={() => selectMatch(key)}>
                      <span className="match-item-status"><i /> {match.status || 'LIVE'}</span>
                      <span className="match-team">
                        <TeamBadge team={match.localteam} />
                        <span className="team-name">{match.localteam?.name ?? 'Home team'}</span>
                        <b>{display(homeScore?.runs)}<small>/{display(homeScore?.wickets)}</small></b>
                      </span>
                      <span className="match-team">
                        <TeamBadge team={match.visitorteam} />
                        <span className="team-name">{match.visitorteam?.name ?? 'Away team'}</span>
                        <b className="opponent-score">{display(awayScore?.runs)}<small>/{display(awayScore?.wickets)}</small></b>
                      </span>
                      <span className="match-item-footer">{match.note || `${display(match.score.overs, 'Overs —')} ov`}<ArrowUp size={12} /></span>
                    </button>
                  )
                })}
              </div>
            )}
            <div className="rail-footnote"><Shield size={13} /> Sportmonks live feed · MatchSense analysis</div>
          </aside>

          <section className="match-view">
            {detailError && (
              <div className="inline-alert"><AlertCircle size={16} /><span>{detailError}</span><button onClick={refresh}>Retry</button></div>
            )}
            {!selectedId && !loadingMatches && !matchesError && (
              <div className="state-panel empty-state">
                <span className="state-icon"><Target size={23} /></span>
                <h2>Waiting for the next match.</h2>
                <p>Live fixtures from Sportmonks will appear here as soon as play is underway.</p>
              </div>
            )}
            {loadingDetail && !analytics && (
              <div className="state-panel loading-state"><LoaderCircle className="spin" size={24} /><span>Loading match analytics</span></div>
            )}
            {selectedId && !loadingDetail && !analytics && !detailError && (
              <div className="state-panel empty-state">
                <span className="state-icon"><BarChart3 size={23} /></span>
                <h2>Match data is not available yet.</h2>
                <p>We’ll keep checking for the latest score and match details.</p>
              </div>
            )}
            {analytics && (
              <>
                <div className="match-banner">
                  <div className="match-banner-top">
                    <div className={`live-label ${analytics.is_finished ? 'finished-label' : ''}`}>
                      <i /> {analytics.is_finished ? 'FINAL SCORE' : 'LIVE MATCH'}
                    </div>
                    <span className="fixture-id">FIXTURE {String(analytics.id ?? '').padStart(5, '0')}</span>
                  </div>
                  <div className="scoreboard">
                    <div className="score-team">
                      <TeamBadge team={analytics.localteam} large />
                      <div><strong>{analytics.localteam?.name ?? 'Home team'}</strong><small>{analytics.localteam?.short_name ?? 'HOME'}</small></div>
                    </div>
                    <div className="main-score">
                      <div className="score-line">{display(shownScore.runs)}<span>/</span>{display(shownScore.wickets)}</div>
                      <div className="overs-line">{display(shownScore.overs, '0')} <span>OVERS</span></div>
                    </div>
                    <div className="score-team away">
                      <div><strong>{analytics.visitorteam?.name ?? 'Away team'}</strong><small>{analytics.visitorteam?.short_name ?? 'AWAY'}</small></div>
                      <TeamBadge team={analytics.visitorteam} large />
                    </div>
                  </div>
                  <div className="scoreboard-footer">
                    <span>{battingTeam ? `${battingTeam.name} batting` : analytics.note || analytics.status || 'Match scorecard'}</span>
                    <span className="score-rate"><Zap size={13} /> RUN RATE <b>{display(currentInnings?.run_rate ?? selectedSummary?.score.run_rate, '—')}</b></span>
                  </div>
                </div>

                <div className="stat-strip">
                  <div className="stat-cell"><span>INNINGS</span><strong>{currentInnings?.innings ?? '—'}</strong><small>{battingTeam?.name ?? 'Current innings'}</small></div>
                  <div className="stat-cell"><span>WICKETS</span><strong>{display(currentInnings?.wickets)}</strong><small>of 10</small></div>
                  <div className="stat-cell"><span>OVERS</span><strong>{display(currentInnings?.overs, '—')}</strong><small>completed</small></div>
                  <div className="stat-cell"><span>RUN RATE</span><strong>{display(currentInnings?.run_rate, '—')}</strong><small>runs per over</small></div>
                  <div className="stat-cell"><span>STATUS</span><strong className={analytics.is_finished ? 'status-finished' : 'status-live'}>{analytics.is_finished ? 'Final' : 'Live'}</strong><small>{analytics.note || analytics.status || 'In play'}</small></div>
                </div>

                <div className="live-tabs" role="tablist" aria-label="Match sections">
                  <button
                    className={activeTab === 'overview' ? 'active' : ''}
                    role="tab"
                    aria-selected={activeTab === 'overview'}
                    onClick={() => selectTab('overview')}
                  >
                    <Activity size={14} /> Match Overview
                  </button>
                  <button
                    className={activeTab === 'scorecard' ? 'active' : ''}
                    role="tab"
                    aria-selected={activeTab === 'scorecard'}
                    onClick={() => selectTab('scorecard')}
                  >
                    <BarChart3 size={14} /> Scorecard
                  </button>
                  <button className={activeTab === 'intelligence' ? 'active' : ''} role="tab" aria-selected={activeTab === 'intelligence'} onClick={() => selectTab('intelligence')}><Zap size={14} /> Match Intelligence</button>
                </div>

                {activeTab === 'scorecard' ? (
                  <div className="scorecard-list">
                    {analytics.validation.status === 'warning' && (
                      <section className="panel validation-panel" role="alert">
                        <strong>Scorecard reconciliation warnings</strong>
                        <ul>{analytics.validation.issues.map((issue) => <li key={`${issue.code}-${issue.message}`}>{issue.message}</li>)}</ul>
                      </section>
                    )}
                    <nav className="innings-nav" aria-label="Scorecard innings">
                      {analytics.scorecards.map((card) => (
                        <button key={`nav-${card.innings}`} onClick={() => document.getElementById(`scorecard-innings-${card.innings}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })}>
                          INNINGS {card.innings}
                        </button>
                      ))}
                    </nav>
                    {analytics.scorecards.length ? analytics.scorecards.map((card) => {
                      const team = [analytics.localteam, analytics.visitorteam]
                        .find((candidate) => candidate?.id != null && card.team_id != null && String(candidate.id) === String(card.team_id)) ?? null
                      const totalExtras = Object.values(card.extras).reduce<number>((total, value) => total + (value ?? 0), 0)
                      const partnershipRows = card.partnerships.length
                        ? card.partnerships
                        : card.partnership ? [card.partnership] : []
                      return (
                        <section className="panel scorecard-panel" id={`scorecard-innings-${card.innings}`} key={`${card.innings}-${card.team_id ?? 'team'}`}>
                          <div className="scorecard-title">
                            <div>
                              <span className="section-kicker">INNINGS {card.innings}</span>
                              <h2>{team?.name ?? 'Team —'}</h2>
                            </div>
                            <div className="scorecard-total">
                              <strong>{display(card.runs)}<small>/{display(card.wickets)}</small></strong>
                              <span>{display(card.overs)} ov · RR {display(card.run_rate)}</span>
                            </div>
                          </div>
                          <div className="scorecard-table-wrap">
                            <table className="scorecard-table">
                              <thead><tr><th>BATTER</th><th>DISMISSAL</th><th>R</th><th>B</th><th>4s</th><th>6s</th><th>SR</th></tr></thead>
                              <tbody>
                                {card.batting.map((batter) => (
                                  <tr key={batter.player_id ?? batter.name}>
                                    <td><button className="player-link" type="button" onClick={() => {
                                      goToPlayer({ role: 'batter', playerId: batter.player_id, name: batter.name, innings: card.innings })
                                    }}>{batter.name}</button>{batter.is_current && batter.current_role && <i className="scorecard-current">{batter.current_role === 'striker' ? 'STRIKER' : 'NON-STRIKER'}</i>}</td>
                                    <td className="scorecard-dismissal">{display(batter.dismissal, 'not out')}</td>
                                    <td>{display(batter.runs)}</td><td>{display(batter.balls)}</td><td>{display(batter.fours)}</td><td>{display(batter.sixes)}</td><td>{display(batter.strike_rate)}</td>
                                  </tr>
                                ))}
                                {!card.batting.length && <tr><td colSpan={7} className="scorecard-empty">Ball-by-ball batting figures are not available.</td></tr>}
                              </tbody>
                            </table>
                          </div>
                          <div className="scorecard-table-wrap">
                            <table className="scorecard-table bowling-scorecard">
                              <thead><tr><th>BOWLER</th><th>O</th><th>M</th><th>R</th><th>W</th><th>ECON</th><th>WD</th><th>NB</th></tr></thead>
                              <tbody>
                                {card.bowling.map((bowler) => (
                                  <tr key={bowler.player_id ?? bowler.name}>
                                    <td><button className="player-link" type="button" onClick={() => {
                                      goToPlayer({ role: 'bowler', playerId: bowler.player_id, name: bowler.name, innings: card.innings })
                                    }}>{bowler.name}</button></td><td>{display(bowler.overs)}</td><td>{display(bowler.maidens)}</td><td>{display(bowler.runs)}</td><td>{display(bowler.wickets)}</td><td>{display(bowler.economy)}</td><td>{display(bowler.wides)}</td><td>{display(bowler.no_balls)}</td>
                                  </tr>
                                ))}
                                {!card.bowling.length && <tr><td colSpan={8} className="scorecard-empty">Ball-by-ball bowling figures are not available.</td></tr>}
                              </tbody>
                            </table>
                          </div>
                          <div className="scorecard-summary-grid">
                            <section className="scorecard-summary-card">
                              <div className="scorecard-subsection-heading"><span className="section-kicker">EXTRAS</span><b>{totalExtras}</b></div>
                              <div className="extras-stat-grid">
                                <span>Wides <b>{display(card.extras.wides)}</b></span>
                                <span>No-balls <b>{display(card.extras.no_balls)}</b></span>
                                <span>Byes <b>{display(card.extras.byes)}</b></span>
                                <span>Leg-byes <b>{display(card.extras.leg_byes)}</b></span>
                                <span>Penalty <b>{display(card.extras.penalty)}</b></span>
                              </div>
                            </section>
                            <section className="scorecard-summary-card">
                              <div className="scorecard-subsection-heading"><span className="section-kicker">POWERPLAY</span><b>{card.powerplay?.overs ? `Overs ${card.powerplay.overs}` : card.powerplay?.label ?? 'Unavailable'}</b></div>
                              {card.powerplay
                                ? <div className="powerplay-stat-grid">
                                  <span>Score <b>{card.powerplay.runs}/{card.powerplay.wickets}</b></span>
                                  <span>Run rate <b>{card.powerplay.run_rate == null ? '—' : card.powerplay.run_rate.toFixed(2)}</b></span>
                                  <span>Legal balls <b>{card.powerplay.legal_balls}</b></span>
                                  <small>{card.powerplay.complete ? 'Phase complete' : 'Phase in progress'}</small>
                                </div>
                                : <p className="scorecard-empty-state">Powerplay figures are unavailable for this innings and provider response.</p>}
                            </section>
                          </div>
                          <section className="scorecard-subsection">
                            <div className="scorecard-subsection-heading"><div><span className="section-kicker">WICKET TIMELINE</span><h3>Fall of wickets</h3></div><small>{card.fall_of_wickets.length} recorded</small></div>
                            {card.fall_of_wickets.length
                              ? <div className="scorecard-table-wrap fow-table-wrap"><table className="scorecard-table fow-table">
                                <thead><tr><th>WICKET</th><th>BATTER DISMISSED</th><th>TEAM SCORE</th><th>OVER</th><th>DISMISSAL</th></tr></thead>
                                <tbody>{card.fall_of_wickets.map((wicket) => (
                                  <tr key={`${card.innings}-${wicket.wicket}-${wicket.batter_id ?? wicket.batter ?? ''}`}>
                                    <td>{wicket.wicket}</td><td>{wicket.batter ?? 'Unavailable'}</td>
                                    <td>{wicket.score}/{wicket.wicket}</td><td>{wicket.over ?? 'Unavailable'}</td>
                                    <td className="scorecard-dismissal">{wicket.dismissal ?? 'Dismissal details unavailable'}</td>
                                  </tr>
                                ))}</tbody>
                              </table></div>
                              : <p className="scorecard-empty-state">No fall-of-wicket events are available for this innings.</p>}
                          </section>
                          <section className="scorecard-subsection">
                            <div className="scorecard-subsection-heading"><div><span className="section-kicker">BATTER PAIRS</span><h3>Partnerships</h3></div><small>{partnershipRows.length} recorded</small></div>
                            {partnershipRows.length
                              ? <div className="partnership-list">{partnershipRows.map((partnership) => (
                                <details className={`partnership-card${partnership.current ? ' current-partnership' : ''}`} key={`${card.innings}-partnership-${partnership.number}`} open={partnership.current}>
                                  <summary>
                                    <span><b>Partnership {partnership.number}</b><small>{partnership.batters.join(' & ') || 'Batter details unavailable'}</small></span>
                                    <strong>{partnership.runs}<small> runs · {partnership.balls} balls</small></strong>
                                    <i>{partnership.current ? 'CURRENT' : partnership.completed ? 'COMPLETED' : 'IN PROGRESS'}</i>
                                  </summary>
                                  <div className="partnership-detail">
                                    <span>Wicket associated: <b>{partnership.wicket == null ? 'Not ended by a wicket' : `Wicket ${partnership.wicket}`}</b></span>
                                    <div className="partnership-contributions">{partnership.contributions.map((batter) => (
                                      <span key={batter.player_id ?? batter.name}>{batter.name}<b>{batter.runs} runs · {batter.balls} balls</b></span>
                                    ))}</div>
                                  </div>
                                </details>
                              ))}</div>
                              : <p className="scorecard-empty-state">Partnership details are unavailable in this provider response.</p>}
                            <div className="did-not-bat"><span className="section-kicker">DID NOT BAT</span>
                              {card.did_not_bat.length
                                ? <p>{card.did_not_bat.map((player) => player.name).join(', ')}</p>
                                : <p className="scorecard-empty-state">No yet-to-bat players identified.</p>}
                            </div>
                          </section>
                        </section>
                      )
                    }) : <section className="panel scorecard-empty-panel">Innings scorecards are not available from the current provider response.</section>}
                  </div>
                ) : activeTab === 'overview' ? (
                  <>
                <div className="analysis-grid">
                  <section className="panel players-panel">
                    <div className="panel-heading"><div><span className="section-kicker">AT THE CREASE</span><h2>Batting now</h2></div><Crosshair size={16} /></div>
                    {activeBatters.length ? (
                      <div className="table-scroll"><table><thead><tr><th>BATTER</th><th>R</th><th>B</th><th>4s</th><th>6s</th><th>SR</th></tr></thead>
                        <tbody>{activeBatters.map((batter) => <tr key={batter.player_id ?? batter.name}>
                          <td className="player-name"><button className="player-link" type="button" onClick={() => currentInnings && (
                            goToPlayer({ role: 'batter', playerId: batter.player_id, name: batter.name, innings: String(currentInnings.innings) })
                          )}>{batter.name}</button>{batter.current_role && <small className="current-player-role">{batter.current_role === 'striker' ? 'STRIKER' : 'NON-STRIKER'}</small>}</td><td>{display(batter.runs)}</td><td>{display(batter.balls)}</td><td>{display(batter.fours)}</td><td>{display(batter.sixes)}</td><td>{display(batter.strike_rate)}</td>
                        </tr>)}</tbody></table></div>
                    ) : <div className="panel-empty">Batter statistics are not available.</div>}
                    <div className="bowler-divider" />
                    <div className="panel-heading bowler-heading"><div><span className="section-kicker">WITH THE BALL</span><h2>Current bowler</h2></div><Target size={16} /></div>
                    {analytics.current_bowler ? (
                      <div className="bowler-row">
                        <div className="bowler-name"><span className="bowler-avatar">{teamInitials({ id: null, short_name: null, name: analytics.current_bowler.name })}</span><button className="player-link" type="button" onClick={() => currentInnings && (
                          goToPlayer({ role: 'bowler', playerId: analytics.current_bowler?.player_id ?? null, name: analytics.current_bowler?.name ?? '', innings: String(currentInnings.innings) })
                        )}>{analytics.current_bowler.name}</button></div>
                        <div><small>O</small><b>{display(analytics.current_bowler.overs)}</b></div>
                        <div><small>R</small><b>{display(analytics.current_bowler.runs)}</b></div>
                        <div><small>W</small><b>{display(analytics.current_bowler.wickets)}</b></div>
                        <div><small>ECON</small><b>{display(analytics.current_bowler.economy)}</b></div>
                      </div>
                    ) : <div className="panel-empty">Bowling figures are not available.</div>}
                  </section>

                  <section className="panel deliveries-panel">
                    <div className="panel-heading"><div><span className="section-kicker">BALL BY BALL</span><h2>Recent deliveries</h2></div><span className="delivery-count">{recentDeliveries.length} BALLS</span></div>
                    {recentDeliveries.length ? <div className="delivery-list">
                      {recentDeliveries.map((ball) => {
                        const outcome = ball.is_wicket ? 'W' : ball.six ? '6' : ball.four ? '4' : String(ball.runs_total)
                        const kind = ball.is_wicket ? 'wicket' : ball.six ? 'boundary' : ball.four ? 'four' : ball.runs_total === 0 ? 'dot' : ''
                        return <div className="delivery-row" key={ball.source_id}>
                          <span className="delivery-over">{ball.over_ball ?? '—'}</span>
                          <span className={`delivery-outcome ${kind}`}>{outcome}</span>
                          <span className="delivery-details">{ball.is_wicket ? ball.dismissal || 'Wicket' : `${ball.batter ?? 'Batter'}${ball.bowler ? ` · ${ball.bowler}` : ''}`}</span>
                          <span className="delivery-score">{ball.innings_runs}</span>
                        </div>
                      })}
                    </div> : <div className="panel-empty">Ball-by-ball data is not available.</div>}
                    <div className="deliveries-foot"><ArrowDown size={12} /> MOST RECENT FIRST</div>
                  </section>
                </div>

                <div className="charts-grid">
                  <section className="panel chart-panel">
                    <div className="panel-heading"><div><span className="section-kicker">INNINGS {currentInnings?.innings ?? ''}</span><h2>Run progression</h2></div><span className="chart-legend"><i /> TOTAL RUNS</span></div>
                    {chartProgression.length ? <div className="chart-area">
                      <ResponsiveContainer width="100%" height="100%">
                        <AreaChart data={chartProgression} margin={{ top: 10, right: 8, left: -18, bottom: 0 }}>
                          <defs><linearGradient id="runFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#6290ff" stopOpacity={0.28} /><stop offset="100%" stopColor="#6290ff" stopOpacity={0} /></linearGradient></defs>
                          <CartesianGrid stroke="#ffffff0b" vertical={false} />
                          <XAxis dataKey="over_ball" tick={{ fill: '#71809c', fontSize: 10 }} axisLine={false} tickLine={false} minTickGap={30} />
                          <YAxis tick={{ fill: '#71809c', fontSize: 10 }} axisLine={false} tickLine={false} />
                          <Tooltip contentStyle={{ background: '#111a2b', border: '1px solid #283653', borderRadius: 8, color: '#edf3ff', fontSize: 12 }} labelStyle={{ color: '#91a4c7' }} />
                          <Area type="monotone" dataKey="runs" stroke="#78a1ff" strokeWidth={2} fill="url(#runFill)" />
                        </AreaChart>
                      </ResponsiveContainer>
                    </div> : <div className="chart-empty">Run progression will appear when delivery data is available.</div>}
                  </section>
                  <section className="panel chart-panel momentum-panel">
                    <div className="panel-heading"><div><span className="section-kicker">SCORING PATTERN</span><h2>Scoring momentum</h2></div><Zap size={16} /></div>
                    {chartMomentum.length ? <div className="chart-area">
                      <ResponsiveContainer width="100%" height="100%">
                        <BarChart data={chartMomentum} margin={{ top: 10, right: 8, left: -18, bottom: 0 }}>
                          <CartesianGrid stroke="#ffffff0b" vertical={false} />
                          <XAxis dataKey="over" tick={{ fill: '#71809c', fontSize: 10 }} axisLine={false} tickLine={false} />
                          <YAxis tick={{ fill: '#71809c', fontSize: 10 }} axisLine={false} tickLine={false} />
                          <Tooltip contentStyle={{ background: '#111a2b', border: '1px solid #283653', borderRadius: 8, color: '#edf3ff', fontSize: 12 }} labelStyle={{ color: '#91a4c7' }} />
                          <Bar dataKey="runs" name="Runs this over" fill="#507be1" radius={[4, 4, 0, 0]} maxBarSize={24} />
                        </BarChart>
                      </ResponsiveContainer>
                    </div> : <div className="chart-empty">Over-by-over scoring will appear as play progresses.</div>}
                  </section>
                </div>

                {analytics.is_finished && (
                  <div className="finished-banner"><Trophy size={17} /><span>This match has finished.</span><strong>{analytics.note || 'Final scorecard'}</strong></div>
                )}
                  </>
                ) : (
                  <div className="live-feature-view">
                    {activeTab === 'intelligence' && intelligence && (
                      <>
                        <div className="feature-heading">
                          <div><span className="section-kicker">LIVE MATCH INTELLIGENCE · MEASURED FROM CURRENT DELIVERIES</span><h2>{intelligence.situation.summary}</h2></div>
                          <span className="pressure-chip">{intelligence.situation.pressure} pressure</span>
                        </div>
                        {liveAnalysisProps && <FullMatchAnalysis
                          {...liveAnalysisProps}
                          onPlayerSelect={goToPlayer}
                        />}
                        {analytics.validation.status === 'warning' && (
                          <section className="panel validation-panel" role="alert">
                            <strong>Data consistency warnings</strong>
                            <ul>{analytics.validation.issues.map((issue) => <li key={`${issue.code}-${issue.message}`}>{issue.message}</li>)}</ul>
                          </section>
                        )}
                        {analytics.validation.target != null && (
                          <section className="panel chase-validation">
                            <span>{battingTeam?.name ?? 'Batting team'} target <b>{analytics.validation.target}</b></span>
                            <span>Required <b>{display(analytics.validation.required_runs)} runs</b> from <b>{display(analytics.validation.required_balls)} legal balls</b></span>
                            <span>Current RR <b>{display(analytics.validation.current_run_rate)}</b> · Required RR <b>{display(analytics.validation.required_run_rate)}</b></span>
                          </section>
                        )}
                        <div className="metric-grid">
                          <div className="panel metric-card"><span>DOT BALLS</span><strong>{display(intelligence.situation.measured_metrics.dot_ball_percentage, '—')}<small>%</small></strong><p>{intelligence.situation.measured_metrics.dot_balls} of {intelligence.situation.measured_metrics.legal_balls} legal balls</p></div>
                          <div className="panel metric-card"><span>BOUNDARY RATE</span><strong>{display(intelligence.situation.measured_metrics.boundary_percentage, '—')}<small>%</small></strong><p>{intelligence.situation.measured_metrics.boundary_deliveries} boundaries from {intelligence.situation.measured_metrics.balls_faced} balls faced</p></div>
                          <div className="panel metric-card"><span>WICKET FREQUENCY</span><strong>{display(intelligence.situation.measured_metrics.balls_per_wicket, '—')}<small>{intelligence.situation.measured_metrics.balls_per_wicket == null ? '' : ' balls/wkt'}</small></strong><p>{intelligence.situation.measured_metrics.wickets} wickets in this innings</p></div>
                          <div className="panel metric-card"><span>MATCH PHASE</span><strong className="phase-value">{display(intelligence.situation.match_phase)}</strong><p>{display(currentInnings?.overs)} overs recorded</p></div>
                        </div>
                        <div className="feature-grid">
                          <section className="panel feature-panel">
                            <div className="panel-heading"><div><span className="section-kicker">SITUATION & PRESSURE</span><h2>Why this matters</h2></div><Activity size={16} /></div>
                            <ul className="evidence-list">{intelligence.situation.factors.map((item, index) => <li key={`factor-${index}`}>{item}</li>)}</ul>
                            <div className="insight-columns">
                              <div><span className="section-kicker">BATTING ADVANTAGES</span>{intelligence.situation.strengths.map((item, index) => <p key={`strength-${index}`}>{item}</p>)}</div>
                              <div><span className="section-kicker">PRESSURE FACTORS</span>{intelligence.situation.weaknesses.map((item, index) => <p key={`weakness-${index}`}>{item}</p>)}</div>
                            </div>
                          </section>
                          <section className="panel feature-panel">
                            <div className="panel-heading"><div><span className="section-kicker">RECENT OVER</span><h2>Events and scoring shift</h2></div><Zap size={16} /></div>
                            {intelligence.situation.recent_over_events.length
                              ? <ul className="evidence-list">{intelligence.situation.recent_over_events.map((item, index) => <li key={`event-${index}`}>{item}</li>)}</ul>
                              : <p className="feature-muted">No current-over delivery events are available.</p>}
                            {intelligence.situation.momentum_shifts.length
                              ? <div className="momentum-note"><b>Momentum change</b>{intelligence.situation.momentum_shifts.map((item, index) => <p key={`shift-${index}`}>{item}</p>)}</div>
                              : <p className="feature-muted">No over-to-over scoring change of four or more runs is measured yet.</p>}
                          </section>
                          <section className="panel feature-panel">
                            <div className="panel-heading"><div><span className="section-kicker">BOWLING EFFECTIVENESS</span><h2>Used options · current innings</h2></div><Shield size={16} /></div>
                            {intelligence.situation.available_bowlers.length
                              ? <div className="table-scroll"><table><thead><tr><th>BOWLER</th><th>O</th><th>R</th><th>W</th><th>ECON</th></tr></thead><tbody>
                                {intelligence.situation.available_bowlers.map((bowler) => <tr key={bowler.name}><td className="player-name">{bowler.name}{bowler.name === analytics.current_bowler?.name && <small className="current-player-role">CURRENT</small>}</td><td>{display(bowler.overs)}</td><td>{bowler.runs}</td><td>{bowler.wickets}</td><td>{display(bowler.economy)}</td></tr>)}
                              </tbody></table></div>
                              : <p className="feature-muted">Bowling figures are unavailable until deliveries are recorded.</p>}
                          </section>
                          <section className="panel feature-panel">
                            <div className="panel-heading"><div><span className="section-kicker">WIN PROBABILITY</span><h2>Deterministic estimate</h2></div><Target size={16} /></div>
                            {intelligence.win_probability.available
                              ? <>
                                <div className="probability-row"><span>{battingTeam?.name ?? 'Batting team'}</span><strong>{display(intelligence.win_probability.batting_team_probability)}%</strong><span>{bowlingTeam?.name ?? 'Bowling team'}</span><strong>{display(intelligence.win_probability.bowling_team_probability)}%</strong></div>
                                <p className="feature-muted">{intelligence.win_probability.label}. This estimate is not an official prediction.</p>
                                <ul className="evidence-list">{intelligence.win_probability.factors.map((factor) => <li key={factor.name}>{factor.name}: {display(factor.value)}</li>)}</ul>
                              </>
                              : <p className="feature-muted">{intelligence.win_probability.reason ?? 'A win probability cannot be estimated from the available innings.'}</p>}
                          </section>
                        </div>
                      </>
                    )}

                  </div>
                )}
              </>
            )}
          </section>
        </div>
      </main>
      <footer className="footer"><span><Activity size={13} /> MATCHSENSE AI</span><span>UNDERSTAND EVERY MOMENT. EXPLORE EVERY POSSIBILITY.</span><span className="footer-live"><i /> DATA-DRIVEN, ALWAYS</span></footer>
    </div>
  )
}

function App() {
  const navigate = useNavigate()
  return (
    <Routes>
      <Route path="/" element={<MatchCentre />} />
      <Route path="/match/:fixtureId" element={<MatchCentre />} />
      <Route path="/match/:fixtureId/player/batter/:playerId" element={<PlayerAnalysisRoute forcedRole="batter" />} />
      <Route path="/match/:fixtureId/player/bowler/:playerId" element={<PlayerAnalysisRoute forcedRole="bowler" />} />
      <Route path="/replay" element={<ReplayMode onBack={() => navigate('/')} />} />
      <Route path="/replay/:fixtureId" element={<ReplayMode onBack={() => navigate('/replay')} />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}

export default App
