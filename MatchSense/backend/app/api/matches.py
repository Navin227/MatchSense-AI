import logging
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.core.config import settings
from app.services.providers.sportmonks import SportmonksProvider, SportmonksError
from app.services.match_normalizer import normalize_fixture, normalize_live_matches
from app.services.match_intelligence_service import build_intelligence

router = APIRouter(prefix="/api/matches", tags=["matches"])
provider = SportmonksProvider()
logger = logging.getLogger(__name__)


class TeamSummary(BaseModel):
    id: int | str | None = None
    name: str
    short_name: str | None = None
    image_path: str | None = None


class ScoreSummary(BaseModel):
    runs: int | None = None
    wickets: int | None = None
    overs: str | None = None
    run_rate: float | None = None


class LiveMatch(BaseModel):
    id: int | str | None = None
    status: str | None = None
    note: str | None = None
    localteam: TeamSummary | None = None
    visitorteam: TeamSummary | None = None
    score: ScoreSummary
    localteam_score: ScoreSummary | None = None
    visitorteam_score: ScoreSummary | None = None


class LiveMatchesResponse(BaseModel):
    matches: list[LiveMatch]
    poll_interval_seconds: int
    updated_at: datetime


class InningsAnalytics(BaseModel):
    id: int | str | None = None
    team_id: int | str | None = None
    innings: int | str
    runs: int | None = None
    wickets: int | None = None
    legal_balls: int | None = None
    overs: str | None = None
    run_rate: float | None = None


class ScorecardBatter(BaseModel):
    player_id: int | str | None = None
    name: str
    dismissal: str | None = None
    runs: int | None = None
    balls: int | None = None
    fours: int | None = None
    sixes: int | None = None
    strike_rate: float | None = None
    is_current: bool = False
    current_role: str | None = None


class ScorecardBowler(BaseModel):
    player_id: int | str | None = None
    name: str
    legal_balls: int | None = None
    overs: str | None = None
    maidens: int | None = None
    runs: int | None = None
    wickets: int | None = None
    economy: float | None = None
    wides: int | None = None
    no_balls: int | None = None


class ScorecardExtras(BaseModel):
    wides: int | None = None
    no_balls: int | None = None
    byes: int | None = None
    leg_byes: int | None = None
    penalty: int | None = None


class FallOfWicket(BaseModel):
    wicket: int
    score: int
    over: str | None = None
    batter: str | None = None
    batter_id: int | str | None = None
    dismissal: str | None = None
    fielder_id: int | str | None = None
    fielder: str | None = None
    bowler_id: int | str | None = None
    bowler: str | None = None
    runout_by_id: int | str | None = None
    runout_by: str | None = None


class BattingPartnership(BaseModel):
    number: int
    wicket: int | None = None
    completed: bool = True
    current: bool = False
    batters: list[str]
    runs: int
    balls: int
    contributions: list[dict[str, int | str | None]]


class PowerplaySummary(BaseModel):
    label: str
    overs: str | None = None
    boundary_legal_balls: int | None = None
    runs: int
    wickets: int
    legal_balls: int
    run_rate: float | None = None
    complete: bool


class InningsScorecard(BaseModel):
    id: int | str | None = None
    team_id: int | str | None = None
    innings: str
    runs: int | None = None
    wickets: int | None = None
    legal_balls: int | None = None
    overs: str | None = None
    run_rate: float | None = None
    extras: ScorecardExtras
    batting: list[ScorecardBatter]
    bowling: list[ScorecardBowler]
    fall_of_wickets: list[FallOfWicket]
    did_not_bat: list[dict[str, int | str | None]]
    partnership: BattingPartnership | None = None
    partnerships: list[BattingPartnership]
    powerplay: PowerplaySummary | None = None


class BattingAnalytics(BaseModel):
    player_id: int | str | None = None
    name: str
    runs: int | None = None
    balls: int | None = None
    fours: int | None = None
    sixes: int | None = None
    strike_rate: float | None = None
    is_current: bool
    current_role: str | None = None


class BowlingAnalytics(BaseModel):
    player_id: int | str | None = None
    name: str
    overs: str | None = None
    maidens: int | None = None
    runs: int | None = None
    wickets: int | None = None
    economy: float | None = None
    is_current: bool


class Delivery(BaseModel):
    source_id: int | str
    over_ball: str | None = None
    innings: str
    team_id: int | str | None = None
    batter_id: int | str | None = None
    non_striker_id: int | str | None = None
    bowler_id: int | str | None = None
    batter: str | None = None
    non_striker: str | None = None
    bowler: str | None = None
    dismissed_batter_id: int | str | None = None
    dismissed_batter: str | None = None
    runs_batter: int | None = None
    runs_total: int
    runs_extras: int = 0
    runs_bowler: int | None = None
    innings_runs: int
    four: bool
    six: bool
    is_wicket: bool
    bowler_wicket: bool = False
    is_retired_hurt: bool = False
    is_legal: int | bool | None = None
    dismissal: str | None = None
    fielder_id: int | str | None = None
    fielder: str | None = None
    runout_by_id: int | str | None = None
    runout_by: str | None = None
    is_wide: bool = False
    is_noball: bool = False
    extras: ScorecardExtras | None = None


