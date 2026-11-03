import json
import logging
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx
from app.core.config import settings

logger = logging.getLogger(__name__)


class SportmonksError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retryable: bool = False,
        retry_after_seconds: int | None = None,
        category: str = "provider_error",
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds
        self.category = category


def _retry_after(response: httpx.Response) -> int | None:
    value = response.headers.get("Retry-After")
    if not value:
        return None
    try:
        return max(1, int(value))
    except ValueError:
        try:
            target = parsedate_to_datetime(value)
            if target.tzinfo is None:
                target = target.replace(tzinfo=timezone.utc)
            return max(1, int((target - datetime.now(timezone.utc)).total_seconds()))
        except (TypeError, ValueError, OverflowError):
            return None


def _safe_provider_message(response: httpx.Response, token: str) -> str:
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    message = payload.get("message") or payload.get("error") if isinstance(payload, dict) else None
    if isinstance(message, dict):
        message = message.get("message") or message.get("detail")
    if isinstance(message, (dict, list)):
        message = json.dumps(message, ensure_ascii=True)
    text = str(message or response.reason_phrase or "No provider detail")
    text = text.replace(token, "[REDACTED]")
    text = re.sub(r"(?i)(api_token=)[^&\s]+", r"\1[REDACTED]", text)
    return re.sub(r"[\r\n\t]+", " ", text)[:300]


class SportmonksProvider:
    """Thin provider client. Keep provider-specific response handling out of routes."""

    def __init__(self) -> None:
        self.base_url = settings.sportmonks_base_url.rstrip("/")

    def _require_token(self) -> str:
        token = settings.sportmonks_api_token.strip()
        if not token:
            raise SportmonksError(
                "SPORTMONKS_API_TOKEN is not configured in the backend environment.",
                category="missing_configuration",
            )
        return token

    async def _get(self, path: str, params: dict | None = None) -> dict:
        token = self._require_token()
        query = {"api_token": token, **(params or {})}
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.get(f"{self.base_url}/{path.lstrip('/')}", params=query)
                if response.is_error:
                    status_code = response.status_code
                    provider_message = _safe_provider_message(response, token)
                    provider_message_lower = provider_message.casefold()
                    retry_after_seconds = _retry_after(response)
                    if status_code == 401:
                        category = "authentication"
                        message = (
                            "Sportmonks rejected the backend API token (HTTP 401). "
                            "Verify SPORTMONKS_API_TOKEN in backend/.env and check that the token is active."
                        )
                        retryable = False
                    elif status_code == 403:
                        category = "subscription"
                        message = (
                            "Sportmonks denied this endpoint for the configured subscription (HTTP 403). "
                            "Check that the account plan includes Cricket API live scores."
                        )
                        retryable = False
                    elif status_code == 429 or (
                        status_code == 400
                        and any(term in provider_message_lower for term in ("too many attempts", "rate limit"))
                    ):
                        category = "rate_limit"
                        message = f"Sportmonks rate limit reached (HTTP {status_code}); back off before retrying."
                        retryable = True
                    elif status_code >= 500:
                        category = "upstream_server"
                        message = f"Sportmonks is temporarily unavailable (HTTP {status_code})."
                        retryable = True
                    elif status_code == 404:
                        category = "not_found"
                        message = "The requested fixture was not found by Sportmonks (HTTP 404)."
                        retryable = False
                    else:
                        category = "request_rejected"
                        message = f"Sportmonks rejected the request (HTTP {status_code})."
                        retryable = False
                    logger.warning(
                        "Sportmonks response status=%s category=%s detail=%s",
                        status_code,
                        category,
                        provider_message,
                    )
                    raise SportmonksError(
                        message,
                        status_code=status_code,
                        retryable=retryable,
                        retry_after_seconds=retry_after_seconds,
                        category=category,
                    )
                payload = response.json()
        except httpx.TimeoutException:
            logger.warning("Sportmonks request timed out")
            raise SportmonksError("Sportmonks request timed out.", retryable=True, category="timeout") from None
        except httpx.RequestError:
            logger.warning("Unable to connect to Sportmonks")
            raise SportmonksError("Unable to connect to Sportmonks.", retryable=True, category="connection") from None
        except ValueError:
            logger.warning("Sportmonks returned invalid JSON")
            raise SportmonksError(
                "Sportmonks returned an invalid response.",
                retryable=True,
                category="invalid_response",
            ) from None
        if not isinstance(payload, dict):
            raise SportmonksError(
                "Unexpected response format from Sportmonks.",
                retryable=True,
                category="invalid_response",
            )
        return payload

    async def get_livescores(self) -> dict:
        return await self._get("livescores", {"include": "localteam,visitorteam,runs"})

    async def get_fixture(self, fixture_id: int) -> dict:
        return await self._get(
            f"fixtures/{fixture_id}",
            {
                "include": (
                    "localteam,visitorteam,runs,batting.batsman,bowling.bowler,"
                    "balls.batsman,balls.bowler,lineup,scoreboards"
                )
            },
        )

    async def get_replay_competitions(self) -> list[dict]:
        payload = await self._get("leagues", {"include": "season,seasons"})
        leagues = payload.get("data", [])
        if not isinstance(leagues, list):
            raise SportmonksError(
                "Sportmonks returned an invalid competition list.",
                retryable=True,
                category="invalid_response",
            )

        competitions = []
        for league in leagues:
            if not isinstance(league, dict) or league.get("id") is None:
                continue
            seasons = league.get("seasons", [])
            if not isinstance(seasons, list):
                seasons = []
            normalized_seasons = [
                {"id": season["id"], "name": str(season.get("name") or season["id"])}
                for season in seasons
                if isinstance(season, dict) and season.get("id") is not None
            ]
            current_season_id = league.get("season_id")
            competitions.append({
                "id": league["id"],
                "name": str(league.get("name") or "Competition unavailable"),
                "seasons": sorted(
                    normalized_seasons,
                    key=lambda season: (
                        str(season["id"]) != str(current_season_id),
                        -int(season["id"]),
                    ),
                ),
            })
        return competitions

    async def get_replay_fixtures(self, season_id: int) -> list[dict]:
        payload = await self._get(
            "fixtures",
            {
                "filter[season_id]": season_id,
                "filter[status]": "Finished",
                "include": (
                    "localteam,visitorteam,runs,batting.batsman,bowling.bowler,"
                    "balls.batsman,balls.bowler,lineup,scoreboards"
                ),
            },
        )
        fixtures = payload.get("data", [])
        if not isinstance(fixtures, list):
            raise SportmonksError(
                "Sportmonks returned an invalid fixture list.",
                retryable=True,
                category="invalid_response",
            )
        return [fixture for fixture in fixtures if isinstance(fixture, dict)]
