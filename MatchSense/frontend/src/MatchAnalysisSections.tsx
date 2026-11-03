import { useEffect, useMemo, useRef, useState } from 'react'

export type Team = { id: number | string | null; name: string; short_name: string | null; image_path?: string | null }
type Extras = { wides: number | null; no_balls: number | null; byes: number | null; leg_byes: number | null; penalty?: number | null }
export type Delivery = {
  source_id: number | string
  over_ball: string | null
  innings: string
  team_id: number | string | null
  batter_id: number | string | null
  non_striker_id?: number | string | null
  bowler_id: number | string | null
  batter: string | null
  non_striker?: string | null
  bowler: string | null
  dismissed_batter_id: number | string | null
  dismissed_batter: string | null
  runs_batter: number | null
  runs_total: number
  runs_extras: number
  runs_bowler: number | null
  is_legal: number | boolean | null
  is_wide: boolean
  is_noball: boolean
  extras: Extras | null
  four: boolean
  six: boolean
  is_wicket: boolean
  bowler_wicket: boolean
  is_retired_hurt?: boolean
  dismissal: string | null
  fielder_id?: number | string | null
  fielder?: string | null
  runout_by_id?: number | string | null
  runout_by?: string | null
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
}
type Partnership = {
  number: number
  wicket: number | null
  completed: boolean
  current: boolean
  batters: string[]
  runs: number
  balls: number
  contributions: { player_id: number | string | null; name: string; runs: number; balls: number }[]
}
export type Scorecard = {
  innings: string
  team_id: number | string | null
  runs: number | null
  wickets: number | null
  legal_balls: number | null
  overs: string | null
  run_rate: number | null
  extras: Extras
  batting: ScorecardBatter[]
  bowling: ScorecardBowler[]
  fall_of_wickets: { wicket: number; score: number; over: string | null; batter: string | null; dismissal: string | null }[]
  did_not_bat: { player_id: number | string | null; name: string }[]
  partnership: Partnership | null
  partnerships: Partnership[]
  powerplay: { label: string; overs?: string | null; boundary_legal_balls?: number | null; runs: number; wickets: number; legal_balls: number; run_rate?: number | null; complete: boolean } | null
}
export type Innings = { innings: number | string; team_id: number | string | null; runs: number | null; wickets?: number | null; overs?: string | null; run_rate?: number | null; legal_balls?: number | null }

export type AnalysisProps = {
  teams: (Team | null)[]
  innings: Innings[]
  scorecards: Scorecard[]
  deliveries: Delivery[]
  matchFormat: string | null
  isFinished: boolean
  fixtureId?: number | string | null
  totalDeliveries?: number
  narrativeSource?: 'live' | 'replay'
  onPlayerSelect?: (player: PlayerSelection) => void
}

export type PlayerSelection = {
  role: 'batter' | 'bowler'
  playerId: number | string | null
  name: string
  innings: string
}

type NarrativeScope = 'innings' | 'player'
type NarrativeResponse = {
  answer: string
  sections?: { title: string; paragraphs: string[] }[]
  evidence: { label: string; value: string }[]
  confidence: 'high' | 'medium' | 'low'
  limitations: string[]
  answer_mode: string
  ai_available?: boolean
  delivery_index: number
  is_finished: boolean
}

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? '/api'

function narrativeCheckpoint(props: AnalysisProps, innings: string) {
  if (props.isFinished) {
    const lastDelivery = props.deliveries.reduce(
      (last, ball, index) => String(ball.innings) === innings ? index + 1 : last,
      0,
    )
    return `${props.narrativeSource ?? 'live'}-final-${lastDelivery}`
  }
  let legalBalls = 0
  let latestMeaningfulDelivery = 0
  props.deliveries.forEach((ball, index) => {
    if (String(ball.innings) !== innings) return
    legalBalls += Number(isLegal(ball))
    if (ball.is_wicket || ball.four || ball.six || (isLegal(ball) && legalBalls > 0 && legalBalls % 6 === 0)) {
      latestMeaningfulDelivery = index + 1
    }
  })
  return `${props.narrativeSource ?? 'live'}-${latestMeaningfulDelivery}`
}

function buildNarrativeState(props: AnalysisProps) {
  const currentLabel = props.innings.length ? String(props.innings[props.innings.length - 1].innings) : null
  const battingCard = props.scorecards.find((card) => card.innings === currentLabel)
  return {
    id: props.fixtureId ?? null,
    status: props.isFinished ? 'Finished' : 'Live',
    is_finished: props.isFinished,
    is_live: props.narrativeSource !== 'replay',
    context_mode: props.narrativeSource === 'replay' ? 'HISTORICAL REPLAY CHECKPOINT' : 'LIVE MATCH SNAPSHOT',
    match_format: props.matchFormat,
    localteam: props.teams[0],
    visitorteam: props.teams[1],
    innings: props.innings,
    deliveries: props.deliveries,
    delivery_index: props.deliveries.length,
    total_deliveries: props.totalDeliveries ?? (props.isFinished ? props.deliveries.length : props.deliveries.length + 1),
    batting: battingCard?.batting ?? [],
    bowling: props.scorecards.flatMap((card) => card.bowling),
    current_batters: battingCard?.batting.filter((batter) => batter.is_current) ?? [],
    current_bowler: null,
    completed_scorecards: props.scorecards.filter((card) => props.isFinished || card.innings !== currentLabel),
    completed_innings: props.scorecards
      .filter((card) => props.isFinished || card.innings !== currentLabel)
      .map((card) => card.innings),
    narrative_partnerships: Object.fromEntries(
      props.scorecards.map((card) => [card.innings, card.partnerships]),
    ),
  }
}

function NarrativePanel({
  props,
  scope,
  innings,
  player,
}: {
  props: AnalysisProps
  scope: NarrativeScope
  innings: string
  player?: PlayerSelection
}) {
  const [response, setResponse] = useState<NarrativeResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [retry, setRetry] = useState(0)
  const state = useMemo(() => buildNarrativeState(props), [props])
  const stateRef = useRef(state)
  stateRef.current = state
  const checkpoint = narrativeCheckpoint(props, innings)

  useEffect(() => {
    const controller = new AbortController()
    const delay = props.narrativeSource === 'replay' ? 350 : 900
    setLoading(true)
    setError(null)
    const timer = window.setTimeout(() => {
      void fetch(`${API_BASE}/narrative`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        signal: controller.signal,
        body: JSON.stringify({
          state: stateRef.current,
          scope,
          innings,
          player_role: player?.role,
          player_id: player?.playerId,
          player_name: player?.name,
        }),
      }).then(async (result) => {
        if (!result.ok) {
          const body = await result.json().catch(() => null) as { detail?: string } | null
          throw new Error(body?.detail ?? `Unable to generate narrative (${result.status}).`)
        }
        return result.json() as Promise<NarrativeResponse>
      }).then((result) => {
        setResponse(result)
      }).catch((reason: unknown) => {
        if (reason instanceof DOMException && reason.name === 'AbortError') return
        setResponse(null)
        setError(reason instanceof Error ? reason.message : 'Unable to generate this narrative.')
      }).finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    }, delay)
    return () => {
      window.clearTimeout(timer)
      controller.abort()
    }
  }, [checkpoint, innings, player?.name, player?.playerId, player?.role, props.narrativeSource, retry, scope])

  const paragraphs = response?.answer.match(/[^.!?]+[.!?]+(?:\s|$)/g)?.map((part) => part.trim()) ?? []
  const answerParagraphs = paragraphs.length
    ? paragraphs.reduce<string[]>((groups, sentence, index) => {
      const group = Math.floor(index / 2)
      groups[group] = `${groups[group] ? `${groups[group]} ` : ''}${sentence}`
      return groups
    }, [])
    : response?.answer ? [response.answer] : []

  return (
    <section className="narrative-panel">
      <header>
        <div><span className="section-kicker">{scope === 'player' ? 'PLAYER STORY' : `INNINGS ${innings} STORY`}</span>
          <h3>{scope === 'player'
            ? player?.role === 'batter' ? 'How the Innings Unfolded' : 'How the Spell Unfolded'
            : `${props.innings.find((item) => String(item.innings) === innings)?.team_id
              ? teamFor(props.teams, props.innings.find((item) => String(item.innings) === innings)?.team_id ?? null)?.name ?? `Innings ${innings}`
              : `Innings ${innings}`} · batting and bowling story`}</h3>
        </div>
        {response && <span className="narrative-source">
          {response.ai_available === false
            ? 'AI narrative unavailable — showing data-derived analysis'
            : response.answer_mode}
        </span>}
      </header>
      {loading && !response && <p className="narrative-loading">Building a delivery-grounded story for checkpoint {state.delivery_index}…</p>}
      {error && <p className="narrative-error">{error}<button type="button" onClick={() => setRetry((value) => value + 1)}>Retry</button></p>}
      {response?.sections?.length
        ? response.sections.map((section) => (
          <div className="narrative-story-section" key={section.title}>
            <h4>{section.title}</h4>
            {section.paragraphs.map((paragraph, index) => (
              <p className="narrative-copy" key={`${index}-${paragraph}`}>{paragraph}</p>
            ))}
          </div>
        ))
        : answerParagraphs.map((paragraph, index) => <p className="narrative-copy" key={`${index}-${paragraph}`}>{paragraph}</p>)}
      {response?.evidence.length ? (
        <div className="narrative-evidence">
          {response.evidence.slice(0, 5).map((item) => <span key={`${item.label}-${item.value}`}><b>{item.label}</b>{item.value}</span>)}
        </div>
      ) : null}
      {response?.limitations.length ? (
        <ul className="narrative-limitations">{response.limitations.map((limitation, index) => <li key={`${index}-${limitation}`}>{limitation}</li>)}</ul>
      ) : null}
      {response?.is_finished === false && <small className="narrative-checkpoint">Provisional · based only on {response.delivery_index} processed deliveries</small>}
      {response && !loading && <button className="narrative-refresh" type="button" onClick={() => setRetry((value) => value + 1)}>Refresh narrative</button>}
    </section>
  )
}

type Over = {
  innings: string
  index: number
  runs: number
  wickets: number
  legalBalls: number
  extras: number
  singles: number
  twos: number
  threes: number
  dots: number
  fours: number
  sixes: number
  batters: string[]
  bowlers: string[]
  events: string[]
  deliveries: Delivery[]
  inProgress: boolean
  benchmarkRate: number | null
  scoreBefore: number
  scoreAfter: number
  wicketsBefore: number
  wicketsAfter: number
}

type PhaseFigures = { legalBalls: number; runs: number; wickets: number; dots: number; boundaryBalls: number; extras: number }
type BowlerFigures = {
  innings: string
  teamId: number | string | null
  playerId: number | string | null
  name: string
  overs: string
  maidens: number
  runs: number
  wickets: number
  economy: number | null
  legalBalls: number
  dots: number
  boundaryBalls: number
  extras: number
  wides: number
  noBalls: number
  spells: number
  phase: Record<string, PhaseFigures>
  hasBallData: boolean
  dotsByBall: number[]
  dismissals: string[]
}
type BowlingSpell = {
  innings: string
  bowlerId: number | string | null
  name: string
  overIndexes: number[]
  legalBalls: number
  runs: number
  wickets: number
  dots: number
  boundaryBalls: number
  extras: number
  dismissalTypes: string[]
  overFigures: { overIndex: number; runs: number; legalBalls: number; wickets: number; dots: number; boundaries: number; extras: number }[]
}

function isLegal(ball: Delivery) {
  return Boolean(ball.is_legal)
}

function numericOvers(overs: string | null) {
  if (!overs) return 0
  const [whole, balls = '0'] = overs.split('.')
  return (Number(whole) || 0) * 6 + (Number(balls[0]) || 0)
}

function overIndex(ball: Delivery, legalBallsBefore: number) {
  const match = /^(\d+)(?:\.(\d+))?/.exec(ball.over_ball ?? '')
  return match ? Number(match[1]) : Math.floor(legalBallsBefore / 6)
}

function teamFor(teams: (Team | null)[], id: number | string | null) {
  if (id == null) return null
  return teams.find((team) => team?.id != null && String(team.id) === String(id)) ?? null
}

function bowlingTeamFor(teams: (Team | null)[], battingTeamId: number | string | null) {
  const battingTeam = teamFor(teams, battingTeamId)
  return battingTeam
    ? teams.find((team) => team?.id != null && String(team.id) !== String(battingTeam.id)) ?? null
    : null
}

