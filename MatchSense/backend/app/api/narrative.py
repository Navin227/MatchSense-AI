import logging
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.narrative_service import generate_narrative as create_narrative

router = APIRouter(prefix="/api/narrative", tags=["narrative"])
logger = logging.getLogger(__name__)


class NarrativeRequest(BaseModel):
    state: dict[str, Any]
    scope: Literal["innings", "player"]
    innings: str = Field(min_length=1, max_length=20)
    player_role: Literal["batter", "bowler"] | None = None
    player_id: int | str | None = None
    player_name: str | None = Field(default=None, max_length=120)


@router.post("")
async def generate_narrative(request: NarrativeRequest):
    deliveries = request.state.get("deliveries")
    if not isinstance(deliveries, list):
        raise HTTPException(status_code=422, detail="Narrative context must contain a delivery list.")
    delivery_index = request.state.get("delivery_index", len(deliveries))
    if not isinstance(delivery_index, int) or delivery_index < 0:
        raise HTTPException(status_code=422, detail="Narrative checkpoint is invalid.")
    if request.scope == "player" and (request.player_role is None or not request.player_name):
        raise HTTPException(status_code=422, detail="Player narratives require a role and player name.")
    checkpoint = min(delivery_index, len(deliveries))
    total_deliveries = request.state.get("total_deliveries")
    if not isinstance(total_deliveries, int) or total_deliveries < checkpoint:
        total_deliveries = checkpoint
    state = {
        **request.state,
        "delivery_index": checkpoint,
        "total_deliveries": total_deliveries,
        "narrative_mode": True,
    }
    if not state.get("context_mode"):
        state["context_mode"] = (
            "LIVE MATCH SNAPSHOT" if state.get("is_live") else "HISTORICAL REPLAY CHECKPOINT"
        )
    try:
        result = await create_narrative(
            state,
            request.innings,
            request.scope,
            request.player_role,
            request.player_name,
            request.player_id,
        )
        return {
            **result,
            "scope": request.scope,
            "innings": request.innings,
            "player_role": request.player_role,
            "player_id": request.player_id,
            "player_name": request.player_name,
            "delivery_index": state["delivery_index"],
            "is_finished": bool(result.get("facts", {}).get("complete")),
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Unable to generate %s narrative for innings %s", request.scope, request.innings)
        raise HTTPException(status_code=502, detail="Unable to prepare evidence-based match narrative.") from exc