class ValidationIssue(BaseModel):
    code: str
    message: str


class MatchValidation(BaseModel):
    status: Literal["ok", "warning"]
    issues: list[ValidationIssue]
    target: int | None = None
    required_runs: int | None = None
    required_balls: int | None = None
    required_run_rate: float | None = None
    current_run_rate: float | None = None


class ProgressionPoint(BaseModel):
    innings: str
    over_ball: str
    runs: int


class MomentumPoint(BaseModel):
    innings: str
    over: str
    runs: int


class MatchAnalyticsResponse(BaseModel):
    id: int | str | None = None
    match_format: str | None = None
    status: str | None = None
    note: str | None = None
    is_finished: bool
    localteam: TeamSummary | None = None
    visitorteam: TeamSummary | None = None
    innings: list[InningsAnalytics]
    scorecards: list[InningsScorecard]
    validation: MatchValidation
    batting: list[BattingAnalytics]
    bowling: list[BowlingAnalytics]
    current_batters: list[BattingAnalytics]
    current_bowler: BowlingAnalytics | None = None
    deliveries: list[Delivery]
    progression: list[ProgressionPoint]
    momentum: list[MomentumPoint]
    intelligence: dict[str, Any]
    poll_interval_seconds: int
    updated_at: datetime


def _provider_error_response(exc: SportmonksError) -> JSONResponse:
    status = 503
    if exc.category == "rate_limit":
        status = 429
    elif exc.category == "timeout":
        status = 504
    elif exc.status_code == 404:
        status = 404
    elif exc.status_code is not None:
        status = 502
    return JSONResponse(
        status_code=status,
        content={
            "detail": str(exc),
            "category": exc.category,
            "retryable": exc.retryable,
            "retry_after_seconds": exc.retry_after_seconds,
        },
        headers=(
            {"Retry-After": str(exc.retry_after_seconds)}
            if exc.retry_after_seconds is not None
            else None
        ),
    )


def _live_intelligence_state(match: dict) -> dict:
    deliveries = match["deliveries"]
    scorecards = match["scorecards"]
    current_innings = match["innings"][-1] if match["innings"] else None
    current_card = next(
        (card for card in scorecards if current_innings and str(card["innings"]) == str(current_innings["innings"])),
        None,
    )
    completed_scorecards = [
        card for card in scorecards
        if match["is_finished"]
        or current_innings is None
        or str(card["innings"]) != str(current_innings["innings"])
    ]
    return {
        **match,
        "delivery_index": len(deliveries),
        "total_deliveries": len(deliveries) if match["is_finished"] else len(deliveries) + 1,
        "batting": current_card["batting"] if current_card else [],
        "bowling": [
            player
            for card in scorecards
            for player in card["bowling"]
        ],
        "current_batters": match["current_batters"],
        "current_bowler": match["current_bowler"],
        "completed_scorecards": completed_scorecards,
        "context_mode": "LIVE MATCH SNAPSHOT",
    }


async def _get_normalized_live_match(fixture_id: int) -> dict:
    payload = await provider.get_fixture(fixture_id)
    match = normalize_fixture(payload)
    if match["id"] is None:
        raise HTTPException(status_code=404, detail=f"Match {fixture_id} was not found.")
    return match


@router.get("/live", response_model=LiveMatchesResponse)
async def live_matches():
    try:
        payload = await provider.get_livescores()
        return {
            "matches": normalize_live_matches(payload),
            "poll_interval_seconds": settings.poll_interval_seconds,
            "updated_at": datetime.now(timezone.utc),
        }
    except SportmonksError as exc:
        return _provider_error_response(exc)
    except Exception as exc:
        logger.exception("Unexpected error retrieving live matches")
        raise HTTPException(status_code=502, detail="Unable to retrieve live matches from Sportmonks.") from exc


@router.get("/{fixture_id}", response_model=MatchAnalyticsResponse)
async def get_match(fixture_id: int):
    try:
        normalized = await _get_normalized_live_match(fixture_id)
        calculated = build_intelligence(_live_intelligence_state(normalized))
        return {
            **normalized,
            "intelligence": {
                "situation": calculated["situation"],
                "win_probability": calculated["win_probability"],
            },
            "poll_interval_seconds": settings.poll_interval_seconds,
            "updated_at": datetime.now(timezone.utc),
        }
    except HTTPException:
        raise
    except SportmonksError as exc:
        return _provider_error_response(exc)
    except Exception as exc:
        logger.exception("Unexpected error retrieving match %s", fixture_id)
        raise HTTPException(status_code=502, detail="Unable to retrieve match analytics from Sportmonks.") from exc