export function buildScorecardsFromDeliveries(deliveries: Delivery[], innings: Innings[]): Scorecard[] {
  return innings.map((inning) => {
    const label = String(inning.innings)
    const balls = deliveries.filter((ball) => String(ball.innings) === label)
    const batters = new Map<string, ScorecardBatter>()
    const bowlers = new Map<string, ScorecardBowler & { legal_balls: number }>()
    const bowlerOvers = new Map<string, { legalBalls: number; runs: number | null }>()
    const extras = { wides: 0, no_balls: 0, byes: 0, leg_byes: 0, penalty: 0 }
    const fallOfWickets: Scorecard['fall_of_wickets'] = []
    const partnerships: Partnership[] = []
    let runs = 0
    let wicketCount = 0
    let legalBefore = 0
    let activePartnership = {
      number: 1,
      wicket: null as number | null,
      completed: false,
      current: true,
      batters: [] as string[],
      runs: 0,
      balls: 0,
      contributions: [] as Partnership['contributions'],
    }

    for (const ball of balls) {
      const batterKey = String(ball.batter_id ?? ball.batter ?? 'unknown-batter')
      const batter = batters.get(batterKey) ?? {
        player_id: ball.batter_id,
        name: ball.batter ?? 'Unknown batter',
        dismissal: null,
        runs: 0,
        balls: 0,
        fours: 0,
        sixes: 0,
        strike_rate: null,
        is_current: false,
      }
      batter.runs = (batter.runs ?? 0) + (ball.runs_batter ?? 0)
      batter.balls = (batter.balls ?? 0) + Number(!ball.is_wide)
      batter.fours = (batter.fours ?? 0) + Number(ball.four)
      batter.sixes = (batter.sixes ?? 0) + Number(ball.six)
      if (ball.is_wicket && String(ball.dismissed_batter_id ?? ball.batter_id ?? ball.batter) === batterKey) {
        batter.dismissal = ball.dismissal
      }
      batters.set(batterKey, batter)

      const bowlerKey = String(ball.bowler_id ?? ball.bowler ?? 'unknown-bowler')
      const currentOver = overIndex(ball, legalBefore)
      const bowlerOverKey = `${bowlerKey}:${currentOver}`
      const bowlerOver = bowlerOvers.get(bowlerOverKey) ?? { legalBalls: 0, runs: 0 }
      bowlerOver.legalBalls += Number(isLegal(ball))
      if (ball.runs_bowler == null) bowlerOver.runs = null
      else if (bowlerOver.runs != null) bowlerOver.runs += ball.runs_bowler
      bowlerOvers.set(bowlerOverKey, bowlerOver)
      const bowler = bowlers.get(bowlerKey) ?? {
        player_id: ball.bowler_id,
        name: ball.bowler ?? 'Unknown bowler',
        overs: '0.0',
        maidens: 0,
        runs: 0,
        wickets: 0,
        economy: null,
        wides: 0,
        no_balls: 0,
        legal_balls: 0,
      }
      const chargedRuns = ball.runs_bowler
      if (chargedRuns == null) bowler.runs = null
      else if (bowler.runs != null) bowler.runs += chargedRuns
      bowler.legal_balls += Number(isLegal(ball))
      bowler.wickets = (bowler.wickets ?? 0) + Number(ball.bowler_wicket)
      bowler.wides = (bowler.wides ?? 0) + (ball.extras?.wides ?? 0)
      bowler.no_balls = (bowler.no_balls ?? 0) + (ball.extras?.no_balls ?? 0)
      bowlers.set(bowlerKey, bowler)

      extras.wides += ball.extras?.wides ?? 0
      extras.no_balls += ball.extras?.no_balls ?? 0
      extras.byes += ball.extras?.byes ?? 0
      extras.leg_byes += ball.extras?.leg_byes ?? 0
      extras.penalty += ball.extras?.penalty ?? 0

      const pairNames = [ball.batter, ball.non_striker].filter((name): name is string => Boolean(name))
      activePartnership.batters = [...new Set([...activePartnership.batters, ...pairNames])]
      activePartnership.runs += ball.runs_total
      activePartnership.balls += Number(!ball.is_wide)
      for (const [id, name] of [[ball.batter_id, ball.batter], [ball.non_striker_id, ball.non_striker]] as const) {
        if (id == null && !name) continue
        const key = String(id ?? name)
        const contribution = activePartnership.contributions.find((item) => String(item.player_id ?? item.name) === key)
        const isStriker = key === batterKey
        if (contribution) {
          if (isStriker) contribution.runs += ball.runs_batter ?? 0
          contribution.balls += Number(isStriker && !ball.is_wide)
        } else {
          activePartnership.contributions.push({
            player_id: id ?? null,
            name: name ?? 'Unknown batter',
            runs: isStriker ? ball.runs_batter ?? 0 : 0,
            balls: Number(isStriker && !ball.is_wide),
          })
        }
      }

      runs += ball.runs_total
      if (ball.is_wicket) {
        wicketCount += 1
        fallOfWickets.push({
          wicket: wicketCount,
          score: runs,
          over: ball.over_ball,
          batter: ball.dismissed_batter ?? ball.batter,
          dismissal: ball.dismissal,
        })
        activePartnership.wicket = wicketCount
        activePartnership.completed = true
        activePartnership.current = false
        partnerships.push(activePartnership)
        activePartnership = {
          number: wicketCount + 1,
          wicket: null,
          completed: false,
          current: true,
          batters: [],
          runs: 0,
          balls: 0,
          contributions: [],
        }
        legalBefore += Number(isLegal(ball))
      }
    }
    if (activePartnership.runs || activePartnership.balls) partnerships.push(activePartnership)
    const legalBalls = balls.filter(isLegal).length
    const bowlingRows = [...bowlers.entries()].map(([key, { legal_balls, ...bowler }]) => ({
      ...bowler,
      overs: `${Math.floor(legal_balls / 6)}.${legal_balls % 6}`,
      economy: bowler.runs == null || !legal_balls ? null : bowler.runs * 6 / legal_balls,
      maidens: [...bowlerOvers.entries()].filter(([overKey, over]) => (
        overKey.startsWith(`${key}:`) && over.legalBalls === 6 && over.runs === 0
      )).length,
    }))
    const battingRows = [...batters.values()].map((batter) => ({
      ...batter,
      strike_rate: batter.balls ? (batter.runs ?? 0) * 100 / batter.balls : null,
    }))
    return {
      innings: label,
      team_id: inning.team_id,
      runs,
      wickets: wicketCount,
      legal_balls: legalBalls,
      overs: `${Math.floor(legalBalls / 6)}.${legalBalls % 6}`,
      run_rate: legalBalls ? runs * 6 / legalBalls : null,
      extras,
      batting: battingRows,
      bowling: bowlingRows,
      fall_of_wickets: fallOfWickets,
      did_not_bat: [],
      partnership: partnerships.find((partnership) => partnership.current) ?? null,
      partnerships,
      powerplay: null,
    }
  })
}

function limits(format: string | null) {
  const normalized = (format ?? '').toLowerCase()
  if (/\bt20i?\b/.test(normalized)) return { overs: 20, powerplay: 6 }
  if (/\bt10\b/.test(normalized)) return { overs: 10, powerplay: 2 }
  if (normalized.includes('odi') || normalized.includes('list a')) return { overs: 50, powerplay: 10 }
  return null
}

function makeOvers(props: AnalysisProps): Over[] {
  const legalBeforeByInnings: Record<string, number> = {}
  const rows = new Map<string, Over>()
  for (const ball of props.deliveries) {
    const inning = String(ball.innings)
    const legalBefore = legalBeforeByInnings[inning] ?? 0
    const index = overIndex(ball, legalBefore)
    const key = `${inning}:${index}`
    let row = rows.get(key)
    if (!row) {
      row = {
        innings: inning,
        index,
        runs: 0,
        wickets: 0,
        legalBalls: 0,
        extras: 0,
        singles: 0,
        twos: 0,
        threes: 0,
        dots: 0,
        fours: 0,
        sixes: 0,
        batters: [],
        bowlers: [],
        events: [],
        deliveries: [],
        inProgress: false,
        benchmarkRate: null,
        scoreBefore: 0,
        scoreAfter: 0,
        wicketsBefore: 0,
        wicketsAfter: 0,
      }
      rows.set(key, row)
    }
    row.runs += ball.runs_total
    row.wickets += Number(ball.is_wicket)
    row.legalBalls += Number(isLegal(ball))
    row.extras += ball.runs_extras ?? 0
    row.singles += Number(ball.runs_batter === 1)
    row.twos += Number(ball.runs_batter === 2)
    row.threes += Number(ball.runs_batter === 3)
    row.dots += Number(isLegal(ball) && ball.runs_total === 0)
    row.fours += Number(ball.four)
    row.sixes += Number(ball.six)
    row.deliveries.push(ball)
    if (ball.batter && !row.batters.includes(ball.batter)) row.batters.push(ball.batter)
    if (ball.bowler && !row.bowlers.includes(ball.bowler)) row.bowlers.push(ball.bowler)
    if (ball.is_wicket) {
      row.events.push(`${ball.dismissed_batter ?? ball.batter ?? 'Batter'} out${ball.dismissal ? ` · ${ball.dismissal}` : ''}`)
    } else if (ball.six) {
      row.events.push(`Six · ${ball.batter ?? 'Batter'}`)
    } else if (ball.four) {
      row.events.push(`Four · ${ball.batter ?? 'Batter'}`)
    }
    if ((ball.runs_extras ?? 0) > 0) {
      const kinds = Object.entries(ball.extras ?? {})
        .filter(([, value]) => Number(value) > 0)
        .map(([kind, value]) => `${kind.replace('_', ' ')} ${value}`)
      row.events.push(`Extras ${ball.runs_extras}${kinds.length ? ` · ${kinds.join(', ')}` : ''}`)
    }
    legalBeforeByInnings[inning] = legalBefore + Number(isLegal(ball))
  }

  const currentInnings = props.innings[props.innings.length - 1]
  const currentLabel = currentInnings ? String(currentInnings.innings) : null
  if (!props.isFinished && currentLabel && (currentInnings?.wickets ?? 0) < 10) {
    const currentRows = [...rows.values()].filter((row) => row.innings === currentLabel)
    const latest = currentRows[currentRows.length - 1]
    if (latest && latest.legalBalls < 6) latest.inProgress = true
  }
  const previousByInnings = new Map<string, Over>()
  const totalsByInnings = new Map<string, { runs: number; legalBalls: number; wickets: number }>()
  return [...rows.values()]
    .sort((a, b) => Number(a.innings) - Number(b.innings) || a.index - b.index)
    .map((row) => {
      const previous = previousByInnings.get(row.innings)
      const current = props.innings.find((inning) => String(inning.innings) === row.innings)
      const battingTeamName = teamFor(props.teams, current?.team_id ?? null)?.name ?? 'Batting team'
      const before = totalsByInnings.get(row.innings) ?? { runs: 0, legalBalls: 0, wickets: 0 }
      row.scoreBefore = before.runs
      row.wicketsBefore = before.wickets
      row.benchmarkRate = before.legalBalls ? (before.runs * 6) / before.legalBalls : null
      const rate = row.legalBalls ? (row.runs * 6) / row.legalBalls : 0
      if (row.legalBalls) {
        row.events.unshift(
          row.benchmarkRate == null ? 'No earlier innings-rate baseline'
            : rate > row.benchmarkRate + 1.5 ? 'Expensive versus the pre-over innings rate'
              : rate < row.benchmarkRate - 1.5 ? 'Economical versus the pre-over innings rate'
                : 'Close to the pre-over innings rate',
        )
      }
      if (previous && (row.wickets > 0 || Math.abs(row.runs - previous.runs) >= 4)) {
        row.events.push(
          row.wickets > 0
            ? `Momentum shift: ${row.wickets} wicket${row.wickets === 1 ? '' : 's'} in the over`
            : `Momentum shift: ${row.runs > previous.runs ? 'up' : 'down'} ${Math.abs(row.runs - previous.runs)} runs versus the previous over`,
        )
      }
      if (row.dots >= 3 || row.wickets > 0) row.events.push('Bowler created pressure with dots and/or a wicket')
      if (previous && row.runs >= previous.runs + 4) row.events.push(`${battingTeamName} accelerated versus the previous over`)
      if (row.wickets > 0 && row.runs <= 5) row.events.push(`${battingTeamName} lost control: wicket(s) with five runs or fewer`)
      totalsByInnings.set(row.innings, {
        runs: before.runs + row.runs,
        legalBalls: before.legalBalls + row.legalBalls,
        wickets: before.wickets + row.wickets,
      })
      row.scoreAfter = before.runs + row.runs
      row.wicketsAfter = before.wickets + row.wickets
      previousByInnings.set(row.innings, row)
      return row
    })
}

function makeBowlers(props: AnalysisProps): BowlerFigures[] {
  const formatLimits = limits(props.matchFormat)
  const phaseOf = (index: number) => {
    if (!formatLimits) return null
    const over = index + 1
    if (over <= formatLimits.powerplay) return 'Powerplay'
    if (over > formatLimits.overs - 5) return 'Death'
    return 'Middle'
  }
  const grouped = new Map<string, { innings: string; bowlerId: number | string | null; name: string; balls: Delivery[]; overIds: Set<number> }>()
  const legalBeforeByInnings: Record<string, number> = {}
  for (const ball of props.deliveries) {
    const inning = String(ball.innings)
    const index = overIndex(ball, legalBeforeByInnings[inning] ?? 0)
    const key = `${inning}:${ball.bowler_id ?? ball.bowler ?? 'unknown'}`
    let row = grouped.get(key)
    if (!row) {
      row = { innings: inning, bowlerId: ball.bowler_id, name: ball.bowler ?? 'Unknown bowler', balls: [], overIds: new Set() }
      grouped.set(key, row)
    }
    row.balls.push(ball)
    row.overIds.add(index)
    legalBeforeByInnings[inning] = (legalBeforeByInnings[inning] ?? 0) + Number(isLegal(ball))
  }

  const result: BowlerFigures[] = []
  for (const card of props.scorecards) {
    const cardRows = card.bowling
    for (const score of cardRows) {
      const deliveryRow = [...grouped.values()].find((item) =>
        item.innings === String(card.innings) && (
          (score.player_id != null && item.bowlerId != null && String(score.player_id) === String(item.bowlerId))
          || score.name === item.name
        ),
      )
      const balls = deliveryRow?.balls ?? []
      const legalBalls = balls.reduce((total, ball) => total + Number(isLegal(ball)), 0)
      const runs = deliveryRow
        ? balls.reduce((total, ball) => total + (ball.runs_bowler ?? ball.runs_total), 0)
        : score.runs ?? 0
      const wickets = deliveryRow
        ? balls.reduce((total, ball) => total + Number(ball.bowler_wicket), 0)
        : score.wickets ?? 0
      const oversPlayed = deliveryRow
        ? [...deliveryRow.overIds].sort((a, b) => a - b)
        : []
      let spells = 0
      let previousOver: number | null = null
      for (const over of oversPlayed) {
        if (previousOver === null || over > previousOver + 1) spells += 1
        previousOver = over
      }
      const phase: Record<string, PhaseFigures> = {}
      const dismissals = balls
        .filter((ball) => ball.bowler_wicket)
        .map((ball) => `${ball.dismissed_batter ?? ball.batter ?? 'Batter'}: ${ball.dismissal ?? 'dismissal type unavailable'}`)
      for (const ball of balls) {
        const label = phaseOf(overIndex(ball, 0))
        if (!label) continue
        const row = phase[label] ?? { legalBalls: 0, runs: 0, wickets: 0, dots: 0, boundaryBalls: 0, extras: 0 }
        row.legalBalls += Number(isLegal(ball))
        row.runs += ball.runs_bowler ?? ball.runs_total
        row.wickets += Number(ball.bowler_wicket)
        row.dots += Number(isLegal(ball) && ball.runs_total === 0)
        row.boundaryBalls += Number(ball.four || ball.six)
        row.extras += ball.runs_extras ?? 0
        phase[label] = row
      }
      result.push({
        innings: String(card.innings),
        teamId: card.team_id,
        playerId: score.player_id,
        name: score.name,
        overs: deliveryRow ? `${Math.floor(legalBalls / 6)}.${legalBalls % 6}` : score.overs ?? '0.0',
        maidens: score.maidens ?? 0,
        runs,
        wickets,
        economy: deliveryRow ? (legalBalls ? (runs * 6) / legalBalls : null) : score.economy,
        legalBalls: deliveryRow ? legalBalls : numericOvers(score.overs),
        dots: deliveryRow ? balls.filter((ball) => isLegal(ball) && ball.runs_total === 0).length : 0,
        boundaryBalls: deliveryRow ? balls.filter((ball) => ball.four || ball.six).length : 0,
        extras: deliveryRow ? balls.reduce((total, ball) => total + (ball.runs_extras ?? 0), 0) : (score.wides ?? 0) + (score.no_balls ?? 0),
        wides: deliveryRow ? balls.reduce((total, ball) => total + (ball.extras?.wides ?? 0), 0) : score.wides ?? 0,
        noBalls: deliveryRow ? balls.reduce((total, ball) => total + (ball.extras?.no_balls ?? 0), 0) : score.no_balls ?? 0,
        spells,
        phase,
        hasBallData: Boolean(deliveryRow),
        dotsByBall: deliveryRow ? balls.map((ball) => Number(isLegal(ball) && ball.runs_total === 0)) : [],
        dismissals,
      })
    }
  }
  return result
}

