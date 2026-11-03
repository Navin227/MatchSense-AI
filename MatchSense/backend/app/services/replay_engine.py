import asyncio
import copy
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


REPLAY_SPEEDS = (1, 2, 5)


def _as_int(value: Any) -> int:
    try:
        return int(value) if value is not None else 0
    except (TypeError, ValueError):
        return 0


def _player_key(player_id: Any, name: str | None) -> str:
    return str(player_id) if player_id is not None else f"name:{name or 'Unknown player'}"


def _overs(legal_balls: int) -> str:
    return f"{legal_balls // 6}.{legal_balls % 6}"


class MatchStateEngine:
    """Provider-independent, delivery-by-delivery match state and analytics pipeline."""

    def __init__(self, normalized_fixture: dict[str, Any]) -> None:
        self.fixture = {
            "id": normalized_fixture.get("id"),
            "match_format": normalized_fixture.get("match_format"),
            "localteam": normalized_fixture.get("localteam"),
            "visitorteam": normalized_fixture.get("visitorteam"),
        }
        self.deliveries = list(normalized_fixture.get("deliveries") or [])
        self.batter_roster = list(normalized_fixture.get("batting") or [])
        self.bowler_roster = list(normalized_fixture.get("bowling") or [])
        self.reset()

    def reset(self) -> None:
        self.cursor = 0
        self.innings: dict[str, dict[str, Any]] = {}
        self.batters: dict[str, dict[str, Any]] = {}
        self.bowlers: dict[str, dict[str, Any]] = {}
        self.current_batter_keys: dict[str, list[str]] = {}
        self.current_bowler_key: str | None = None
        self.history: list[dict[str, Any]] = []
        self.momentum: dict[tuple[str, str], int] = {}

        for player in self.batter_roster:
            player_id, name = player.get("player_id"), player.get("name")
            key = _player_key(player_id, name)
            self.batters[key] = self._empty_batter(player_id, name)
        for player in self.bowler_roster:
            player_id, name = player.get("player_id"), player.get("name")
            key = _player_key(player_id, name)
            self.bowlers[key] = self._empty_bowler(player_id, name)

    @staticmethod
    def _empty_batter(player_id: Any, name: str | None) -> dict[str, Any]:
        return {
            "player_id": player_id,
            "name": name or "Unknown batter",
            "runs": 0,
            "balls": 0,
            "fours": 0,
            "sixes": 0,
            "strike_rate": 0.0,
            "is_current": False,
        }

    @staticmethod
    def _empty_bowler(player_id: Any, name: str | None) -> dict[str, Any]:
        return {
            "player_id": player_id,
            "name": name or "Unknown bowler",
            "overs": "0.0",
            "legal_balls": 0,
            "maidens": 0,
            "runs": 0,
            "wickets": 0,
            "economy": 0.0,
            "is_current": False,
        }

    def _get_innings(self, delivery: dict[str, Any]) -> dict[str, Any]:
        label = str(delivery.get("innings") or "1")
        if label not in self.innings:
            self.innings[label] = {
                "innings": label,
                "team_id": delivery.get("team_id"),
                "runs": 0,
                "wickets": 0,
                "legal_balls": 0,
                "overs": "0.0",
                "run_rate": 0.0,
            }
        return self.innings[label]

    def _apply_delivery(self, delivery: dict[str, Any]) -> dict[str, Any]:
        innings = self._get_innings(delivery)
        innings_key = str(innings["innings"])
        batter_id, batter_name = delivery.get("batter_id"), delivery.get("batter")
        bowler_id, bowler_name = delivery.get("bowler_id"), delivery.get("bowler")
        batter_key = _player_key(batter_id, batter_name)
        bowler_key = _player_key(bowler_id, bowler_name)

        batter = self.batters.setdefault(batter_key, self._empty_batter(batter_id, batter_name))
        bowler = self.bowlers.setdefault(bowler_key, self._empty_bowler(bowler_id, bowler_name))
        if batter_name and batter["name"] == "Unknown batter":
            batter["name"] = batter_name
        if bowler_name and bowler["name"] == "Unknown bowler":
            bowler["name"] = bowler_name
        batter_runs = _as_int(delivery.get("runs_batter"))
        team_runs = _as_int(delivery.get("runs_total"))
        bowler_runs = _as_int(delivery.get("runs_bowler", batter_runs))
        legal = bool(delivery.get("is_legal"))
        wicket = bool(delivery.get("is_wicket"))
        bowler_wicket = bool(delivery.get("bowler_wicket", wicket))

        innings["runs"] += team_runs
        if wicket:
            innings["wickets"] += 1
        if legal:
            innings["legal_balls"] += 1
        innings["overs"] = _overs(innings["legal_balls"])
        balls_bowled = innings["legal_balls"]
        innings["run_rate"] = round(innings["runs"] * 6 / balls_bowled, 2) if balls_bowled else 0.0

        batter["runs"] += batter_runs
        if not bool(delivery.get("is_wide")):
            batter["balls"] += 1
        batter["fours"] += int(bool(delivery.get("four")))
        batter["sixes"] += int(bool(delivery.get("six")))
        batter["strike_rate"] = round(batter["runs"] * 100 / batter["balls"], 2) if batter["balls"] else 0.0

        bowler["runs"] += bowler_runs
        if legal:
            bowler["legal_balls"] += 1
        if bowler_wicket:
            bowler["wickets"] += 1
        bowler["overs"] = _overs(bowler["legal_balls"])
        bowler["economy"] = (
            round(bowler["runs"] * 6 / bowler["legal_balls"], 2) if bowler["legal_balls"] else 0.0
        )

        current = self.current_batter_keys.setdefault(innings_key, [])
        if wicket:
            dismissed_id = delivery.get("dismissed_batter_id") or batter_id
            dismissed_key = _player_key(dismissed_id, batter_name)
            if dismissed_key in current:
                current.remove(dismissed_key)
            if batter_key != dismissed_key and batter_key not in current:
                current.append(batter_key)
        else:
            if batter_key in current:
                current.remove(batter_key)
            current.append(batter_key)
        del current[:-2]
        self.current_bowler_key = bowler_key

        over_match = str(delivery.get("over_ball") or "").split(".", 1)[0]
        if over_match.isdigit():
            over_number = int(over_match)
        else:
            over_number = max(0, (innings["legal_balls"] - 1) // 6)
        over_key = str(over_number)
        momentum_key = (innings_key, over_key)
        self.momentum[momentum_key] = self.momentum.get(momentum_key, 0) + team_runs

        updated_delivery = {
            **delivery,
            "innings_runs": innings["runs"],
            "innings_wickets": innings["wickets"],
            "innings_overs": innings["overs"],
            "run_rate": innings["run_rate"],
        }
        self.history.append(updated_delivery)
        return updated_delivery

    def _detect_events(self, delivery: dict[str, Any]) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        if delivery.get("is_wicket"):
            events.append({
                "event": "wicket",
                "label": "Wicket",
                "dismissal": delivery.get("dismissal"),
                "batter": delivery.get("dismissed_batter") or delivery.get("batter"),
                "over_ball": delivery.get("over_ball"),
            })
        if delivery.get("four") or delivery.get("six"):
            runs = 6 if delivery.get("six") else 4
            events.append({
                "event": "boundary",
                "label": f"{runs} runs",
                "runs": runs,
                "batter": delivery.get("batter"),
                "over_ball": delivery.get("over_ball"),
            })

        innings = self.innings[str(delivery.get("innings") or "1")]
        legal_balls = innings["legal_balls"]
        if delivery.get("is_legal") and legal_balls and legal_balls % 6 == 0:
            events.append({
                "event": "over_complete",
                "label": f"End of over {legal_balls // 6}",
                "over": legal_balls // 6,
                "over_ball": delivery.get("over_ball"),
            })
        return events

    def step(self) -> dict[str, Any] | None:
        if self.cursor >= len(self.deliveries):
            return None
        raw_delivery = self.deliveries[self.cursor]
        delivery = self._apply_delivery(raw_delivery)
        events = self._detect_events(delivery)
        self.cursor += 1
        return {
            "type": "delivery_update",
            "delivery_index": self.cursor,
            "total_deliveries": len(self.deliveries),
            "delivery": delivery,
            "detected_events": events,
            "state": self.snapshot(),
        }

    def seek(self, delivery_index: int) -> None:
        """Rebuild the deterministic state at a processed-delivery checkpoint."""
        self.reset()
        for _ in range(max(0, min(delivery_index, len(self.deliveries)))):
            self.step()

    def snapshot(self) -> dict[str, Any]:
        innings = list(self.innings.values())
        current_innings = innings[-1] if innings else None
        current_keys = self.current_batter_keys.get(str(current_innings["innings"]), []) if current_innings else []
        for key, batter in self.batters.items():
            batter["is_current"] = key in current_keys
        for key, bowler in self.bowlers.items():
            bowler["is_current"] = key == self.current_bowler_key

        batters = list(self.batters.values())
        bowlers = list(self.bowlers.values())
        momentum = [
            {"innings": innings_label, "over": over, "runs": runs}
            for (innings_label, over), runs in self.momentum.items()
        ]
        return copy.deepcopy({
            **self.fixture,
            "innings": innings,
            "batting": batters,
            "bowling": bowlers,
            "current_batters": [self.batters[key] for key in current_keys if key in self.batters],
            "current_bowler": self.bowlers.get(self.current_bowler_key) if self.current_bowler_key else None,
            "deliveries": list(self.history),
            "progression": [
                {
                    "innings": str(delivery.get("innings") or "1"),
                    "over_ball": str(delivery.get("over_ball") or index + 1),
                    "runs": delivery["innings_runs"],
                }
                for index, delivery in enumerate(self.history)
            ],
            "momentum": momentum,
            "current_score": current_innings,
            "delivery_index": self.cursor,
            "total_deliveries": len(self.deliveries),
        })


@dataclass
class ReplayRun:
    session_id: str
    engine: MatchStateEngine
    speed: int = 1
    status: str = "ready"
    subscribers: set[asyncio.Queue[dict[str, Any]]] = field(default_factory=set)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    worker: asyncio.Task[None] | None = None


class ReplayService:
    """Manage replay controls and broadcast state/events without provider dependencies."""

    def __init__(self, base_interval_seconds: float) -> None:
        self.base_interval_seconds = base_interval_seconds
        self.sessions: dict[str, ReplayRun] = {}

    def create(self, normalized_fixture: dict[str, Any]) -> dict[str, Any]:
        deliveries = normalized_fixture.get("deliveries")
        if not isinstance(deliveries, list) or not deliveries:
            raise ValueError("This fixture does not contain replayable ball-by-ball deliveries.")
        session_id = str(uuid4())
        run = ReplayRun(session_id=session_id, engine=MatchStateEngine(normalized_fixture))
        self.sessions[session_id] = run
        return self.snapshot(run)

    def get(self, session_id: str) -> ReplayRun | None:
        return self.sessions.get(session_id)

    def snapshot(self, run: ReplayRun) -> dict[str, Any]:
        return {
            "session_id": run.session_id,
            "mode": "HISTORICAL REPLAY / SIMULATED LIVE",
            "status": run.status,
            "speed": run.speed,
            "available_speeds": list(REPLAY_SPEEDS),
            "base_interval_seconds": self.base_interval_seconds,
            "state": run.engine.snapshot(),
        }

    async def subscribe(self, run: ReplayRun) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        run.subscribers.add(queue)
        await queue.put({"type": "replay_state", **self.snapshot(run)})
        return queue

    def unsubscribe(self, run: ReplayRun, queue: asyncio.Queue[dict[str, Any]]) -> None:
        run.subscribers.discard(queue)

    async def publish(self, run: ReplayRun, event: dict[str, Any]) -> None:
        for queue in tuple(run.subscribers):
            await queue.put(event)

    async def command(self, run: ReplayRun, message: dict[str, Any]) -> None:
        action = message.get("action")
        async with run.lock:
            if action == "set_speed":
                speed = message.get("speed")
                if type(speed) is not int or speed not in REPLAY_SPEEDS:
                    await self.publish(run, {"type": "replay_error", "message": "Replay speed must be 1x, 2x, or 5x."})
                    return
                run.speed = speed
            elif action == "play":
                if run.status != "ready":
                    await self.publish(run, {"type": "replay_error", "message": "Reset the replay before playing it again."})
                    return
                run.status = "playing"
                self._ensure_worker(run)
            elif action == "pause":
                if run.status == "playing":
                    run.status = "paused"
            elif action == "resume":
                if run.status != "paused":
                    await self.publish(run, {"type": "replay_error", "message": "Only a paused replay can be resumed."})
                    return
                run.status = "playing"
                self._ensure_worker(run)
            elif action == "reset":
                run.status = "ready"
                run.engine.reset()
            elif action == "seek":
                delivery_index = message.get("delivery_index")
                if type(delivery_index) is not int or not 0 <= delivery_index <= len(run.engine.deliveries):
                    await self.publish(run, {
                        "type": "replay_error",
                        "message": "Replay checkpoint must be a delivery index within the fixture.",
                    })
                    return
                run.status = "paused" if delivery_index else "ready"
                run.engine.seek(delivery_index)
                if delivery_index == len(run.engine.deliveries) and delivery_index:
                    run.status = "finished"
            else:
                await self.publish(run, {"type": "replay_error", "message": "Unknown replay command."})
                return

        await self.publish(run, {"type": "replay_state", **self.snapshot(run)})

    def _ensure_worker(self, run: ReplayRun) -> None:
        if run.worker is None or run.worker.done():
            run.worker = asyncio.create_task(self._play(run))

    async def _play(self, run: ReplayRun) -> None:
        while True:
            await asyncio.sleep(self.base_interval_seconds / run.speed)
            async with run.lock:
                if run.status != "playing":
                    return
                update = run.engine.step()
                if update is None:
                    run.status = "finished"
                    completed = True
                    events: list[dict[str, Any]] = []
                else:
                    update["session_id"] = run.session_id
                    update["mode"] = "HISTORICAL REPLAY / SIMULATED LIVE"
                    update["speed"] = run.speed
                    completed = run.engine.cursor >= len(run.engine.deliveries)
                    if completed:
                        run.status = "finished"
                    update["status"] = run.status
                    events = update.pop("detected_events")

            if update is not None:
                await self.publish(run, update)
                for event in events:
                    await self.publish(run, {
                        "type": "match_event",
                        "session_id": run.session_id,
                        "mode": "HISTORICAL REPLAY / SIMULATED LIVE",
                        **event,
                    })
            if completed:
                await self.publish(run, {"type": "replay_state", **self.snapshot(run)})
                await self.publish(run, {"type": "replay_complete", **self.snapshot(run)})
                return
