import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from app.core.config import settings
from app.services.match_normalizer import normalize_fixture
from app.services.providers.sportmonks import SportmonksError, SportmonksProvider
from app.services.replay_engine import ReplayService

router = APIRouter(prefix="/api/replay", tags=["replay"])
provider = SportmonksProvider()
replay_service = ReplayService(settings.replay_base_interval_seconds)
logger = logging.getLogger(__name__)


class ReplayRequest(BaseModel):
    fixture_id: int = Field(gt=0)


def _entity(value):
    if not isinstance(value, dict):
        return {}
    nested = value.get("data")
    return nested if isinstance(nested, dict) else value


def _items(value):
    if isinstance(value, dict):
        value = value.get("data")
    return value if isinstance(value, list) else []


def _fixture_library_entry(fixture: dict[str, Any]) -> dict[str, Any]:
    localteam = _entity(fixture.get("localteam"))
    visitorteam = _entity(fixture.get("visitorteam"))
    teams = {
        str(team["id"]): team.get("name") or team.get("fullname") or "Team unavailable"
        for team in (localteam, visitorteam)
        if team.get("id") is not None
    }
    innings = []
    for row in _items(fixture.get("runs")):
        team_id = row.get("team_id")
        innings.append({
            "team": teams.get(str(team_id), "Team unavailable"),
            "runs": row.get("score"),
            "wickets": row.get("wickets"),
            "overs": row.get("overs"),
        })
    deliveries = _items(fixture.get("balls"))
    result = fixture.get("note") or fixture.get("result")
    validation = normalize_fixture({"data": fixture}).get("validation", {})
    return {
        "fixture_id": fixture.get("id"),
        "starting_at": fixture.get("starting_at"),
        "localteam": localteam.get("name") or localteam.get("fullname") or "Team unavailable",
        "visitorteam": visitorteam.get("name") or visitorteam.get("fullname") or "Team unavailable",
        "status": fixture.get("status"),
        "result": result if isinstance(result, str) and result.strip() else None,
        "innings": innings,
        "delivery_count": len(deliveries),
        "replay_available": bool(deliveries),
        "data_quality_status": validation.get("status", "unavailable"),
        "data_quality_issues": [
            issue.get("message", "Data validation warning")
            for issue in validation.get("issues", [])
            if isinstance(issue, dict)
        ],
    }


@router.get("/library")
async def replay_library():
    try:
        return {
            "source": "Sportmonks",
            "competitions": await provider.get_replay_competitions(),
        }
    except SportmonksError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Unexpected error loading the Sportmonks replay library")
        raise HTTPException(status_code=502, detail="Unable to load the replay library.") from exc


@router.get("/library/matches")
async def replay_library_matches(
    league_id: int = Query(gt=0),
    season_id: int = Query(gt=0),
):
    try:
        competitions = await provider.get_replay_competitions()
        competition = next(
            (item for item in competitions if str(item["id"]) == str(league_id)),
            None,
        )
        if competition is None or not any(
            str(season["id"]) == str(season_id) for season in competition["seasons"]
        ):
            raise HTTPException(status_code=404, detail="Competition season is not available from Sportmonks.")

        fixtures = await provider.get_replay_fixtures(season_id)
        matches = []
        for fixture in fixtures:
            status = fixture.get("status")
            if isinstance(status, dict):
                status = status.get("name") or status.get("short_name")
            if str(status).casefold() not in {"finished", "complete", "completed"}:
                continue
            matches.append(_fixture_library_entry(fixture))
        matches.sort(key=lambda match: match["starting_at"] or "", reverse=True)
        season = next(
            season["name"]
            for season in competition["seasons"]
            if str(season["id"]) == str(season_id)
        )
        return {
            "source": "Sportmonks",
            "competition": competition["name"],
            "season": season,
            "matches": matches,
        }
    except HTTPException:
        raise
    except SportmonksError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Unexpected error loading replay fixtures for season %s", season_id)
        raise HTTPException(status_code=502, detail="Unable to load completed replay fixtures.") from exc


@router.post("")
async def create_replay(request: ReplayRequest):
    try:
        payload = await provider.get_fixture(request.fixture_id)
        match = normalize_fixture(payload)
        if not match.get("is_finished"):
            raise ValueError("Only completed Sportmonks fixtures can be replayed.")
        if not match.get("deliveries"):
            raise ValueError("This completed fixture has no ball-by-ball delivery coverage.")
        snapshot = replay_service.create(match)
        return {"type": "replay_state", **snapshot}
    except SportmonksError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Unexpected error creating fixture %s replay", request.fixture_id)
        raise HTTPException(status_code=502, detail="Unable to prepare the historical replay.") from exc


@router.get("/{session_id}")
async def get_replay(session_id: str):
    run = replay_service.get(session_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Replay session not found.")
    return {"type": "replay_state", **replay_service.snapshot(run)}


@router.websocket("/{session_id}/ws")
async def replay_events(websocket: WebSocket, session_id: str):
    run = replay_service.get(session_id)
    if run is None:
        await websocket.close(code=4404, reason="Replay session not found.")
        return

    await websocket.accept()
    queue = await replay_service.subscribe(run)

    async def send_events() -> None:
        while True:
            await websocket.send_json(await queue.get())

    async def receive_commands() -> None:
        while True:
            message = await websocket.receive_json()
            if not isinstance(message, dict):
                await replay_service.publish(run, {"type": "replay_error", "message": "Replay commands must be JSON objects."})
                continue
            await replay_service.command(run, message)

    sender = asyncio.create_task(send_events())
    receiver = asyncio.create_task(receive_commands())
    try:
        done, pending = await asyncio.wait(
            {sender, receiver},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        for task in done:
            task.result()
    except WebSocketDisconnect:
        pass
    finally:
        sender.cancel()
        receiver.cancel()
        replay_service.unsubscribe(run, queue)