function makeSpells(props: AnalysisProps): BowlingSpell[] {
  const perOver = new Map<string, {
    innings: string
    bowlerId: number | string | null
    name: string
    overIndex: number
    legalBalls: number
    runs: number
    wickets: number
    dots: number
    boundaryBalls: number
    extras: number
    dismissalTypes: string[]
  }>()
  const legalBeforeByInnings: Record<string, number> = {}
  for (const ball of props.deliveries) {
    const innings = String(ball.innings)
    const index = overIndex(ball, legalBeforeByInnings[innings] ?? 0)
    const bowlerKey = String(ball.bowler_id ?? ball.bowler ?? 'unknown')
    const key = `${innings}:${bowlerKey}:${index}`
    const row = perOver.get(key) ?? {
      innings,
      bowlerId: ball.bowler_id,
      name: ball.bowler ?? 'Unknown bowler',
      overIndex: index,
      legalBalls: 0,
      runs: 0,
      wickets: 0,
      dots: 0,
      boundaryBalls: 0,
      extras: 0,
      dismissalTypes: [],
    }
    row.legalBalls += Number(isLegal(ball))
    row.runs += ball.runs_bowler ?? ball.runs_total
    row.wickets += Number(ball.bowler_wicket)
    row.dots += Number(isLegal(ball) && ball.runs_total === 0)
    row.boundaryBalls += Number(ball.four || ball.six)
    row.extras += ball.runs_extras ?? 0
    if (ball.bowler_wicket) row.dismissalTypes.push(ball.dismissal ?? 'dismissal type unavailable')
    perOver.set(key, row)
    legalBeforeByInnings[innings] = (legalBeforeByInnings[innings] ?? 0) + Number(isLegal(ball))
  }

  const byBowler = new Map<string, typeof perOver extends Map<string, infer Value> ? Value[] : never>()
  for (const over of perOver.values()) {
    const key = `${over.innings}:${over.bowlerId ?? over.name}`
    const rows = byBowler.get(key) ?? []
    rows.push(over)
    byBowler.set(key, rows)
  }
  const spells: BowlingSpell[] = []
  for (const rows of byBowler.values()) {
    rows.sort((a, b) => a.overIndex - b.overIndex)
    let active: BowlingSpell | null = null
    for (const over of rows) {
      if (!active || over.overIndex > active.overIndexes[active.overIndexes.length - 1] + 1) {
        if (active) spells.push(active)
        active = {
          innings: over.innings,
          bowlerId: over.bowlerId,
          name: over.name,
          overIndexes: [],
          legalBalls: 0,
          runs: 0,
          wickets: 0,
          dots: 0,
          boundaryBalls: 0,
          extras: 0,
          dismissalTypes: [],
          overFigures: [],
        }
      }
      active.overIndexes.push(over.overIndex)
      active.legalBalls += over.legalBalls
      active.runs += over.runs
      active.wickets += over.wickets
      active.dots += over.dots
      active.boundaryBalls += over.boundaryBalls
      active.extras += over.extras
      active.dismissalTypes.push(...over.dismissalTypes)
      active.overFigures.push({
        overIndex: over.overIndex,
        runs: over.runs,
        legalBalls: over.legalBalls,
        wickets: over.wickets,
        dots: over.dots,
        boundaries: over.boundaryBalls,
        extras: over.extras,
      })
    }
    if (active) spells.push(active)
  }
  return spells
}

function overInterpretation(row: Over, prior: Over | undefined) {
  const rate = row.legalBalls ? (row.runs * 6) / row.legalBalls : null
  const assessment = rate == null || row.benchmarkRate == null
    ? 'Rate comparison unavailable'
    : rate > row.benchmarkRate + 1.5 ? 'Expensive'
      : rate < row.benchmarkRate - 1.5 ? 'Economical'
        : 'Around the innings rate'
  const momentum = !prior
    ? 'No earlier over to compare'
    : row.wickets > 0 ? `${row.wickets} wicket${row.wickets === 1 ? '' : 's'}; wicket event changed the over`
      : Math.abs(row.runs - prior.runs) >= 4 ? `${row.runs > prior.runs ? 'Scoring rose' : 'Scoring fell'} by ${Math.abs(row.runs - prior.runs)} runs`
        : 'No measured scoring swing'
  const batting = prior && row.runs >= prior.runs + 4 ? 'Accelerated' : row.wickets > 0 && row.runs <= 5 ? 'Lost control' : 'No clear shift'
  const pressure = row.dots >= 3 || row.wickets > 0 ? 'Pressure created (dots/wicket)' : 'No strong pressure signal'
  return { assessment, momentum, batting, pressure, rate }
}

export function OverByOverAnalysis(props: AnalysisProps) {
  const [filter, setFilter] = useState('all')
  const overs = useMemo(() => makeOvers(props), [props.deliveries, props.innings, props.isFinished])
  const visible = filter === 'all' ? overs : overs.filter((row) => row.innings === filter)
  const inningsByLabel = new Map(props.innings.map((row) => [String(row.innings), row]))
  const previousByLabel = new Map<string, Over>()

  return (
    <section className="panel analysis-section">
      <div className="panel-heading">
        <div><span className="section-kicker">FULL MATCH · BOTH INNINGS</span><h2>Over-by-over analysis</h2></div>
        <label className="analysis-filter">INNINGS
          <select value={filter} onChange={(event) => setFilter(event.target.value)}>
            <option value="all">Entire match</option>
            {props.innings.map((inning) => {
              const team = teamFor(props.teams, inning.team_id)
              return <option key={String(inning.innings)} value={String(inning.innings)}>Innings {inning.innings} · {team?.name ?? 'Team unavailable'}</option>
            })}
          </select>
        </label>
      </div>
      {visible.length ? <div className="analysis-rows">
        {visible.map((row) => {
          const current = inningsByLabel.get(row.innings)
          const team = teamFor(props.teams, current?.team_id ?? null)
          const bowlingTeam = bowlingTeamFor(props.teams, current?.team_id ?? null)
          const previous = previousByLabel.get(row.innings)
          const insight = overInterpretation(row, previous)
          previousByLabel.set(row.innings, row)
          return (
            <details className="analysis-row" key={`${row.innings}-${row.index}`} open={row.inProgress}>
              <summary>
                <span className="analysis-over-title">Over {row.index + 1}<small>{team?.name ?? 'Team unavailable'}</small></span>
                {row.inProgress && <b className="in-progress-badge">PROVISIONAL · IN PROGRESS</b>}
                <span className="analysis-over-bowler">{bowlingTeam?.name ?? 'Bowling team unavailable'} · {row.bowlers.join(', ') || 'Bowler unavailable'}</span>
                <span className="analysis-over-score">{row.runs}/{row.wickets}<small>{row.legalBalls} legal</small></span>
              </summary>
              <div className="analysis-over-detail">
                <div className="analysis-stat-grid">
                  <span>Runs <b>{row.runs}</b></span><span>Wickets <b>{row.wickets}</b></span>
                  <span>Legal balls <b>{row.legalBalls}</b></span><span>Extras <b>{row.extras}</b></span>
                  <span>Dots <b>{row.dots}</b></span><span>Boundaries <b>{row.fours}×4 · {row.sixes}×6</b></span>
                  <span>1s / 2s / 3s off bat <b>{row.singles} / {row.twos} / {row.threes}</b></span>
                  <span>Over run rate <b>{insight.rate == null ? '—' : insight.rate.toFixed(2)}</b></span>
                  <span>Batters faced <b>{row.batters.join(', ') || '—'}</b></span>
                  <span>Score change <b>{row.scoreBefore}/{row.wicketsBefore} → {row.scoreAfter}/{row.wicketsAfter}</b></span>
                </div>
                <div className="analysis-interpretation">
                  <b>{insight.assessment}</b><span>{insight.momentum}</span>
                  <span>Batting: {insight.batting} · Bowling: {insight.pressure}. Statistical signals only; tactical cause is not available in delivery data.</span>
                </div>
                {row.events.length > 0 && <ul className="analysis-events">{row.events.map((event, index) => <li key={`${row.innings}-${row.index}-${index}`}>{event}</li>)}</ul>}
                {!row.events.length && <p className="analysis-muted">No boundary, dismissal, or extra event recorded in this over.</p>}
                <details className="delivery-sequence">
                  <summary>Delivery sequence · {row.deliveries.length} recorded deliveries</summary>
                  <ol>{row.deliveries.map((ball, index) => (
                    <li key={ball.source_id}>
                      <b>{ball.over_ball ?? index + 1}</b>
                      <span>{ball.batter ?? 'Batter unavailable'}</span>
                      <span>{ball.is_legal ? 'Legal' : ball.is_wide ? 'Wide' : ball.is_noball ? 'No-ball' : 'Extra delivery'}</span>
                      <span>{ball.runs_batter ?? 0} off bat + {ball.runs_extras} extras = {ball.runs_total}</span>
                      {ball.is_wicket && <strong>{ball.dismissed_batter ?? ball.batter ?? 'Batter'} out · {ball.dismissal ?? 'dismissal type unavailable'}</strong>}
                    </li>
                  ))}</ol>
                </details>
              </div>
            </details>
          )
        })}
      </div> : <p className="analysis-muted">No ball-by-ball over history is available in this fixture response.</p>}
    </section>
  )
}

export function BowlerAnalysis(props: AnalysisProps) {
  const [filter, setFilter] = useState('all')
  const bowlers = useMemo(() => makeBowlers(props), [props.deliveries, props.scorecards, props.matchFormat])
  const visible = filter === 'all' ? bowlers : bowlers.filter((row) => row.innings === filter)
  const spells = useMemo(() => makeSpells(props), [props.deliveries])
  const economyOrder = [...bowlers].filter((row) => row.economy != null && row.legalBalls >= 6).sort((a, b) => (a.economy ?? Infinity) - (b.economy ?? Infinity))
  const wicketOrder = [...bowlers].filter((row) => row.wickets > 0 && row.legalBalls >= 6).sort(
    (a, b) => (b.wickets / b.legalBalls) - (a.wickets / a.legalBalls) || b.wickets - a.wickets,
  )
  const expensiveOrder = spells.filter((spell) => spell.legalBalls >= 6).sort(
    (a, b) => (b.runs / b.legalBalls) - (a.runs / a.legalBalls),
  )
  const pressureOrder = spells.filter((spell) => spell.legalBalls >= 6).sort((a, b) => {
    const score = (item: BowlingSpell) => item.dots * 100 / item.legalBalls + (item.wickets * 100 / item.legalBalls) - item.boundaryBalls * 100 / item.legalBalls
    return score(b) - score(a)
  })
  const qualified = bowlers.filter((bowler) => bowler.hasBallData && bowler.legalBalls >= 6)
  const overallOrder = [...qualified].sort(
    (a, b) => b.wickets - a.wickets || (a.economy ?? Infinity) - (b.economy ?? Infinity),
  )
  const impactOrder = qualified.map((bowler) => {
    const inningsRate = props.innings.find((inning) => String(inning.innings) === bowler.innings)?.run_rate
    const runsSaved = inningsRate == null ? null : inningsRate * bowler.legalBalls / 6 - bowler.runs
    return {
      bowler,
      runsSaved,
      impact: runsSaved == null ? null : bowler.wickets * 10 + runsSaved,
    }
  }).filter((row): row is typeof row & { runsSaved: number; impact: number } => row.runsSaved != null && row.impact != null)
    .sort((a, b) => b.impact - a.impact)
  const features = [
    {
      title: 'Best overall performance',
      name: overallOrder[0]?.name,
      innings: overallOrder[0]?.innings,
      metric: overallOrder[0] ? `${overallOrder[0].wickets} wickets; economy ${overallOrder[0].economy?.toFixed(2)}. Ranked by wickets, then economy; minimum 6 legal balls.` : 'Needs at least 6 legal balls with delivery data',
    },
    {
      title: 'Greatest match impact',
      name: impactOrder[0]?.bowler.name,
      innings: impactOrder[0]?.bowler.innings,
      metric: impactOrder[0] ? `Impact ${impactOrder[0].impact.toFixed(1)} = 10 × wickets + ${impactOrder[0].runsSaved.toFixed(1)} runs saved versus innings rate.` : 'Needs an innings rate and at least 6 legal balls',
    },
    {
      title: 'Most economical',
      name: economyOrder[0]?.name,
      innings: economyOrder[0]?.innings,
      metric: economyOrder[0] ? `Econ ${economyOrder[0].economy?.toFixed(2)} · ${economyOrder[0].runs} runs` : 'Needs at least 6 legal balls',
    },
    {
      title: 'Most effective wicket-taking',
      name: wicketOrder[0]?.name,
      innings: wicketOrder[0]?.innings,
      metric: wicketOrder[0] ? `${wicketOrder[0].wickets} wickets · ${(wicketOrder[0].wickets * 6 / wicketOrder[0].legalBalls).toFixed(2)} wickets/over` : 'No wicket-taker established with 6+ legal balls',
    },
    {
      title: 'Most expensive spell',
      name: expensiveOrder[0]?.name,
      innings: expensiveOrder[0]?.innings,
      metric: expensiveOrder[0] ? `${expensiveOrder[0].overIndexes.map((over) => over + 1).join(', ')} overs · ${expensiveOrder[0].runs} runs · Econ ${(expensiveOrder[0].runs * 6 / expensiveOrder[0].legalBalls).toFixed(2)}` : 'No complete spell in delivery data',
    },
    {
      title: 'Pressure-building spell',
      name: pressureOrder[0]?.name,
      innings: pressureOrder[0]?.innings,
      metric: pressureOrder[0] ? `${pressureOrder[0].overIndexes.map((over) => over + 1).join(', ')} overs · ${pressureOrder[0].dots} dots · ${pressureOrder[0].wickets} wickets · ${pressureOrder[0].boundaryBalls} boundary balls` : 'Needs at least 6 legal balls',
    },
  ]

  return (
    <section className="panel analysis-section">
      <div className="panel-heading">
        <div><span className="section-kicker">EVERY BOWLER · BOTH TEAMS</span><h2>Bowler analysis</h2></div>
        <label className="analysis-filter">INNINGS
          <select value={filter} onChange={(event) => setFilter(event.target.value)}>
            <option value="all">Entire match</option>
            {props.scorecards.map((card) => {
              const team = teamFor(props.teams, card.team_id)
              return <option key={card.innings} value={card.innings}>Innings {card.innings} · {team?.name ?? 'Team unavailable'}</option>
            })}
          </select>
        </label>
      </div>
      {bowlers.length > 0 && <div className="bowler-highlights">
        {features.map(({ title, name, innings, metric }) => <div className="bowler-highlight" key={title}>
          <span>{title}</span>
          <b>{name ?? 'Not established'}</b>
          <small>{innings ? `${bowlingTeamFor(props.teams, props.scorecards.find((card) => card.innings === innings)?.team_id ?? null)?.name ?? `Innings ${innings}`} · ${metric}` : metric}</small>
        </div>)}
      </div>}
      <p className="analysis-method">Economy measures bowler-charged runs; wicket-taking and pressure are separate. Best overall ranks wickets, then economy (6+ legal balls). Match impact is 10 points per wicket plus runs saved versus the innings run rate. Pressure ranks dot-ball rate + wicket rate − boundary-ball rate per 100 balls.</p>
      {visible.length ? <div className="analysis-rows">
        {visible.map((row) => {
          const team = bowlingTeamFor(props.teams, props.scorecards.find((card) => card.innings === row.innings)?.team_id ?? null)
          const inningsBowlers = bowlers.filter((candidate) => candidate.innings === row.innings && candidate.legalBalls > 0)
          const averageEconomy = inningsBowlers.length
            ? inningsBowlers.reduce((sum, candidate) => sum + (candidate.economy ?? 0), 0) / inningsBowlers.length
            : null
          return (
            <details className="analysis-row bowler-analysis-row" key={`${row.innings}-${row.playerId ?? row.name}`}>
              <summary>
                <span className="analysis-over-title">{row.name}<small>{team?.name ?? 'Innings unavailable'} · Innings {row.innings}</small></span>
                <span className="analysis-over-bowler">{row.overs} O · {row.maidens} M · {row.runs} R · {row.wickets} W</span>
                <span className="analysis-over-score">{row.economy == null ? '—' : row.economy.toFixed(2)}<small>ECON</small></span>
              </summary>
              <div className="analysis-over-detail">
                <div className="analysis-stat-grid">
                  <span>Dot-ball rate <b>{row.hasBallData && row.legalBalls ? `${(row.dots * 100 / row.legalBalls).toFixed(1)}% (${row.dots}/${row.legalBalls})` : 'Unavailable'}</b></span>
                  <span>Boundary balls <b>{row.hasBallData ? row.boundaryBalls : 'Unavailable'}</b></span>
                  <span>Wides / no-balls <b>{row.wides} / {row.noBalls}</b></span>
                  <span>Extras on deliveries <b>{row.hasBallData ? row.extras : 'Unavailable'}</b></span>
                  <span>Wickets per spell <b>{row.spells ? `${(row.wickets / row.spells).toFixed(2)} (${row.spells} spell${row.spells === 1 ? '' : 's'})` : 'Unavailable'}</b></span>
                  <span>Innings comparison <b>{averageEconomy == null || row.economy == null ? 'Unavailable' : `${row.economy < averageEconomy ? 'Below' : row.economy > averageEconomy ? 'Above' : 'At'} group econ ${averageEconomy.toFixed(2)}`}</b></span>
                </div>
                <div className="phase-figures">
                  {(['Powerplay', 'Middle', 'Death'] as const).map((phaseName) => {
                    const figures = row.phase[phaseName]
                    return <span key={phaseName}><b>{phaseName}</b>{figures ? `${Math.floor(figures.legalBalls / 6)}.${figures.legalBalls % 6} ov · ${figures.runs} charged runs · ${figures.wickets} wkts · ${figures.dots} dots · ${figures.boundaryBalls} boundaries · ${figures.extras} extras · Econ ${figures.legalBalls ? (figures.runs * 6 / figures.legalBalls).toFixed(2) : '—'}` : props.matchFormat ? 'No deliveries' : 'Unavailable: format not identified'}</span>
                  })}
                </div>
                {row.dismissals.length > 0 && <div className="bowler-dismissals"><b>Bowler-credited wickets</b>{row.dismissals.map((dismissal, index) => <span key={`${row.innings}-${row.playerId}-${index}`}>{dismissal}</span>)}</div>}
                {spells.filter((spell) => spell.innings === row.innings && (
                  row.playerId != null && spell.bowlerId != null
                    ? String(spell.bowlerId) === String(row.playerId)
                    : spell.name === row.name
                )).map((spell, spellIndex) => (
                  <details className="bowler-spell" key={`${row.innings}-${row.name}-${spellIndex}`}>
                    <summary>Spell {spellIndex + 1} · overs {spell.overIndexes.map((over) => over + 1).join(', ')} · {spell.legalBalls} legal · {spell.runs} charged runs · {spell.wickets} wickets</summary>
                    <div className="bowler-spell-overs">{spell.overFigures.map((over) => (
                      <span key={over.overIndex}>
                        Over {over.overIndex + 1}: {over.runs} charged runs · {over.legalBalls} legal · {over.wickets} wickets · {over.dots} dots · {over.boundaries} boundary balls · {over.extras} total extras
                      </span>
                    ))}</div>
                  </details>
                ))}
                {!row.hasBallData && <p className="analysis-muted">Delivery-level dot, boundary, spell, and phase figures are unavailable; scorecard figures are shown.</p>}
              </div>
            </details>
          )
        })}
      </div> : <p className="analysis-muted">No bowler figures are available for this fixture.</p>}
    </section>
  )
}

type BatterReport = {
  innings: string
  teamId: number | string | null
  playerId: number | string | null
  name: string
  runs: number
  balls: number
  fours: number
  sixes: number
  strikeRate: number | null
  dots: number | null
  boundaryBalls: number
  boundaryRuns: number
  contribution: number | null
  dismissal: string | null
  partnerships: string[]
  phases: Record<string, { runs: number; balls: number }>
}

type PhaseReport = {
  name: string
  label: string
  startOver: number
  endOver: number
  runs: number
  wickets: number
  legalBalls: number
  dots: number
  boundaries: number
  extras: number
  runRate: number | null
  averageRunsPerOver: number | null
  bowlingRuns: number
  bowlingWickets: number
  bowlingDots: number
  bowlingBoundaries: number
  bowlingExtras: number
  bowlingEconomy: number | null
  bowlingDotPercentage: number | null
  complete: boolean
  reached: boolean
  leadingBatter: string | null
  leadingBowler: string | null
  leadingPartnership: string | null
  previousRunRate: number | null
  deltaFromPrevious: number | null
}

function phaseRanges(props: AnalysisProps, card: Scorecard) {
  const formatLimits = limits(props.matchFormat)
  if (!formatLimits) return []
  const powerplayBalls = card.powerplay?.boundary_legal_balls ?? formatLimits.powerplay * 6
  const powerplayEndOver = Math.ceil(powerplayBalls / 6)
  const deathStartBall = (formatLimits.overs - 5) * 6
  const powerplayLabel = card.powerplay?.overs
    ? `Overs ${card.powerplay.overs}`
    : card.powerplay?.label ?? `Overs 1-${powerplayEndOver}`
  return [
    { name: 'Powerplay', label: powerplayLabel, startBall: 0, endBall: powerplayBalls },
    {
      name: 'Middle overs',
      label: powerplayBalls % 6 === 0
        ? `Overs ${powerplayBalls / 6 + 1}-${formatLimits.overs - 5}`
        : `Legal balls ${powerplayBalls + 1}-${deathStartBall}`,
      startBall: powerplayBalls,
      endBall: deathStartBall,
    },
    {
      name: 'Death overs',
      label: `Overs ${formatLimits.overs - 4}-${formatLimits.overs}`,
      startBall: deathStartBall,
      endBall: formatLimits.overs * 6,
    },
  ].filter((phase) => phase.endBall > phase.startBall)
}

function batterReports(props: AnalysisProps, card: Scorecard, balls: Delivery[]): BatterReport[] {
  const legalBeforeBySource = new Map<string, number>()
  let legalBefore = 0
  for (const ball of balls) {
    legalBeforeBySource.set(String(ball.source_id), legalBefore)
    legalBefore += Number(isLegal(ball))
  }
  const ranges = phaseRanges(props, card)
  return card.batting.map((score) => {
    const deliveries = balls.filter((ball) => (
      score.player_id != null && ball.batter_id != null
        ? String(score.player_id) === String(ball.batter_id)
        : score.name === ball.batter
    ))
    const runs = deliveries.reduce((sum, ball) => sum + (ball.runs_batter ?? 0), 0)
    const ballsFaced = deliveries.filter((ball) => !ball.is_wide).length
    const fours = deliveries.filter((ball) => ball.four).length
    const sixes = deliveries.filter((ball) => ball.six).length
    const dots = deliveries.filter((ball) => isLegal(ball) && ball.runs_total === 0).length
    const phaseStats: BatterReport['phases'] = {}
    for (const phase of balls.length ? ranges : []) {
      const phaseBalls = deliveries.filter((ball) => {
        const before = legalBeforeBySource.get(String(ball.source_id))
        return before != null && before >= phase.startBall && before < phase.endBall
      })
      phaseStats[phase.name] = {
        runs: phaseBalls.reduce((sum, ball) => sum + (ball.runs_batter ?? 0), 0),
        balls: phaseBalls.filter((ball) => !ball.is_wide).length,
      }
    }
    const partnerships = card.partnerships
      .filter((partnership) => partnership.contributions.some((player) => (
        score.player_id != null && player.player_id != null
          ? String(score.player_id) === String(player.player_id)
          : player.name === score.name
      )))
      .map((partnership) => {
        const contribution = partnership.contributions.find((player) => (
          score.player_id != null && player.player_id != null
            ? String(score.player_id) === String(player.player_id)
            : player.name === score.name
        ))
        return `#${partnership.number}: ${contribution?.runs ?? 0} (${contribution?.balls ?? 0}) of ${partnership.runs} with ${partnership.batters.filter((name) => name !== score.name).join(' & ') || 'partner unavailable'}`
      })
    const batterRuns = balls.length ? runs : score.runs ?? 0
    return {
      innings: card.innings,
      teamId: card.team_id,
      playerId: score.player_id,
      name: score.name,
      runs: balls.length ? runs : score.runs ?? 0,
      balls: balls.length ? ballsFaced : score.balls ?? 0,
      fours: balls.length ? fours : score.fours ?? 0,
      sixes: balls.length ? sixes : score.sixes ?? 0,
      strikeRate: balls.length
        ? ballsFaced ? runs * 100 / ballsFaced : null
        : score.strike_rate,
      dots: balls.length ? dots : null,
      boundaryBalls: balls.length ? fours + sixes : (score.fours ?? 0) + (score.sixes ?? 0),
      boundaryRuns: balls.length ? fours * 4 + sixes * 6 : (score.fours ?? 0) * 4 + (score.sixes ?? 0) * 6,
      contribution: card.runs ? batterRuns * 100 / card.runs : null,
      dismissal: score.dismissal,
      partnerships,
      phases: phaseStats,
    }
  })
}

function phaseReports(
  props: AnalysisProps,
  card: Scorecard,
  balls: Delivery[],
  completed: boolean,
): PhaseReport[] {
  const ranges = phaseRanges(props, card)
  const legalBeforeBySource = new Map<string, number>()
  let legalBefore = 0
  for (const ball of balls) {
    legalBeforeBySource.set(String(ball.source_id), legalBefore)
    legalBefore += Number(isLegal(ball))
  }
  const sourcePartnerships = new Map(card.partnerships.map((partnership) => [partnership.number, partnership]))
  const reports: PhaseReport[] = ranges.map((phase) => {
    const inPhase = balls.filter((ball) => {
      const before = legalBeforeBySource.get(String(ball.source_id))
      return before != null && before >= phase.startBall && before < phase.endBall
    })
    const legalBalls = inPhase.filter(isLegal).length
    const runs = inPhase.reduce((sum, ball) => sum + ball.runs_total, 0)
    const bowlingRuns = inPhase.reduce((sum, ball) => sum + (ball.runs_bowler ?? ball.runs_total), 0)
    const bowlingLegalBalls = legalBalls
    const batterRunLeaders = new Map<string, number>()
    const bowlerWicketLeaders = new Map<string, number>()
    const partnershipPhaseRuns = new Map<number, number>()
    let activePartnership = 1
    for (const ball of balls) {
      if (inPhase.includes(ball)) {
        const batterName = ball.batter ?? 'Batter unavailable'
        batterRunLeaders.set(batterName, (batterRunLeaders.get(batterName) ?? 0) + (ball.runs_batter ?? 0))
        if (ball.bowler_wicket) {
          const bowlerName = ball.bowler ?? 'Bowler unavailable'
          bowlerWicketLeaders.set(bowlerName, (bowlerWicketLeaders.get(bowlerName) ?? 0) + 1)
        }
        partnershipPhaseRuns.set(activePartnership, (partnershipPhaseRuns.get(activePartnership) ?? 0) + ball.runs_total)
      }
      if (ball.is_wicket || ball.is_retired_hurt) activePartnership += 1
    }
    const leadingBatter = [...batterRunLeaders].sort((a, b) => b[1] - a[1])[0]
    const leadingBowler = [...bowlerWicketLeaders].sort((a, b) => b[1] - a[1])[0]
    const leadingPartnership = [...partnershipPhaseRuns].sort((a, b) => b[1] - a[1])[0]
    const expectedLegalBalls = phase.endBall - phase.startBall
    const phaseComplete = legalBalls >= expectedLegalBalls || (completed && inPhase.length > 0)
    return {
      name: phase.name,
      label: phase.label,
      startOver: Math.floor(phase.startBall / 6) + 1,
      endOver: Math.ceil(phase.endBall / 6),
      runs,
      wickets: inPhase.filter((ball) => ball.is_wicket).length,
      legalBalls,
      dots: inPhase.filter((ball) => isLegal(ball) && ball.runs_total === 0).length,
      boundaries: inPhase.filter((ball) => ball.four || ball.six).length,
      extras: inPhase.reduce((sum, ball) => sum + (ball.runs_extras ?? 0), 0),
      runRate: legalBalls ? runs * 6 / legalBalls : null,
      averageRunsPerOver: legalBalls ? runs / (legalBalls / 6) : null,
      bowlingRuns,
      bowlingWickets: inPhase.filter((ball) => ball.bowler_wicket).length,
      bowlingDots: inPhase.filter((ball) => isLegal(ball) && ball.runs_total === 0).length,
      bowlingBoundaries: inPhase.filter((ball) => ball.four || ball.six).length,
      bowlingExtras: inPhase.reduce((sum, ball) => sum + (ball.runs_extras ?? 0), 0),
      bowlingEconomy: bowlingLegalBalls ? bowlingRuns * 6 / bowlingLegalBalls : null,
      bowlingDotPercentage: bowlingLegalBalls
        ? inPhase.filter((ball) => isLegal(ball) && ball.runs_total === 0).length * 100 / bowlingLegalBalls
        : null,
      complete: phaseComplete,
      reached: inPhase.length > 0,
      leadingBatter: leadingBatter ? `${leadingBatter[0]} (${leadingBatter[1]})` : null,
      leadingBowler: leadingBowler ? `${leadingBowler[0]} (${leadingBowler[1]} wkts)` : null,
      leadingPartnership: leadingPartnership
        ? `Partnership ${leadingPartnership[0]}: ${sourcePartnerships.get(leadingPartnership[0])?.batters.join(' & ') ?? 'pair unavailable'} · ${leadingPartnership[1]} in phase`
        : null,
      previousRunRate: null,
      deltaFromPrevious: null,
    }
  })
  for (let index = 0; index < reports.length; index += 1) {
    const previous = reports[index - 1]
    const current = reports[index]
    if (previous?.runRate != null && current?.runRate != null) {
      current.previousRunRate = previous.runRate
      current.deltaFromPrevious = current.runRate - previous.runRate
    }
  }
  return reports
}

function buildMatchReport(props: AnalysisProps, minimumImpactBalls: number) {
  const formatLimits = limits(props.matchFormat)
  const currentLabel = props.innings.length ? String(props.innings[props.innings.length - 1].innings) : null
  const inningsReports = props.scorecards.map((card, cardIndex) => {
    const inning = props.innings.find((item) => String(item.innings) === card.innings)
    const balls = props.deliveries.filter((ball) => String(ball.innings) === card.innings)
    const complete = props.isFinished || card.innings !== currentLabel || (card.wickets ?? 0) >= 10
      || (formatLimits != null && (card.legal_balls ?? 0) >= formatLimits.overs * 6)
    const batters = batterReports(props, card, balls)
    const batterRunTotal = balls.reduce((total, ball) => total + (ball.runs_batter ?? 0), 0)
    const teamRuns = card.runs ?? balls.reduce((total, ball) => total + ball.runs_total, 0)
    const legalBalls = balls.filter(isLegal).length
    const dots = balls.filter((ball) => isLegal(ball) && ball.runs_total === 0).length
    const fours = balls.filter((ball) => ball.four).length
    const sixes = balls.filter((ball) => ball.six).length
    const boundaryRuns = fours * 4 + sixes * 6
    const oppositionCard = props.scorecards.find((other) => String(other.team_id) !== String(card.team_id))
    const bowlersAgainst = oppositionCard
      ? makeBowlers({ ...props, scorecards: [oppositionCard] }).filter((bowler) => (
        String(bowler.innings) === oppositionCard.innings
        && props.scorecards.find((scorecard) => scorecard.innings === bowler.innings)?.team_id != null
      ))
      : []
    const attackBowlers = makeBowlers(props).filter((bowler) => bowler.innings === card.innings)
    const sortedBatters = [...batters].sort((a, b) => b.runs - a.runs)
    return {
      card,
      team: teamFor(props.teams, card.team_id),
      opponent: bowlingTeamFor(props.teams, card.team_id),
      complete,
      current: card.innings === currentLabel && !complete,
      balls,
      batters,
      topBatters: sortedBatters.slice(0, 3),
      runs: teamRuns,
      wickets: card.wickets ?? balls.filter((ball) => ball.is_wicket).length,
      legalBalls,
      runRate: legalBalls ? teamRuns * 6 / legalBalls : card.run_rate,
      dotBalls: dots,
      dotPercentage: legalBalls ? dots * 100 / legalBalls : null,
      fours,
      sixes,
      boundaryRuns,
      boundaryContribution: teamRuns ? boundaryRuns * 100 / teamRuns : null,
      extras: balls.reduce((sum, ball) => sum + (ball.runs_extras ?? 0), 0),
      batterRunTotal,
      partnerships: card.partnerships,
      phases: phaseReports(props, card, balls, complete),
      attackBowlers,
      bowlingSideRows: bowlersAgainst,
      inningsIndex: cardIndex,
    }
  })
  const overRows = makeOvers(props)
  const partnerships = inningsReports.flatMap((report) => report.partnerships.map((partnership) => ({
    ...partnership,
    innings: report.card.innings,
    team: report.team?.name ?? 'Team unavailable',
  })))
  const topBatter = inningsReports.flatMap((report) => report.batters.map((batter) => ({
    ...batter,
    team: report.team?.name ?? 'Team unavailable',
  }))).sort((a, b) => b.runs - a.runs)[0]
  const topStrikeRate = inningsReports.flatMap((report) => report.batters.map((batter) => ({
    ...batter,
    team: report.team?.name ?? 'Team unavailable',
  }))).filter((batter) => batter.balls >= minimumImpactBalls).sort((a, b) => (b.strikeRate ?? 0) - (a.strikeRate ?? 0))[0]
  const impactBatter = inningsReports.flatMap((report) => report.batters.map((batter) => ({
    ...batter,
    team: report.team?.name ?? 'Team unavailable',
    inningsRunRate: report.runRate,
    paceImpact: report.runRate == null || batter.balls <= 0
      ? null
      : batter.runs - report.runRate * batter.balls / 6,
  }))).filter((batter) => batter.balls >= minimumImpactBalls && batter.paceImpact != null)
    .sort((a, b) => (b.paceImpact ?? -Infinity) - (a.paceImpact ?? -Infinity))[0]
  const slowScoringSignals = inningsReports.flatMap((report) => report.batters
    .filter((batter) => batter.balls >= minimumImpactBalls && batter.strikeRate != null && report.runRate != null)
    .map((batter) => ({
      ...batter,
      team: report.team?.name ?? 'Team unavailable',
      inningsRunRate: report.runRate,
      expectedRuns: report.runRate! * batter.balls / 6,
      belowPace: report.runRate! * batter.balls / 6 - batter.runs,
    })))
    .filter((batter) => batter.belowPace >= Math.max(5, batter.expectedRuns * 0.2))
    .sort((a, b) => b.belowPace - a.belowPace)
  const boundaryLeader = inningsReports.flatMap((report) => report.batters.map((batter) => ({
    ...batter,
    team: report.team?.name ?? 'Team unavailable',
  }))).sort((a, b) => b.boundaryRuns - a.boundaryRuns)[0]
  const shareLeader = inningsReports.flatMap((report) => report.batters.map((batter) => ({
    ...batter,
    team: report.team?.name ?? 'Team unavailable',
  }))).sort((a, b) => (b.contribution ?? 0) - (a.contribution ?? 0))[0]
  const bestOver = [...overRows].sort((a, b) => b.runs - a.runs || b.wickets - a.wickets)[0]
  const wicketOver = [...overRows].filter((over) => over.wickets > 0).sort((a, b) => b.wickets - a.wickets || a.runs - b.runs)[0]
  const biggestShift = [...overRows].map((over, index, list) => {
    const previous = [...list.slice(0, index)].reverse().find((item) => item.innings === over.innings)
    return previous ? { over, previous, change: over.runs - previous.runs } : null
  }).filter((item): item is { over: Over; previous: Over; change: number } => item != null)
    .sort((a, b) => Math.abs(b.change) - Math.abs(a.change))[0]
  const phaseComparisons = ['Powerplay', 'Middle overs', 'Death overs'].map((phaseName) => {
    const first = inningsReports[0]?.phases.find((phase) => phase.name === phaseName)
    const second = inningsReports[1]?.phases.find((phase) => phase.name === phaseName)
    if (!first || !second) return { phaseName, available: false as const, reason: 'Equivalent phase data is unavailable.' }
    const comparable = first.complete && second.complete && first.reached && second.reached
    let battingEdge: string | null = null
    if (comparable && first.runRate != null && second.runRate != null) {
      if (Math.abs(first.runRate - second.runRate) < 0.01 && first.wickets === second.wickets) battingEdge = 'Even on scoring rate and wickets'
      else if (first.runRate > second.runRate && first.wickets <= second.wickets) battingEdge = inningsReports[0]?.team?.name ?? 'Innings 1'
      else if (second.runRate > first.runRate && second.wickets <= first.wickets) battingEdge = inningsReports[1]?.team?.name ?? 'Innings 2'
      else battingEdge = 'Split: scoring rate and wickets favor different innings'
    }
    return { phaseName, available: true as const, comparable, battingEdge, first, second }
  })
  const turningPoints = [
    bestOver && { kind: 'Highest-scoring over', innings: bestOver.innings, over: bestOver.index + 1, team: inningsReports.find((item) => item.card.innings === bestOver.innings)?.team?.name, detail: `${bestOver.runs} runs; ${bestOver.wickets} wicket(s). Highest team total in a recorded over.` },
    wicketOver && { kind: 'Wicket cluster', innings: wicketOver.innings, over: wicketOver.index + 1, team: inningsReports.find((item) => item.card.innings === wicketOver.innings)?.opponent?.name, detail: `${wicketOver.wickets} wicket(s) for ${wicketOver.runs} runs; this over contained the most wickets.` },
    biggestShift && { kind: 'Largest over-to-over scoring change', innings: biggestShift.over.innings, over: biggestShift.over.index + 1, team: inningsReports.find((item) => item.card.innings === biggestShift.over.innings)?.team?.name, detail: `${biggestShift.previous.runs} to ${biggestShift.over.runs} runs (${biggestShift.change > 0 ? '+' : ''}${biggestShift.change}).` },
    [...partnerships].sort((a, b) => b.runs - a.runs)[0] && {
      kind: 'Largest partnership',
      innings: [...partnerships].sort((a, b) => b.runs - a.runs)[0].innings,
      over: null,
      team: [...partnerships].sort((a, b) => b.runs - a.runs)[0].team,
      detail: `${[...partnerships].sort((a, b) => b.runs - a.runs)[0].batters.join(' & ')} added ${[...partnerships].sort((a, b) => b.runs - a.runs)[0].runs} runs from ${[...partnerships].sort((a, b) => b.runs - a.runs)[0].balls} balls.`,
    },
  ].filter((item): item is NonNullable<typeof item> => item != null)
  const firstInnings = inningsReports[0]
  const secondInnings = inningsReports[1]
  const target = firstInnings?.runs != null ? firstInnings.runs + 1 : null
  const runsRequired = target != null && secondInnings ? Math.max(0, target - secondInnings.runs) : null
  const remainingBalls = formatLimits && secondInnings
    ? Math.max(0, formatLimits.overs * 6 - secondInnings.legalBalls)
    : null
  return {
    formatKnown: formatLimits != null,
    innings: inningsReports,
    phaseComparisons,
    turningPoints,
    topBatter,
    topStrikeRate,
    impactBatter,
    slowScoringSignals,
    boundaryLeader,
    shareLeader,
    bestOver,
    wicketOver,
    target,
    runsRequired,
    remainingBalls,
    partnerships,
    isFinished: props.isFinished,
    status: props.isFinished ? 'Final' : 'Provisional live analysis',
    completedOverCount: overRows.filter((over) => !over.inProgress).length,
    overCount: overRows.length,
  }
}

export function FullMatchAnalysis(props: AnalysisProps) {
  const [minimumImpactBalls, setMinimumImpactBalls] = useState(10)
  const report = useMemo(() => buildMatchReport(props, minimumImpactBalls), [
    props.teams,
    props.innings,
    props.scorecards,
    props.deliveries,
    props.matchFormat,
    props.isFinished,
    minimumImpactBalls,
  ])
  return (
    <div className="full-match-report">
      <section className="panel analysis-section narrative-section">
        <div className="panel-heading">
          <div><span className="section-kicker">DELIVERY-GROUNDED DESCRIPTIVE ANALYSIS</span><h2>THE STORY OF THE MATCH</h2></div>
          <span className="narrative-confidence-note">Facts and interpretation are separated; unavailable details are identified.</span>
        </div>
        <div className="narrative-innings-list">
          {report.innings.map((inning) => (
            <NarrativePanel key={`narrative-innings-${inning.card.innings}`} props={props} scope="innings" innings={inning.card.innings} />
          ))}
          {!report.innings.length && <p className="analysis-muted">An innings narrative is unavailable until delivery-backed innings data is present.</p>}
        </div>
      </section>

      <section className="panel analysis-section">
        <div className="panel-heading">
          <div><span className="section-kicker">MATCH-WIDE · {report.status.toUpperCase()}</span><h2>Complete match assessment</h2></div>
          <label className="analysis-filter">MINIMUM IMPACT SAMPLE
            <select value={minimumImpactBalls} onChange={(event) => setMinimumImpactBalls(Number(event.target.value))}>
              {[6, 10, 15, 20].map((balls) => <option key={balls} value={balls}>{balls} balls faced</option>)}
            </select>
          </label>
        </div>
        <div className="report-scoreline">
          {report.innings.map((inning) => <div key={inning.card.innings}>
            <span>Innings {inning.card.innings} · {inning.team?.name ?? 'Team unavailable'}</span>
            <b>{inning.runs}/{inning.wickets}</b>
            <small>{inning.card.overs ?? '—'} ov · RR {inning.runRate?.toFixed(2) ?? '—'} · {inning.complete ? 'complete' : 'in progress'}</small>
          </div>)}
        </div>
        {report.target != null && report.runsRequired != null && report.remainingBalls != null && (
          <p className="analysis-method">Target {report.target}; {report.runsRequired} required from {report.remainingBalls} legal balls. {report.isFinished ? 'Final match snapshot.' : 'This is provisional and updates with the live feed.'}</p>
        )}
        {!report.formatKnown && <p className="analysis-muted">Phase comparisons are unavailable because the match format was not identified.</p>}
        <div className="report-rankings">
          <div><span>Impact vs innings scoring pace</span><b>{report.impactBatter ? `${report.impactBatter.name} · ${report.impactBatter.runs} (${report.impactBatter.balls})` : `No batter has ${minimumImpactBalls}+ balls faced`}</b><small>{report.impactBatter ? `${report.impactBatter.team} · ${report.impactBatter.paceImpact! >= 0 ? '+' : ''}${report.impactBatter.paceImpact!.toFixed(1)} runs vs innings-rate baseline` : 'Sample threshold applies across both innings'}</small></div>
          <div><span>Fastest rate · minimum sample</span><b>{report.topStrikeRate ? `${report.topStrikeRate.name} · ${report.topStrikeRate.strikeRate?.toFixed(1)}` : `No batter has ${minimumImpactBalls}+ balls faced`}</b><small>{report.topStrikeRate?.team ?? ''} · {minimumImpactBalls}+ balls faced</small></div>
          <div><span>Boundary contribution</span><b>{report.boundaryLeader ? `${report.boundaryLeader.name} · ${report.boundaryLeader.boundaryRuns} boundary runs` : 'Unavailable'}</b><small>{report.boundaryLeader?.team ?? ''}</small></div>
          <div><span>Largest share of team total</span><b>{report.shareLeader ? `${report.shareLeader.name} · ${report.shareLeader.contribution?.toFixed(1)}%` : 'Unavailable'}</b><small>{report.shareLeader?.team ?? ''} · significant-innings criterion: share of team total</small></div>
        </div>
        <div className="analysis-evidence-list">
          <h3>Under-par scoring signals · minimum sample {minimumImpactBalls} balls</h3>
          {report.slowScoringSignals.length
            ? report.slowScoringSignals.slice(0, 4).map((batter) => (
              <p key={`${batter.innings}-${batter.playerId ?? batter.name}`}>{batter.name} ({batter.team}, innings {batter.innings}): {batter.runs} runs from {batter.balls} balls vs {batter.expectedRuns.toFixed(1)} expected at innings rate; {batter.belowPace.toFixed(1)} runs below that baseline. This is a scoring-rate signal, not a standalone poor-performance verdict.</p>
            ))
            : <p>No batter met the configured sample and under-pace evidence threshold.</p>}
        </div>
      </section>

      <section className="panel analysis-section">
        <div className="panel-heading"><div><span className="section-kicker">BOTH INNINGS · TEAM-SPECIFIC EVIDENCE</span><h2>Innings analysis</h2></div></div>
        <div className="innings-analysis-grid">
          {report.innings.map((inning) => {
            const battingContribution = inning.runs ? inning.batterRunTotal * 100 / inning.runs : null
            const dismissals = inning.card.fall_of_wickets
            const opponentBowling = inning.attackBowlers
              .filter((bowler) => bowler.legalBalls > 0)
              .sort((a, b) => b.wickets - a.wickets || (a.economy ?? Infinity) - (b.economy ?? Infinity))
            const bowlingRows = inning.bowlingSideRows
            return (
              <article className="innings-analysis-card" key={`innings-report-${inning.card.innings}`}>
                <header>
                  <div><span className="section-kicker">INNINGS {inning.card.innings} · {inning.complete ? 'COMPLETED' : 'PROVISIONAL'}</span><h3>{inning.team?.name ?? 'Team unavailable'}</h3></div>
                  <b>{inning.runs}/{inning.wickets}</b>
                </header>
                <p className="innings-matchup-label">Batting against {inning.opponent?.name ?? 'opposition unavailable'}</p>
                <div className="analysis-stat-grid">
                  <span>Run rate <b>{inning.runRate?.toFixed(2) ?? '—'}</b></span>
                  <span>Dot balls <b>{inning.dotPercentage == null ? 'Unavailable' : `${inning.dotPercentage.toFixed(1)}% (${inning.dotBalls}/${inning.legalBalls})`}</b></span>
                  <span>Boundaries <b>{inning.fours} fours · {inning.sixes} sixes</b></span>
                  <span>Boundary runs <b>{inning.boundaryRuns} ({inning.boundaryContribution?.toFixed(1) ?? '—'}% of total)</b></span>
                  <span>Extras <b>{inning.extras}</b></span>
                  <span>Runs off bat <b>{inning.batterRunTotal} ({battingContribution?.toFixed(1) ?? '—'}% of total)</b></span>
                </div>
                <div className="report-evidence-columns">
                  <div><b>Scoring and batting</b>
                    <p>{inning.topBatters.map((batter) => `${batter.name} ${batter.runs} (${batter.balls}), SR ${batter.strikeRate?.toFixed(1) ?? '—'}`).join(' · ') || 'No batter figures available.'}</p>
                    <p>Wickets fell: {dismissals.length ? dismissals.map((wicket) => `${wicket.batter ?? 'Batter unavailable'} ${wicket.score}/${wicket.wicket} at ${wicket.over ?? 'over unavailable'}`).join(' · ') : 'None recorded'}</p>
                    <p>Partnerships: {inning.partnerships.length ? inning.partnerships.map((partnership) => `${partnership.batters.join(' & ')} ${partnership.runs} (${partnership.balls})`).join(' · ') : 'Unavailable'}</p>
                  </div>
                  {props.onPlayerSelect && <div className="player-analysis-links">
                    {inning.card.batting.map((batter) => <button key={`bat-${batter.player_id ?? batter.name}`} type="button" onClick={() => props.onPlayerSelect?.({
                      role: 'batter', playerId: batter.player_id, name: batter.name, innings: inning.card.innings,
                    })}>Analyze {batter.name} · batting</button>)}
                    {inning.card.bowling.map((bowler) => <button key={`bowl-${bowler.player_id ?? bowler.name}`} type="button" onClick={() => props.onPlayerSelect?.({
                      role: 'bowler', playerId: bowler.player_id, name: bowler.name, innings: inning.card.innings,
                    })}>Analyze {bowler.name} · bowling</button>)}
                  </div>}
                  <div><b>Bowling against {inning.team?.name ?? 'this team'}</b>
                    {bowlingRows.length
                      ? <p>{bowlingRows.map((bowler) => `${bowler.name} ${bowler.overs} ov, ${bowler.runs} runs, ${bowler.wickets} wickets, Econ ${bowler.economy?.toFixed(2) ?? '—'}`).join(' · ')}</p>
                      : <p>Opposition bowling figures are not present in the delivery-backed scorecard.</p>}
                    <p>Most wickets in this innings: {opponentBowling[0] ? `${opponentBowling[0].name} (${opponentBowling[0].wickets})` : 'No wicket credited to a bowler yet'}.</p>
                  </div>
                </div>
                <p className="innings-data-interpretation">
                  Evidence: {inning.boundaryContribution != null && inning.boundaryContribution >= 40
                    ? `${inning.boundaryContribution.toFixed(1)}% of the total came in boundaries`
                    : `the boundary contribution was ${inning.boundaryContribution?.toFixed(1) ?? 'unavailable'}%`}
                  ; {inning.dotPercentage?.toFixed(1) ?? '—'}% of legal balls were dots; {dismissals.length} wicket(s) fell.
                </p>
              </article>
            )
          })}
        </div>
      </section>

      <OverByOverAnalysis {...props} />

      <section className="panel analysis-section">
        <div className="panel-heading"><div><span className="section-kicker">EQUIVALENT PHASES · COMPLETION-AWARE</span><h2>Phase-wise comparison</h2></div></div>
        {report.phaseComparisons.length
          ? <div className="phase-report-list">{report.phaseComparisons.map((comparison) => {
            if (!comparison.available) return <p className="analysis-muted" key={comparison.phaseName}>{comparison.phaseName}: {comparison.reason}</p>
            const first = comparison.first
            const second = comparison.second
            return (
              <details className="phase-report" key={comparison.phaseName}>
                <summary>{comparison.phaseName} · {first.label} / {second.label} overs
                  <span>{comparison.comparable ? `Statistical control signal: ${comparison.battingEdge}` : 'Not compared: one or both phases are incomplete'}</span>
                </summary>
                <div className="phase-comparison-grid">
                  {[0, 1].map((index) => {
                    const phase = index === 0 ? first : second
                    const inning = report.innings[index]
                    if (!inning) return null
                    return (
                      <article key={`${comparison.phaseName}-${inning.team?.id ?? index}`}>
                        <b>{inning.team?.name ?? `Innings ${index + 1}`} · {phase.complete ? 'complete' : phase.reached ? 'in progress' : 'not reached'}</b>
                        <span>Batting: {phase.runs} runs · {phase.wickets} wickets · RR {phase.runRate?.toFixed(2) ?? '—'} · {phase.boundaries} boundaries · {phase.dots} dots ({phase.legalBalls ? (phase.dots * 100 / phase.legalBalls).toFixed(1) : '—'}%) · {phase.extras} extras · {phase.averageRunsPerOver?.toFixed(1) ?? '—'} runs/over</span>
                        <span>Bowling: {phase.bowlingRuns} runs conceded · Econ {phase.bowlingEconomy?.toFixed(2) ?? '—'} · {phase.bowlingWickets} bowler wickets · {phase.bowlingDots} dots ({phase.bowlingDotPercentage?.toFixed(1) ?? '—'}%) · {phase.bowlingBoundaries} boundary balls · {phase.bowlingExtras} extras</span>
                        <span>Leading batter: {phase.leadingBatter ?? 'Unavailable'} · Leading bowler: {phase.leadingBowler ?? 'No bowler wicket'} · Partnership contribution: {phase.leadingPartnership ?? 'Unavailable'}</span>
                        <span>Run-rate change from preceding phase: {phase.deltaFromPrevious == null ? 'Unavailable' : `${phase.deltaFromPrevious > 0 ? '+' : ''}${phase.deltaFromPrevious.toFixed(2)}`}</span>
                      </article>
                    )
                  })}
                </div>
                <p className="analysis-method">The control signal compares batting run rate and wickets lost only when both phases are reached and complete; bowling measures are shown separately. It does not infer tactical causes.</p>
              </details>
            )
          })}</div>
          : <p className="analysis-muted">Phase splits unavailable for the current match format.</p>}
      </section>

      <section className="panel analysis-section">
        <div className="panel-heading"><div><span className="section-kicker">EVERY BATTER · BOTH TEAMS</span><h2>Batter-wise analysis</h2></div></div>
        <div className="batter-analysis-innings">
          {report.innings.map((inning) => (
            <details className="batter-innings" key={`batters-${inning.card.innings}`} open>
              <summary>Innings {inning.card.innings} · {inning.team?.name ?? 'Team unavailable'} · {inning.batters.length} batters</summary>
              <div className="analysis-table-wrap">
                <table className="analysis-table batter-analysis-table">
                  <thead><tr><th>Batter</th><th>R</th><th>B</th><th>SR</th><th>4/6</th><th>Boundary balls</th><th>Dot %</th><th>Team share</th><th>Dismissal</th><th>Partnerships</th><th>Phases</th></tr></thead>
                  <tbody>{inning.batters.map((batter) => (
                    <tr key={`${batter.playerId ?? batter.name}-${batter.innings}`}>
                      <td>{batter.name}</td><td>{batter.runs}</td><td>{batter.balls}</td><td>{batter.strikeRate?.toFixed(1) ?? '—'}</td>
                      <td>{batter.fours}/{batter.sixes}</td><td>{batter.boundaryBalls ? `${(batter.boundaryBalls * 100 / Math.max(batter.balls, 1)).toFixed(1)}%` : '0%'}</td>
                      <td>{batter.balls && batter.dots != null ? `${(batter.dots * 100 / batter.balls).toFixed(1)}%` : '—'}</td>
                      <td>{batter.contribution?.toFixed(1) ?? '—'}%</td><td>{batter.dismissal ?? 'not out / unavailable'}</td>
                      <td>{batter.partnerships.join(', ') || 'Unavailable'}</td>
                      <td>{Object.entries(batter.phases).map(([name, figures]) => `${name}: ${figures.runs} (${figures.balls})`).join(' · ') || 'Unavailable'}</td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
              <p className="analysis-method">Strike-rate leader is qualified at 10 balls faced. Boundary-ball percentage = (fours + sixes) / balls faced; dot-ball percentage = legal zero-run deliveries / balls faced; team share = batter runs / innings total.</p>
              {inning.card.did_not_bat.length > 0 && <p className="analysis-muted">Did not bat: {inning.card.did_not_bat.map((player) => player.name).join(', ')}</p>}
            </details>
          ))}
        </div>
      </section>

      <BowlerAnalysis {...props} />

      <section className="panel analysis-section">
        <div className="panel-heading"><div><span className="section-kicker">EVIDENCE-BASED TURNING POINTS</span><h2>Match momentum and turning points</h2></div></div>
        <p className="analysis-method">Ranked only from observed score changes, wicket counts, runs per over, and partnership totals. These statistics identify events; they do not prove the tactical cause.</p>
        {report.turningPoints.length
          ? <div className="turning-point-list">{report.turningPoints.map((point, index) => (
            <article key={`${point.kind}-${index}`}><span>{point.kind}</span><b>{point.team ?? 'Team unavailable'}{point.over != null ? ` · Over ${point.over}` : ''}</b><small>Innings {point.innings} · {point.detail}</small></article>
          ))}</div>
          : <p className="analysis-muted">Turning points are unavailable without recorded deliveries and partnerships.</p>}
        {report.isFinished
          ? <p className="analysis-method">Final summary: {report.innings.map((inning) => `${inning.team?.name ?? `Innings ${inning.card.innings}`} scored ${inning.runs}/${inning.wickets} at RR ${inning.runRate?.toFixed(2) ?? '—'}`).join('; ')}. Result causality beyond the recorded score, dismissals, and phase data is not inferred.</p>
          : <p className="analysis-method">Live match-wide report is provisional. Completed first-innings evidence remains available while the chase proceeds.</p>}
      </section>
    </div>
  )
}

export function PlayerAnalysisDetail({
  props,
  player,
  onBack,
}: {
  props: AnalysisProps
  player: PlayerSelection
  onBack: () => void
}) {
  const analysis = useMemo(() => {
    const balls = props.deliveries.filter((ball) => String(ball.innings) === player.innings)
    const card = props.scorecards.find((item) => item.innings === player.innings)
    const playerMatches = (id: number | string | null, name: string | null) => (
      player.playerId != null && id != null
        ? String(player.playerId) === String(id)
        : player.name === name
    )
    let legalBefore = 0
    const overMap = new Map<number, Delivery[]>()
    const progression: { index: number; runs: number }[] = []
    let cumulativeRuns = 0
    const ownBalls: Delivery[] = []
    for (const ball of balls) {
      const index = overIndex(ball, legalBefore)
      const over = overMap.get(index) ?? []
      over.push(ball)
      overMap.set(index, over)
      const isPlayerBall = player.role === 'batter'
        ? playerMatches(ball.batter_id, ball.batter)
        : playerMatches(ball.bowler_id, ball.bowler)
      if (isPlayerBall) {
        ownBalls.push(ball)
        if (player.role === 'batter') {
          cumulativeRuns += ball.runs_batter ?? 0
          if (!ball.is_wide) progression.push({ index: progression.length + 1, runs: cumulativeRuns })
        }
      }
      legalBefore += Number(isLegal(ball))
    }

    const ownOvers = [...overMap.entries()].map(([index, over]) => {
      const selected = over.filter((ball) => player.role === 'batter'
        ? playerMatches(ball.batter_id, ball.batter)
        : playerMatches(ball.bowler_id, ball.bowler))
      const legalBalls = selected.filter(isLegal).length
      return {
        index,
        deliveries: selected,
        runs: selected.reduce((sum, ball) => sum + (player.role === 'batter'
          ? ball.runs_batter ?? 0
          : ball.runs_bowler ?? ball.runs_total), 0),
        balls: player.role === 'batter'
          ? selected.filter((ball) => !ball.is_wide).length
          : legalBalls,
        legalBalls,
        wickets: selected.filter((ball) => ball.bowler_wicket).length,
        dots: selected.filter((ball) => isLegal(ball) && ball.runs_total === 0).length,
        boundaries: selected.filter((ball) => ball.four || ball.six).length,
      }
    }).filter((over) => over.deliveries.length).sort((a, b) => a.index - b.index)

    const batterBalls = player.role === 'batter' ? ownBalls : []
    const bowlerBalls = player.role === 'bowler' ? ownBalls : []
    const runs = batterBalls.reduce((sum, ball) => sum + (ball.runs_batter ?? 0), 0)
    const ballsFaced = batterBalls.filter((ball) => !ball.is_wide).length
    const fours = batterBalls.filter((ball) => ball.four).length
    const sixes = batterBalls.filter((ball) => ball.six).length
    const legalBalls = bowlerBalls.filter(isLegal).length
    const bowlerRuns = bowlerBalls.reduce((sum, ball) => sum + (ball.runs_bowler ?? ball.runs_total), 0)
    const bowlerWickets = bowlerBalls.filter((ball) => ball.bowler_wicket).length
    const inning = props.innings.find((item) => String(item.innings) === player.innings)
    const matchups = new Map<string, { runs: number; balls: number; outs: number }>()
    for (const ball of player.role === 'batter' ? batterBalls : bowlerBalls) {
      const opponent = player.role === 'batter' ? ball.bowler : ball.batter
      const id = player.role === 'batter' ? ball.bowler_id : ball.batter_id
      const key = String(id ?? opponent ?? 'Unavailable')
      const row = matchups.get(key) ?? { runs: 0, balls: 0, outs: 0 }
      row.runs += player.role === 'batter'
        ? ball.runs_batter ?? 0
        : ball.runs_bowler ?? ball.runs_total
      row.balls += player.role === 'batter'
        ? Number(!ball.is_wide)
        : Number(isLegal(ball))
      if (player.role === 'batter' && ball.is_wicket && playerMatches(ball.dismissed_batter_id, ball.dismissed_batter)) row.outs += 1
      if (player.role === 'bowler' && ball.bowler_wicket) row.outs += 1
      matchups.set(key, row)
    }
    const partnerships = (card?.partnerships ?? [])
      .map((partnership) => ({
        ...partnership,
        contribution: partnership.contributions.find((contribution) => playerMatches(contribution.player_id, contribution.name)),
      }))
      .filter((partnership) => partnership.contribution)
    const dismissal = balls.find((ball) => ball.is_wicket
      && playerMatches(ball.dismissed_batter_id, ball.dismissed_batter ?? ball.batter))
    const overs = ownOvers.map((over) => ({
      ...over,
      economy: over.legalBalls ? over.runs * 6 / over.legalBalls : null,
    }))
    const bestOver = overs.length
      ? [...overs].sort((a, b) => player.role === 'batter'
        ? b.runs - a.runs
        : (a.economy ?? Infinity) - (b.economy ?? Infinity))[0]
      : null
    const toughestOver = overs.length
      ? [...overs].sort((a, b) => player.role === 'batter'
        ? a.runs - b.runs
        : (b.economy ?? -Infinity) - (a.economy ?? -Infinity))[0]
      : null
    const phases = limits(props.matchFormat)
      ? (() => {
        const format = limits(props.matchFormat)!
        return [
          { name: 'Powerplay', start: 0, end: format.powerplay * 6 },
          { name: 'Middle', start: format.powerplay * 6, end: (format.overs - 5) * 6 },
          { name: 'Death', start: (format.overs - 5) * 6, end: format.overs * 6 },
        ].map((phase) => {
          let ballsBefore = 0
          const phaseBalls = balls.filter((ball) => {
            const inPhase = ballsBefore >= phase.start && ballsBefore < phase.end
            ballsBefore += Number(isLegal(ball))
            return inPhase && (player.role === 'batter'
              ? playerMatches(ball.batter_id, ball.batter)
              : playerMatches(ball.bowler_id, ball.bowler))
          })
          const phaseLegal = phaseBalls.filter(isLegal).length
          const phaseRuns = phaseBalls.reduce((sum, ball) => sum + (player.role === 'batter'
            ? ball.runs_batter ?? 0
            : ball.runs_bowler ?? ball.runs_total), 0)
          const phaseBallsFaced = phaseBalls.filter((ball) => !ball.is_wide).length
          return {
            name: phase.name,
            runs: phaseRuns,
            balls: player.role === 'batter' ? phaseBallsFaced : phaseLegal,
            wickets: phaseBalls.filter((ball) => ball.bowler_wicket).length,
            rate: player.role === 'batter'
              ? phaseBallsFaced ? phaseRuns * 100 / phaseBallsFaced : null
              : phaseLegal ? phaseRuns * 6 / phaseLegal : null,
          }
        })
      })()
      : null
    return {
      balls,
      card,
      inning,
      overMap,
      progression,
      overs,
      runs,
      ballsFaced,
      fours,
      sixes,
      legalBalls,
      bowlerRuns,
      bowlerWickets,
      matchups: [...matchups.entries()],
      partnerships,
      dismissal,
      bestOver,
      toughestOver,
      phases,
      dots: batterBalls.filter((ball) => isLegal(ball) && ball.runs_total === 0).length,
      boundaryBalls: batterBalls.filter((ball) => ball.four || ball.six).length,
      bowlerDots: bowlerBalls.filter((ball) => isLegal(ball) && ball.runs_total === 0).length,
      bowlerBoundaries: bowlerBalls.filter((ball) => ball.four || ball.six).length,
      bowlerExtras: bowlerBalls.reduce((sum, ball) => sum + (ball.runs_extras ?? 0), 0),
      bowlerMaidens: ownOvers.filter((over) => over.legalBalls === 6 && over.runs === 0).length,
    }
  }, [player, props.deliveries, props.innings, props.matchFormat, props.scorecards])

  const team = teamFor(props.teams, analysis.card?.team_id ?? null)
  const maxProgression = Math.max(1, ...analysis.progression.map((point) => point.runs))

  return (
    <section className="panel player-detail">
      <div className="player-detail-heading">
        <button className="player-back" type="button" onClick={onBack}>← Back to Match Intelligence</button>
        <span className="section-kicker">INNINGS {player.innings} · {team?.name ?? 'TEAM UNAVAILABLE'} · DELIVERY-DERIVED</span>
        <h2>{player.name}</h2>
        <p>{player.role === 'batter' ? 'Batting analysis' : 'Bowling analysis'} · {analysis.balls.length ? `${analysis.balls.length} recorded deliveries in this innings` : 'No ball-by-ball records available'}</p>
      </div>
      {analysis.balls.length > 0 && (
        <NarrativePanel props={props} scope="player" innings={player.innings} player={player} />
      )}
      {!analysis.balls.length ? (
        <p className="analysis-muted">Detailed analysis is unavailable because this innings has no delivery records.</p>
      ) : player.role === 'batter' ? (
        <>
          <div className="player-detail-stats">
            <span>Runs <b>{analysis.runs}</b></span><span>Balls <b>{analysis.ballsFaced}</b></span>
            <span>Strike rate <b>{analysis.ballsFaced ? (analysis.runs * 100 / analysis.ballsFaced).toFixed(2) : '—'}</b></span>
            <span>4s / 6s <b>{analysis.fours} / {analysis.sixes}</b></span>
            <span>Dot balls <b>{analysis.dots} ({analysis.ballsFaced ? (analysis.dots * 100 / analysis.ballsFaced).toFixed(1) : '—'}%)</b></span>
            <span>Boundary balls <b>{analysis.boundaryBalls} ({analysis.ballsFaced ? (analysis.boundaryBalls * 100 / analysis.ballsFaced).toFixed(1) : '—'}%)</b></span>
          </div>
          <div className="player-detail-grid">
            <section><h3>Scoring progression</h3>
              <div className="player-progression">{analysis.progression.map((point) => (
                <span key={point.index} style={{ height: `${Math.max(3, point.runs / maxProgression * 100)}%` }} title={`Ball ${point.index}: ${point.runs} runs`} />
              ))}</div>
              <p className="analysis-muted">Cumulative runs off the bat by ball faced. Wide deliveries do not count as a ball faced.</p>
            </section>
            <section><h3>Over-by-over scoring</h3>
              {analysis.overs.length
                ? <div className="player-over-list">{analysis.overs.map((over) => <div key={over.index}>
                  <b>Over {over.index + 1}</b><span>{over.runs} runs · {over.balls} balls</span>
                  <small>{over.runs >= 10 ? 'High-scoring over' : over.balls >= 4 && over.runs <= 2 ? 'Low-scoring over' : '—'}</small>
                </div>)}</div>
                : <p className="analysis-muted">Over figures unavailable.</p>}
            </section>
            <section><h3>Phase scoring</h3>
              {analysis.phases
                ? <div className="player-over-list">{analysis.phases.map((phase) => <div key={phase.name}>
                  <b>{phase.name}</b><span>{phase.runs} runs · {phase.balls} balls</span><small>{phase.rate == null ? 'Rate unavailable' : `Innings rate ${phase.rate.toFixed(2)}`}</small>
                </div>)}</div>
                : <p className="analysis-muted">Phase definitions are unavailable for this match format.</p>}
            </section>
            <section><h3>Performance against bowlers</h3>
              <div className="player-over-list">{analysis.matchups.map(([key, row]) => (
                <div key={key}><b>{analysis.balls.find((ball) => String(ball.bowler_id ?? ball.bowler) === key)?.bowler ?? 'Bowler unavailable'}</b>
                  <span>{row.runs} runs · {row.balls} balls · {row.outs} dismissal(s)</span><small>{row.balls ? `SR ${(row.runs * 100 / row.balls).toFixed(1)}` : 'No balls faced'}</small>
                </div>
              ))}</div>
            </section>
            <section><h3>Partnership contribution</h3>
              {analysis.partnerships.length
                ? analysis.partnerships.map(({ number, batters, runs: partnershipRuns, contribution }) => (
                  <p key={number}>{batters.join(' & ')}: {contribution?.runs ?? 0} runs from {contribution?.balls ?? 0} balls in a {partnershipRuns}-run partnership.</p>
                ))
                : <p className="analysis-muted">Partnership records are unavailable.</p>}
            </section>
            <section><h3>Dismissal & assessment</h3>
              {analysis.dismissal
                ? <p>{analysis.dismissal.dismissed_batter ?? player.name} dismissed {analysis.dismissal.dismissal ?? 'type unavailable'} at delivery {analysis.dismissal.over_ball ?? 'unavailable'}{analysis.dismissal.bowler ? ` by ${analysis.dismissal.bowler}` : ''}.</p>
                : <p>Not out at this checkpoint, or dismissal details unavailable.</p>}
              <p>Observed: {analysis.runs} runs from {analysis.ballsFaced} balls, strike rate {analysis.ballsFaced ? (analysis.runs * 100 / analysis.ballsFaced).toFixed(1) : 'unavailable'}; {analysis.dots} legal dots and {analysis.boundaryBalls} boundary deliveries. No intent or shot-selection claim is inferred.</p>
              {analysis.balls.some((ball) => ball.is_noball) && <p className="analysis-muted">Bowling type and pace/spin classification are not supplied by this delivery data.</p>}
            </section>
          </div>
        </>
      ) : (
        <>
          <div className="player-detail-stats">
            <span>Overs <b>{Math.floor(analysis.legalBalls / 6)}.{analysis.legalBalls % 6}</b></span>
            <span>Maidens <b>{analysis.bowlerMaidens}</b></span><span>Runs conceded <b>{analysis.bowlerRuns}</b></span>
            <span>Wickets <b>{analysis.bowlerWickets}</b></span>
            <span>Economy <b>{analysis.legalBalls ? (analysis.bowlerRuns * 6 / analysis.legalBalls).toFixed(2) : '—'}</b></span>
            <span>Dots / boundaries <b>{analysis.bowlerDots} / {analysis.bowlerBoundaries}</b></span>
          </div>
          <div className="player-detail-grid">
            <section><h3>Over-by-over spell</h3>
              {analysis.overs.map((over) => <p key={over.index}>Over {over.index + 1}: {over.runs} bowler-conceded runs · {over.legalBalls} legal balls · {over.wickets} wicket(s) · {over.dots} dots · {over.boundaries} boundary balls.</p>)}
            </section>
            <section><h3>Against each batter</h3>
              <div className="player-over-list">{analysis.matchups.map(([key, row]) => (
                <div key={key}><b>{analysis.balls.find((ball) => String(ball.batter_id ?? ball.batter) === key)?.batter ?? 'Batter unavailable'}</b>
                  <span>{row.runs} bowler-conceded runs · {row.balls} legal balls · {row.outs} bowler-credit wicket(s)</span><small>{row.balls ? `Economy ${(row.runs * 6 / row.balls).toFixed(2)}` : 'Economy unavailable'}</small>
                </div>
              ))}</div>
            </section>
            <section><h3>Phase impact</h3>
              {analysis.phases
                ? analysis.phases.map((phase) => <p key={phase.name}>{phase.name}: {phase.rate == null ? 'No deliveries recorded' : `economy ${phase.rate.toFixed(2)} over ${phase.balls} legal balls`} · {phase.wickets} wicket(s).</p>)
                : <p className="analysis-muted">Phase definitions are unavailable for this match format.</p>}
            </section>
            <section><h3>Boundary & extras control</h3>
              <p>{analysis.bowlerBoundaries} boundary balls conceded; {analysis.bowlerExtras} total extras in this bowler's deliveries.</p>
              <p>Wicket impact ({analysis.bowlerWickets}) and economy ({analysis.legalBalls ? (analysis.bowlerRuns * 6 / analysis.legalBalls).toFixed(2) : 'unavailable'}) are shown separately.</p>
              <p className="analysis-muted">Pace/spin classification is unavailable unless supplied by the provider.</p>
            </section>
            <section><h3>Best & most expensive over</h3>
              <p>{analysis.bestOver ? `Most economical recorded over: ${analysis.bestOver.index + 1} (${analysis.bestOver.runs} runs, ${analysis.bestOver.legalBalls} legal balls).` : 'No over data.'}</p>
              <p>{analysis.toughestOver ? `Most expensive recorded over: ${analysis.toughestOver.index + 1} (${analysis.toughestOver.runs} runs, economy ${analysis.toughestOver.economy?.toFixed(2) ?? 'unavailable'}).` : 'No over data.'}</p>
            </section>
            <section><h3>Assessment</h3>
              <p>Observed: {analysis.bowlerRuns} bowler-conceded runs from {analysis.legalBalls} legal balls, {analysis.bowlerWickets} bowler-credit wicket(s), {analysis.bowlerDots} dots, {analysis.bowlerBoundaries} boundary balls, {analysis.bowlerExtras} extras.</p>
              <p>Wickets are not discounted because of a high economy; control and wicket impact remain separate evidence.</p>
            </section>
          </div>
        </>
      )}
    </section>
  )
}

export function TeamTacticalAnalysis(props: AnalysisProps & { mode: 'debate' | 'decision' }) {
  const report = useMemo(() => {
    const active = props.innings[props.innings.length - 1]
    const card = active && props.scorecards.find((item) => item.innings === String(active.innings))
    if (!active || !card) return null
    const battingTeam = teamFor(props.teams, active.team_id)
    const bowlingTeam = bowlingTeamFor(props.teams, active.team_id)
    const balls = props.deliveries.filter((ball) => String(ball.innings) === String(active.innings))
    const legalBalls = balls.filter(isLegal).length
    const runs = balls.reduce((total, ball) => total + ball.runs_total, 0)
    const wickets = balls.filter((ball) => ball.is_wicket).length
    const dots = balls.filter((ball) => isLegal(ball) && ball.runs_total === 0).length
    const boundaries = balls.filter((ball) => ball.four || ball.six).length
    const battingRows = batterReports(props, card, balls).sort((a, b) => b.runs - a.runs)
    const bowlingRows = makeBowlers(props).filter((bowler) => bowler.innings === String(active.innings))
    const wicketLeader = [...bowlingRows].filter((bowler) => bowler.legalBalls > 0)
      .sort((a, b) => b.wickets - a.wickets || (a.economy ?? Infinity) - (b.economy ?? Infinity))[0]
    const economyLeader = [...bowlingRows].filter((bowler) => bowler.legalBalls > 0)
      .sort((a, b) => (a.economy ?? Infinity) - (b.economy ?? Infinity))[0]
    const topBatter = battingRows[0]
    const first = props.scorecards[0]
    const target = first && card.innings !== first.innings && first.runs != null ? first.runs + 1 : null
    const requiredRuns = target == null ? null : Math.max(0, target - runs)
    const formatLimits = limits(props.matchFormat)
    const ballsRemaining = formatLimits && target != null
      ? Math.max(0, formatLimits.overs * 6 - legalBalls)
      : null
    const requiredRate = ballsRemaining && requiredRuns != null
      ? requiredRuns * 6 / ballsRemaining
      : null
    const currentRate = legalBalls ? runs * 6 / legalBalls : null
    const battingEvidence = [
      `${battingTeam?.name ?? 'Batting team'}: ${runs}/${wickets} from ${legalBalls} legal balls${currentRate == null ? '' : ` (RR ${currentRate.toFixed(2)})`}.`,
      `${dots} dot balls (${legalBalls ? (dots * 100 / legalBalls).toFixed(1) : '—'}% of legal balls); ${boundaries} boundary balls.`,
      topBatter ? `${topBatter.name}: ${topBatter.runs} from ${topBatter.balls}, SR ${topBatter.strikeRate?.toFixed(1) ?? '—'}.` : 'No batter sample is available.',
      ...(requiredRuns == null ? [] : [`Chase: ${requiredRuns} required from ${ballsRemaining ?? 'unknown'} legal balls${requiredRate == null ? '' : ` at ${requiredRate.toFixed(2)} per over`}.`]),
    ]
    const bowlingEvidence = [
      `${bowlingTeam?.name ?? 'Bowling team'} bowling in innings ${active.innings}.`,
      wicketLeader ? `${wicketLeader.name}: ${wicketLeader.wickets} bowler-credited wickets from ${wicketLeader.legalBalls} balls; Econ ${wicketLeader.economy?.toFixed(2) ?? '—'}.` : 'No bowler-credited wicket or delivery sample is established.',
      economyLeader ? `${economyLeader.name} has the lowest recorded economy in this innings: ${economyLeader.economy?.toFixed(2) ?? '—'} from ${economyLeader.legalBalls} legal balls.` : 'No bowling economy comparison is available.',
      `${dots} legal dot balls and ${boundaries} boundary balls recorded against the batting side.`,
    ]
    return {
      active,
      battingTeam,
      bowlingTeam,
      legalBalls,
      runs,
      wickets,
      dots,
      boundaries,
      battingEvidence,
      bowlingEvidence,
      wicketLeader,
      economyLeader,
      topBatter,
      requiredRuns,
      requiredRate,
      currentRate,
      confidence: legalBalls >= 24 ? 'Medium' : 'Low',
      isFinished: props.isFinished,
    }
  }, [props.teams, props.innings, props.scorecards, props.deliveries, props.matchFormat, props.isFinished])

  if (!report) return <section className="panel feature-panel"><p className="feature-muted">Team assessments require an innings and scorecard snapshot.</p></section>
  const sections = [
    {
      team: report.battingTeam,
      role: 'Batting',
      evidence: report.battingEvidence,
      aggressive: {
        title: 'Aggressive approach',
        action: report.topBatter
          ? `Consider using ${report.topBatter.name}'s recorded scoring rate as the attack reference, while preserving the other batter's role.`
          : 'Avoid a player-specific attack until a batter has a measured delivery sample.',
        benefit: 'Can convert observed scoring opportunities into a higher rate.',
        risk: 'A higher-variance choice can expose wickets; the current sample cannot predict the next delivery.',
        conditions: report.requiredRate != null && report.currentRate != null && report.requiredRate > report.currentRate
          ? `Only suitable if the chase rate demand (${report.requiredRate.toFixed(2)}) exceeds the current rate and enough wickets remain.`
          : 'May suit a period with wickets in hand and a demonstrated batter-scoring option.',
      },
      controlled: {
        title: 'Controlled approach',
        action: 'Prioritize strike rotation and protect the remaining batting resources before increasing boundary attempts.',
        benefit: 'Limits wicket exposure while retaining the existing scoring base.',
        risk: report.requiredRate == null ? 'A lower scoring rate can reduce options later in the innings.' : `The required rate is ${report.requiredRate.toFixed(2)}; excessive caution may increase the later demand.`,
        conditions: `${10 - report.wickets} wickets remain; use this option when that resource constraint outweighs immediate rate pressure.`,
      },
    },
    {
      team: report.bowlingTeam,
      role: 'Bowling',
      evidence: report.bowlingEvidence,
      aggressive: {
        title: 'Wicket-seeking approach',
        action: report.wicketLeader
          ? `Consider retaining ${report.wicketLeader.name} as a wicket-taking option, based on the recorded ${report.wicketLeader.wickets} bowler-credited wickets.`
          : 'No wicket-taking bowler is established; do not make a player-specific attack claim from this sample.',
        benefit: 'A wicket can reduce remaining batting resources.',
        risk: 'The current wickets-per-ball sample may not persist; an attacking option may also concede more runs.',
        conditions: report.wicketLeader ? `${report.wicketLeader.name} has the strongest observed wicket count in this innings.` : 'Wait for a measurable wicket-taking signal.',
      },
      controlled: {
        title: 'Run-control approach',
        action: report.economyLeader
          ? `Consider using ${report.economyLeader.name}'s recorded economy as a run-control reference rather than assuming it guarantees continued control.`
          : 'Compare bowlers only after delivery-backed figures are available.',
        benefit: 'Can limit runs while the bowling side waits for another scoring or dismissal signal.',
        risk: 'Economy alone does not measure wicket impact; a low rate can still leave the batting side in control.',
        conditions: 'Best considered when protecting the rate is more important than forcing a high-risk dismissal attempt.',
      },
    },
  ]

  return (
    <div className="team-tactical-report">
      <div className="feature-heading">
        <div><span className="section-kicker">{props.mode === 'debate' ? 'STRATEGIC DEBATE' : 'EXECUTIVE DECISION'} · BOTH TEAM PERSPECTIVES</span>
          <h2>{report.isFinished ? 'Retrospective evidence-based assessment' : `Innings ${report.active.innings}: ${report.battingTeam?.name ?? 'Batting team'} vs ${report.bowlingTeam?.name ?? 'Bowling team'}`}</h2>
        </div>
        <span className="data-scope">{report.isFinished ? 'FINAL SNAPSHOT' : 'PROVISIONAL · DATA-BASED, NOT TEAM INSTRUCTIONS'}</span>
      </div>
      {props.mode === 'debate' ? (
        <>
          <div className="team-strategy-grid">
            {sections.map((section) => [section.aggressive, section.controlled].map((approach) => (
              <article className="panel strategy-card team-strategy-card" key={`${section.role}-${approach.title}`}>
                <span className="section-kicker">{section.team?.name ?? `${section.role} team`} · {approach.title}</span>
                <h2>{approach.action}</h2>
                <p><b>Expected advantage:</b> {approach.benefit}</p>
                <p><b>Risk:</b> {approach.risk}</p>
                <p><b>When it may fit:</b> {approach.conditions}</p>
                <div className="strategy-detail"><b>Observed evidence</b>{section.evidence.map((item, index) => <p key={`${section.role}-${index}`}>{item}</p>)}</div>
              </article>
            )))}
          </div>
          <p className="analysis-method">Aggressive and controlled options are alternatives, not forecasts. Evidence is from the current innings snapshot; no field placement, pitch condition, or team instruction is inferred.</p>
        </>
      ) : (
        <div className="team-decision-grid">
          {sections.map((section) => {
            const evidenceLeader = section.role === 'Batting' ? report.topBatter?.name : report.wicketLeader?.name ?? report.economyLeader?.name
            const recommended = section.role === 'Batting'
              ? report.wickets >= 7 ? 'Protect the remaining wickets and prioritize lower-risk strike rotation.'
                : report.requiredRate != null && report.currentRate != null && report.requiredRate > report.currentRate
                  ? 'Increase scoring intent while using the observed batter evidence; preserve wicket options.'
                  : 'Maintain a measured scoring rate and avoid a player-specific instruction beyond the available sample.'
              : report.wicketLeader?.wickets
                ? `Retain ${report.wicketLeader.name} as a wicket-taking option; ${report.economyLeader?.name ?? 'another bowler'} provides the lower-economy comparison.`
                : report.economyLeader
                  ? `Use ${report.economyLeader.name}'s lower economy as a run-control reference; no wicket-taker is established yet.`
                  : 'Gather more delivery evidence before selecting a player-specific bowling option.'
            return (
              <article className="panel decision-panel team-decision-card" key={`decision-${section.role}`}>
                <span className="section-kicker">{section.team?.name ?? `${section.role} team`} · {section.role} perspective</span>
                <h2>{report.isFinished ? 'Retrospective assessment' : 'Recommended tactical option'}</h2>
                <p>{recommended}</p>
                <div className="decision-evidence"><b>Supporting match statistics</b>{section.evidence.map((item, index) => <p key={`${section.role}-decision-${index}`}>{item}</p>)}</div>
                <div className="insight-columns">
                  <div><span className="section-kicker">EXPECTED BENEFIT</span><p>{section.role === 'Batting' ? 'Balances scoring opportunities against the observed wicket position.' : 'Uses the measured wicket or economy signal without treating it as a guaranteed outcome.'}</p></div>
                  <div><span className="section-kicker">RISK</span><p>{section.role === 'Batting' ? 'A wicket or a short player sample may invalidate the current scoring read.' : 'Economy and wickets are different measures; the selected signal does not guarantee a dismissal or containment.'}</p></div>
                </div>
                <div className="decision-confidence"><b>Confidence: {report.confidence} · evidence quantity only</b><span>{evidenceLeader ? `Lead player evidence: ${evidenceLeader}. ` : ''}Based on {report.legalBalls} legal balls in the current innings; not a calibrated prediction.</span></div>
              </article>
            )
          })}
        </div>
      )}
    </div>
  )
}
