import asyncio
import json
import logging
import re
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.config import settings
from app.services.match_chat_service import (
    _parse_provider_content,
    _providers_to_try,
    _provider_config,
    _validate_narrative_entities,
)
from app.services.match_context_builder import build_match_context

logger = logging.getLogger(__name__)


class NarrativeSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=100)
    paragraphs: list[str] = Field(min_length=1, max_length=12)


class NarrativeOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sections: list[NarrativeSection] = Field(min_length=1, max_length=10)


_PHASES = {
    "t10": (("Powerplay", 1, 3), ("Middle overs", 4, 7), ("Death overs", 8, 10)),
    "t20": (("Powerplay", 1, 6), ("Middle overs", 7, 15), ("Death overs", 16, 20)),
    "odi": (("Powerplay", 1, 10), ("Middle overs", 11, 40), ("Death overs", 41, 50)),
}
_SECTION_TITLES = (
    "Overall innings story",
    "Powerplay story",
    "Middle-over story",
    "Death-over story",
    "Batter-by-batter contributions",
    "Bowler-by-bowler performances",
    "Major partnerships",
    "Turning points",
    "Final assessment",
)


def _name(team: Any) -> str:
    return str(team.get("name") or "Unknown team") if isinstance(team, dict) else "Unknown team"


def _rate(runs: int, balls: int) -> float:
    return round(runs * 100 / balls, 1) if balls else 0.0


def _crease_phrase(batters: list[str]) -> str:
    names = list(dict.fromkeys(name for name in batters if name))
    if len(names) >= 2:
        return f"{names[0]} and {names[1]} at the crease"
    if names:
        return f"{names[0]} on strike"
    return ""


def _join_names(names: list[str]) -> str:
    if len(names) < 2:
        return names[0] if names else ""
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return f"{', '.join(names[:-1])} and {names[-1]}"


def _quantity(value: int, singular: str, plural: str | None = None) -> str:
    return f"{value} {singular if value == 1 else plural or singular + 's'}"


def _over_key(delivery: dict[str, Any]) -> str:
    return str(delivery.get("over") or "1")


def _canonical_innings_timeline(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def ball_order(row: dict[str, Any]) -> tuple[int, int] | None:
        match = re.fullmatch(r"(\d+)\.(\d+)", str(row.get("over_ball") or ""))
        return (int(match.group(1)), int(match.group(2))) if match else None

    if all(ball_order(row) is not None for row in rows):
        ordered = sorted(
            rows,
            key=lambda row: (*ball_order(row), row.get("index", 0)),
        )
    else:
        ordered = sorted(rows, key=lambda row: row.get("index", 0))

    previous_runs = previous_wickets = previous_legal_balls = 0
    previous_ball: tuple[int, int] | None = None
    timeline = []
    for event_order, source_row in enumerate(ordered, 1):
        row = {**source_row, "event_order": event_order}
        current_ball = ball_order(row)
        if current_ball is not None:
            row["over"] = str(current_ball[0] + 1)
        row["score_before"] = previous_runs
        row["wickets_before"] = previous_wickets
        row["legal_balls_before"] = previous_legal_balls
        row["score_after"] = previous_runs + int(row["total_runs"])
        row["wickets_after"] = previous_wickets + int(row["wicket"])
        row["legal_balls_after"] = previous_legal_balls + int(row["is_legal"])
        if (
            int(row["total_runs"]) < 0
            or int(row["wicket"]) not in (0, 1)
            or int(row["is_legal"]) not in (0, 1)
            or (current_ball is not None and previous_ball is not None and current_ball < previous_ball)
        ):
            raise ValueError(
                f"Delivery timeline is inconsistent at event {event_order} "
                f"({row.get('over_ball') or 'delivery label unavailable'})."
            )
        previous_runs = row["score_after"]
        previous_wickets = row["wickets_after"]
        previous_legal_balls = row["legal_balls_after"]
        if current_ball is not None:
            previous_ball = current_ball
        timeline.append(row)
    return timeline


def _ball_event(row: dict[str, Any]) -> str:
    source_label = str(row.get("over_ball") or "")
    match = re.fullmatch(r"(\d+)\.(\d+)", source_label)
    if match:
        ball = int(match.group(2))
        ball_name = {1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth", 6: "sixth"}.get(
            ball, f"{ball}th"
        )
        return f"{source_label} ({ball_name} ball of over {row['over']})"
    return f"over {row['over']}, delivery {row['event_order']}"


def _phase_ranges(match_format: Any) -> tuple[tuple[str, int, int], ...]:
    value = str(match_format or "").casefold()
    if "t10" in value or "ten10" in value:
        return _PHASES["t10"]
    if "t20" in value or "twenty20" in value:
        return _PHASES["t20"]
    if "odi" in value or "one day" in value:
        return _PHASES["odi"]
    return ()


def _innings_limit(match_format: Any) -> int | None:
    value = str(match_format or "").casefold()
    if "t10" in value or "ten10" in value:
        return 10
    if "t20" in value or "twenty20" in value:
        return 20
    if "odi" in value or "one day" in value:
        return 50
    return None


def _delivery_facts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "event_order": row["event_order"],
            "ball": _ball_event(row),
            "over": row["over"],
            "batter": row.get("batter") or "Batter unavailable",
            "non_striker": row.get("non_striker"),
            "bowler": row.get("bowler") or "Bowler unavailable",
            "runs": row["total_runs"],
            "bat_runs": row["batter_runs"],
            "legal": row["is_legal"],
            "extra": row["extras"],
            "boundary": 6 if row["six"] else 4 if row["four"] else 0,
            "wicket": (
                {
                    "batter": row["dismissed_batter"],
                    "dismissal": row["dismissal"],
                    "bowler_credit": row["bowler_wicket"],
                }
                if row["wicket"]
                else None
            ),
            "score_before": f"{row['score_before']}/{row['wickets_before']}",
            "score": f"{row['score_after']}/{row['wickets_after']}",
        }
        for row in rows
    ]


def _make_partnerships(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    stands: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    def start(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "start_score": f"{row['score_before']}/{row['wickets_before']}",
            "start_event_order": row["event_order"],
            "start_over": int(row["over"]),
            "start_ball": _ball_event(row),
            "end_over": int(row["over"]),
            "end_ball": _ball_event(row),
            "runs": 0,
            "balls": 0,
            "batter_runs": {},
            "batter_balls": {},
            "opening_batter_stats": {},
            "opening_balls": 0,
            "batters": [],
            "over_runs": {},
            "end_wicket": None,
        }

    for row in rows:
        if current is None:
            current = start(row)
        current["runs"] += row["total_runs"]
        current["balls"] += int(row["batter_faced_ball"])
        current["end_over"] = int(row["over"])
        current["end_ball"] = _ball_event(row)
        current["end_event_order"] = row["event_order"]
        batter = row.get("batter") or "Batter unavailable"
        current["batter_runs"][batter] = current["batter_runs"].get(batter, 0) + row["batter_runs"]
        current["batter_balls"][batter] = current["batter_balls"].get(batter, 0) + int(row["batter_faced_ball"])
        if row["batter_faced_ball"] and current["opening_balls"] < 6:
            opening = current["opening_batter_stats"].setdefault(batter, {"runs": 0, "balls": 0})
            opening["runs"] += row["batter_runs"]
            opening["balls"] += 1
            current["opening_balls"] += 1
        participants = [batter, row.get("non_striker")]
        for participant in participants:
            if participant and participant not in current["batters"]:
                current["batters"].append(participant)
        over = _over_key(row)
        current["over_runs"][over] = current["over_runs"].get(over, 0) + row["total_runs"]
        if row["wicket"]:
            current["end_wicket"] = {
                "batter": row["dismissed_batter"],
                "bowler": row.get("bowler"),
                "bowler_credit": row["bowler_wicket"],
                "over": int(row["over"]),
                "ball": _ball_event(row),
                "dismissal": row["dismissal"],
                "score_before": f"{row['score_before']}/{row['wickets_before']}",
                "score": f"{row['score_after']}/{row['wickets_after']}",
            }
            stands.append(current)
            current = None
    if current:
        current["ongoing"] = True
        stands.append(current)
    for stand in stands:
        batter_runs_total = sum(stand["batter_runs"].values())
        if batter_runs_total > stand["runs"]:
            raise ValueError("Partnership batter contributions exceed the stand total.")
        stand["extras"] = stand["runs"] - batter_runs_total
        stand["opening_balls_faced"] = stand["opening_balls"]
        stand["contributions"] = [
            {
                "batter": name,
                "runs": runs,
                "balls": stand["batter_balls"].get(name, 0),
                "strike_rate": _rate(runs, stand["batter_balls"].get(name, 0)),
            }
            for name, runs in stand["batter_runs"].items()
        ]
        stand["faster_batter"] = (
            max(
                stand["contributions"],
                key=lambda item: (item["strike_rate"], item["runs"]),
            )["batter"]
            if stand["contributions"]
            else None
        )
        stand["opening_contributions"] = [
            {"batter": name, **stats, "strike_rate": _rate(stats["runs"], stats["balls"])}
            for name, stats in stand["opening_batter_stats"].items()
        ]
        stand["opening_faster_batter"] = max(
            stand["opening_contributions"],
            key=lambda item: (item["strike_rate"], item["runs"]),
            default={},
        ).get("batter")
        del stand["batter_runs"]
        del stand["batter_balls"]
        del stand["opening_batter_stats"]
        del stand["opening_balls"]
        stand["over_runs"] = [
            {"over": over, "runs": runs}
            for over, runs in stand["over_runs"].items()
        ]
    return stands


def _batter_facts(rows: list[dict[str, Any]], innings_complete: bool) -> list[dict[str, Any]]:
    batters: dict[str, dict[str, Any]] = {}
    for row in rows:
        non_striker = row.get("non_striker")
        if non_striker:
            batters.setdefault(non_striker, {
                "name": non_striker,
                "entry_score": f"{row['score_before']}/{row['wickets_before']}",
                "entry_over": row["over"],
                "first_delivery_index": row["index"],
                "runs": 0,
                "balls": 0,
                "boundaries": 0,
                "overs": {},
                "bowlers": {},
                "progression": [],
                "progression_events": [],
                "dismissal": None,
                "end_score": None,
                "entry_wickets": row["wickets_before"],
            })
        name = row.get("batter") or "Batter unavailable"
        batter = batters.setdefault(name, {
            "name": name,
            "entry_score": f"{row['score_before']}/{row['wickets_before']}",
            "entry_over": row["over"],
            "first_delivery_index": row["index"],
            "runs": 0,
            "balls": 0,
            "boundaries": 0,
            "overs": {},
            "bowlers": {},
            "progression": [],
            "progression_events": [],
            "dismissal": None,
            "end_score": None,
            "entry_wickets": row["wickets_before"],
        })
        batter["runs"] += row["batter_runs"]
        batter["balls"] += int(row["batter_faced_ball"])
        batter["boundaries"] += int(row["four"] or row["six"])
        over = _over_key(row)
        over_facts = batter["overs"].setdefault(over, {
            "over": over,
            "runs": 0,
            "balls": 0,
            "boundaries": 0,
            "dots": 0,
        })
        over_facts["runs"] += row["batter_runs"]
        over_facts["balls"] += int(row["batter_faced_ball"])
        over_facts["boundaries"] += int(row["four"] or row["six"])
        over_facts["dots"] += int(row["batter_faced_ball"] and row["batter_runs"] == 0)
        bowler = row.get("bowler") or "Bowler unavailable"
        matchup = batter["bowlers"].setdefault(bowler, {
            "bowler": bowler,
            "batter_runs": 0,
            "balls": 0,
            "boundaries": 0,
            "dots": 0,
            "dismissals": 0,
        })
        matchup["batter_runs"] += row["batter_runs"]
        matchup["balls"] += int(row["batter_faced_ball"])
        matchup["boundaries"] += int(row["four"] or row["six"])
        matchup["dots"] += int(row["batter_faced_ball"] and row["batter_runs"] == 0)
        matchup["dismissals"] += int(row["wicket"] and row["dismissed_batter"] == name)
        if row["batter_faced_ball"]:
            batter["progression"].append(row["batter_runs"])
            batter["progression_events"].append({
                "over": row["over"],
                "ball": _ball_event(row),
                "bowler": bowler,
                "runs": row["batter_runs"],
            })
        if row["wicket"] and row["dismissed_batter"] == name:
            batter["dismissal"] = {
                "type": row["dismissal"],
                "bowler": bowler,
                "bowler_credit": row["bowler_wicket"],
                "over": int(row["over"]),
                "ball": _ball_event(row),
                "score_before": f"{row['score_before']}/{row['wickets_before']}",
            }
            batter["end_score"] = f"{row['score_after']}/{row['wickets_after']}"
        elif row["wicket"] and row["dismissed_batter"]:
            dismissed = batters.setdefault(row["dismissed_batter"], {
                "name": row["dismissed_batter"],
                "entry_score": f"{row['score_before']}/{row['wickets_before']}",
                "entry_over": row["over"],
                "first_delivery_index": row["index"],
                "runs": 0,
                "balls": 0,
                "boundaries": 0,
                "overs": {},
                "bowlers": {},
                "progression": [],
                "progression_events": [],
                "dismissal": None,
                "end_score": None,
                "entry_wickets": row["wickets_before"],
            })
            dismissed["dismissal"] = {
                "type": row["dismissal"],
                "bowler": bowler,
                "bowler_credit": row["bowler_wicket"],
                "over": int(row["over"]),
                "ball": _ball_event(row),
                "score_before": f"{row['score_before']}/{row['wickets_before']}",
            }
            dismissed["end_score"] = f"{row['score_after']}/{row['wickets_after']}"

    result = []
    for batter in batters.values():
        progression = batter.pop("progression")
        progression_events = batter.pop("progression_events")
        early_balls = 15 if len(progression) >= 30 else len(progression) // 2
        first_runs, second_runs = sum(progression[:early_balls]), sum(progression[early_balls:])
        first_rate = _rate(first_runs, early_balls)
        second_rate = _rate(second_runs, len(progression) - early_balls)
        batter["strike_rate"] = _rate(batter["runs"], batter["balls"])
        later_balls = len(progression) - early_balls
        pace_change = None
        if early_balls >= 6 and later_balls >= 6:
            if second_rate >= first_rate * 1.35 and second_runs > first_runs:
                pace_change = "acceleration"
            elif first_rate >= second_rate * 1.35 and first_runs > second_runs:
                pace_change = "slowdown"
        batter["acceleration"] = (
            {
                "first_half_sr": first_rate,
                "second_half_sr": second_rate,
                "early_balls": early_balls,
                "later_balls": len(progression) - early_balls,
                "first_segment": progression_events[:early_balls],
                "later_segment": progression_events[early_balls:],
            }
            if early_balls >= 6
            and len(progression) - early_balls >= 6
            and second_rate >= first_rate * 1.35
            and second_runs > first_runs
            else None
        )
        batter["pace_change"] = (
            {
                "type": pace_change,
                "first_segment_balls": early_balls,
                "later_segment_balls": later_balls,
                "first_segment_strike_rate": first_rate,
                "later_segment_strike_rate": second_rate,
                "first_segment": progression_events[:early_balls],
                "later_segment": progression_events[early_balls:],
            }
            if pace_change
            else None
        )
        batter["overs"] = list(batter["overs"].values())
        batter["bowler_matchups"] = [
            {
                **matchup,
                "strike_rate": _rate(matchup["batter_runs"], matchup["balls"]),
            }
            for matchup in batter["bowlers"].values()
        ]
        del batter["bowlers"]
        batter["not_out_at_checkpoint"] = batter["dismissal"] is None
        batter["innings_complete"] = innings_complete
        result.append(batter)
    return result


def _bowler_facts(rows: list[dict[str, Any]], phases: tuple[tuple[str, int, int], ...]) -> list[dict[str, Any]]:
    bowlers: dict[str, dict[str, Any]] = {}
    for row in rows:
        name = row.get("bowler") or "Bowler unavailable"
        bowler = bowlers.setdefault(name, {
            "name": name,
            "entry_score": f"{row['score_before']}/{row['wickets_before']}",
            "first_over": _over_key(row),
            "overs": {},
            "matchups": {},
            "wickets": [],
            "phase_totals": {},
            "runs": 0,
            "legal_balls": 0,
            "dots": 0,
            "boundaries": 0,
        })
        over = _over_key(row)
        over_facts = bowler["overs"].setdefault(over, {
            "over": over,
            "runs": 0,
            "legal_balls": 0,
            "dots": 0,
            "boundaries": 0,
            "wickets": [],
            "batters": {},
            "entry_score": f"{row['score_before']}/{row['wickets_before']}",
            "end_score": f"{row['score_after']}/{row['wickets_after']}",
        })
        over_facts["runs"] += row["bowler_runs"]
        over_facts["end_score"] = f"{row['score_after']}/{row['wickets_after']}"
        over_facts["legal_balls"] += int(row["is_legal"])
        over_facts["dots"] += int(row["is_legal"] and row["total_runs"] == 0)
        over_facts["boundaries"] += int(row["four"] or row["six"])
        batter = row.get("batter") or "Batter unavailable"
        over_matchup = over_facts["batters"].setdefault(batter, {
            "batter": batter,
            "batter_runs": 0,
            "runs_conceded": 0,
            "legal_balls": 0,
            "dots": 0,
            "boundaries": 0,
        })
        over_matchup["batter_runs"] += row["batter_runs"]
        over_matchup["runs_conceded"] += row["bowler_runs"]
        over_matchup["legal_balls"] += int(row["is_legal"])
        over_matchup["dots"] += int(row["is_legal"] and row["total_runs"] == 0)
        over_matchup["boundaries"] += int(row["four"] or row["six"])
        bowler["runs"] += row["bowler_runs"]
        bowler["legal_balls"] += int(row["is_legal"])
        bowler["dots"] += int(row["is_legal"] and row["total_runs"] == 0)
        bowler["boundaries"] += int(row["four"] or row["six"])
        matchup = bowler["matchups"].setdefault(batter, {
            "batter": batter,
            "batter_runs": 0,
            "runs_conceded": 0,
            "legal_balls": 0,
            "dots": 0,
            "boundaries": 0,
            "wickets": 0,
        })
        matchup["batter_runs"] += row["batter_runs"]
        matchup["runs_conceded"] += row["bowler_runs"]
        matchup["legal_balls"] += int(row["is_legal"])
        matchup["dots"] += int(row["is_legal"] and row["total_runs"] == 0)
        matchup["boundaries"] += int(row["four"] or row["six"])
        matchup["wickets"] += int(row["wicket"] and row["bowler_wicket"])
        phase = next((label for label, start, end in phases if start <= int(over) <= end), None)
        if phase:
            over_facts["phase"] = phase
        if phase:
            total = bowler["phase_totals"].setdefault(phase, {"runs": 0, "legal_balls": 0, "wickets": 0})
            total["runs"] += row["bowler_runs"]
            total["legal_balls"] += int(row["is_legal"])
            total["wickets"] += int(row["bowler_wicket"])
        if row["wicket"] and row["bowler_wicket"]:
            wicket = {
                "batter": row["dismissed_batter"],
                "over": int(row["over"]),
                "ball": _ball_event(row),
                "dismissal": row["dismissal"],
                "score_before": f"{row['score_before']}/{row['wickets_before']}",
                "score": f"{row['score_after']}/{row['wickets_after']}",
            }
            bowler["wickets"].append(wicket)
            over_facts["wickets"].append(wicket)
    result = []
    for bowler in bowlers.values():
        bowler["overs"] = [
            {
                **over,
                "batters": list(over["batters"].values()),
            }
            for over in bowler["overs"].values()
        ]
        bowler["matchups"] = list(bowler["matchups"].values())
        bowler["economy"] = round(bowler["runs"] * 6 / bowler["legal_balls"], 2) if bowler["legal_balls"] else 0
        result.append(bowler)
    return result


def _turning_points(rows: list[dict[str, Any]], partnerships: list[dict[str, Any]]) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    stand_by_wicket = {
        stand["end_wicket"]["ball"]: stand
        for stand in partnerships
        if stand.get("end_wicket")
    }
    overs: dict[int, dict[str, Any]] = {}
    for row in rows:
        over = int(row["over"])
        item = overs.setdefault(over, {"over": over, "runs": 0, "wickets": 0, "balls": 0, "rows": []})
        item["runs"] += row["total_runs"]
        item["wickets"] += int(row["wicket"])
        item["balls"] += int(row["is_legal"])
        item["rows"].append(row)

    over_numbers = sorted(overs)

    def passage(start: int, end: int) -> list[dict[str, Any]]:
        return [overs[number] for number in over_numbers if start <= number <= end]

    for row_index, row in enumerate(rows):
        if not row["wicket"]:
            continue
        stand = stand_by_wicket.get(_ball_event(row))
        before = passage(int(row["over"]) - 3, int(row["over"]) - 1)
        after = passage(int(row["over"]), int(row["over"]) + 2)
        if len(before) < 2 or len(after) < 2:
            continue
        before_rate = sum(item["runs"] for item in before) / len(before)
        after_rate = sum(item["runs"] for item in after) / len(after)
        later_wickets = sum(item["wickets"] for item in after)
        substantial_stand = bool(stand and stand["runs"] >= 35)
        if not substantial_stand and later_wickets < 2:
            continue
        if after_rate > before_rate - 2 and later_wickets < 2:
            continue
        following_rows = [
            item for item in rows[row_index + 1:]
            if int(row["over"]) <= int(item["over"]) <= int(row["over"]) + 2
        ]
        if not following_rows:
            continue
        final = following_rows[-1]
        dismissal = (
            f"{row.get('bowler') or 'the bowler'} took {row['dismissed_batter']}'s wicket"
            if row["bowler_wicket"]
            else f"{row['dismissed_batter']} was dismissed"
        )
        event = (
            f"From {row['score_before']}/{row['wickets_before']}, {dismissal} at "
            f"{_ball_event(row)}; the score became "
            f"{row['score_after']}/{row['wickets_after']}."
        )
        if stand:
            event += (
                f" It ended a {stand['runs']}-run stand with "
                f"{' and '.join(stand['batters'])}."
            )
        remaining_in_over = [
            item for item in following_rows if int(item["over"]) == int(row["over"])
        ]
        if remaining_in_over:
            aftermath_context = (
                f"From the remaining deliveries in over {row['over']} through "
                f"over {final['over']}"
            )
        else:
            aftermath_context = (
                f"From over {following_rows[0]['over']} through over {final['over']}"
            )
        event += (
            f" {aftermath_context}, the side added "
            f"{sum(item['total_runs'] for item in following_rows)} runs and lost "
            f"{_quantity(sum(int(item['wicket']) for item in following_rows), 'further wicket')}, "
            f"reaching {final['score_after']}/{final['wickets_after']}."
        )
        points.append({
            "kind": "wicket",
            "event": event,
            "event_order": row["event_order"],
            "batter": row["dismissed_batter"],
            "ball": _ball_event(row),
            "bowler": row.get("bowler") if row["bowler_wicket"] else None,
            "score_before": f"{row['score_before']}/{row['wickets_before']}",
            "score_after": f"{row['score_after']}/{row['wickets_after']}",
            "partnership_runs": stand["runs"] if stand else None,
            "aftermath_runs": sum(item["total_runs"] for item in following_rows),
            "aftermath_wickets": sum(int(item["wicket"]) for item in following_rows),
            "aftermath_score": f"{final['score_after']}/{final['wickets_after']}",
        })

    surge_candidates = []
    for end in range(3, len(over_numbers)):
        current_numbers = over_numbers[max(0, end - 2):end + 1]
        prior_numbers = over_numbers[max(0, end - 5):end - 2]
        after_numbers = over_numbers[end + 1:end + 4]
        if len(current_numbers) < 3 or len(prior_numbers) < 2 or len(after_numbers) < 2:
            continue
        current = [overs[number] for number in current_numbers]
        prior = [overs[number] for number in prior_numbers]
        after = [overs[number] for number in after_numbers]
        current_rate = sum(item["runs"] for item in current) / len(current)
        prior_rate = sum(item["runs"] for item in prior) / len(prior)
        if current_rate < prior_rate + 3 or sum(item["runs"] for item in current) < 24:
            continue
        surge_candidates.append((current_rate - prior_rate, current, prior, after))
    if surge_candidates:
        _, current, prior, after = max(surge_candidates, key=lambda item: item[0])
        first_row, last_row = current[0]["rows"][0], current[-1]["rows"][-1]
        aftermath_row = after[-1]["rows"][-1]
        batter_runs: dict[str, int] = {}
        for item in current:
            for delivery in item["rows"]:
                batter = delivery.get("batter") or "Batter unavailable"
                batter_runs[batter] = batter_runs.get(batter, 0) + delivery["batter_runs"]
        leader, leader_runs = max(batter_runs.items(), key=lambda item: item[1])
        points.append({
            "kind": "scoring-passage",
            "event_order": first_row["event_order"],
            "event": (
                f"After {round(sum(item['runs'] for item in prior) / len(prior), 1)} runs per over "
                f"in the preceding passage, the rate climbed to "
                f"{round(sum(item['runs'] for item in current) / len(current), 1)} across "
                f"overs {current[0]['over']}-{current[-1]['over']}. {leader} led with "
                f"{leader_runs} off the bat. The following overs added "
                f"{sum(item['runs'] for item in after)} runs and "
                f"{_quantity(sum(item['wickets'] for item in after), 'wicket')}, taking the score from "
                f"{last_row['score_after']}/{last_row['wickets_after']} to "
                f"{aftermath_row['score_after']}/{aftermath_row['wickets_after']}."
            ),
        })
    return sorted(points, key=lambda point: point["event_order"])


def _turning_point_priority(point: dict[str, Any]) -> int:
    if point["kind"] == "required-rate-change":
        return 4
    if point["kind"] == "scoring-passage":
        return 2
    if point["kind"] == "wicket":
        if point.get("aftermath_wickets", 0) >= 2:
            return 4
        if point.get("partnership_runs", 0) >= 50:
            return 3
        if point.get("partnership_runs", 0) >= 35:
            return 1
    return 0


def build_innings_facts(
    context: dict[str, Any],
    innings_label: str,
) -> dict[str, Any] | None:
    innings = next((row for row in context["innings"] if str(row["innings"]) == str(innings_label)), None)
    if innings is None:
        return None
    source_rows = [row for row in context["delivery_history"] if str(row["innings"]) == str(innings_label)]
    if not source_rows:
        return None
    rows = _canonical_innings_timeline(source_rows)
    complete = str(innings_label) in context["completed_innings"]
    final_event = rows[-1]
    innings_runs = final_event["score_after"]
    innings_wickets = final_event["wickets_after"]
    legal_balls = final_event["legal_balls_after"]
    if (
        innings_runs != int(innings["runs"])
        or innings_wickets != int(innings["wickets"])
        or legal_balls != int(innings["legal_balls"])
    ):
        raise ValueError(
            f"Canonical delivery totals do not reconcile with innings {innings_label} aggregates."
        )
    innings_overs = f"{legal_balls // 6}.{legal_balls % 6}"
    innings_run_rate = round(innings_runs * 6 / legal_balls, 2) if legal_balls else 0.0
    phases = _phase_ranges(context.get("match_format"))
    overs: dict[str, dict[str, Any]] = {}
    for row in rows:
        over = _over_key(row)
        item = overs.setdefault(over, {
            "over": over,
            "runs": 0,
            "wickets": [],
            "dots": 0,
            "boundaries": 0,
            "entry_score": f"{row['score_before']}/{row['wickets_before']}",
            "end_score": f"{row['score_after']}/{row['wickets_after']}",
            "batters": {},
            "bowlers": {},
            "entry_batters": [
                name for name in (row.get("batter"), row.get("non_striker")) if name
            ],
        })
        item["runs"] += row["total_runs"]
        item["end_score"] = f"{row['score_after']}/{row['wickets_after']}"
        item["dots"] += int(row["is_legal"] and row["total_runs"] == 0)
        item["boundaries"] += int(row["four"] or row["six"])
        if row["wicket"]:
            item["wickets"].append({
                "batter": row["dismissed_batter"],
                "bowler": row.get("bowler"),
                "bowler_credit": row["bowler_wicket"],
                "dismissal": row["dismissal"],
                "ball": _ball_event(row),
                "score": f"{row['score_after']}/{row['wickets_after']}",
            })
        batter = row.get("batter") or "Batter unavailable"
        item["batters"][batter] = item["batters"].get(batter, 0) + row["batter_runs"]
        bowler = row.get("bowler") or "Bowler unavailable"
        item["bowlers"][bowler] = item["bowlers"].get(bowler, 0) + row["bowler_runs"]
    for item in overs.values():
        item["batters"] = [{"batter": name, "runs": runs} for name, runs in item["batters"].items()]
        item["bowlers"] = [{"bowler": name, "runs": runs} for name, runs in item["bowlers"].items()]

    phase_facts = []
    for label, first, last in phases:
        phase_rows = [row for row in rows if first <= int(row["over"]) <= last]
        if phase_rows:
            phase_facts.append({
                "phase": label,
                "overs": f"{first}-{last}",
                "start_over": first,
                "end_over": last,
                "runs": sum(row["total_runs"] for row in phase_rows),
                "wickets": sum(int(row["wicket"]) for row in phase_rows),
                "legal_balls": sum(int(row["is_legal"]) for row in phase_rows),
                "dots": sum(int(row["is_legal"] and row["total_runs"] == 0) for row in phase_rows),
                "boundaries": sum(int(row["four"] or row["six"]) for row in phase_rows),
                "run_rate": round(
                    sum(row["total_runs"] for row in phase_rows) * 6
                    / sum(int(row["is_legal"]) for row in phase_rows),
                    2,
                ) if any(row["is_legal"] for row in phase_rows) else 0,
                "complete": (
                    complete
                    or sum(int(row["is_legal"]) for row in rows if int(row["over"]) <= last) >= last * 6
                    or innings_wickets >= 10
                ),
            })

    second_innings = str(innings_label).isdigit() and int(innings_label) > 1
    prior = [
        item for item in context["innings"]
        if str(item["innings"]).isdigit() and int(item["innings"]) == int(innings_label) - 1
    ] if second_innings else []
    target = prior[-1]["runs"] + 1 if prior else None
    limit = _innings_limit(context.get("match_format"))
    complete = (
        complete
        or innings_wickets >= 10
        or (limit is not None and legal_balls >= limit * 6)
        or (target is not None and innings_runs >= target)
    )
    if complete:
        for phase in phase_facts:
            phase["complete"] = True
    chase_by_over = []
    if target is not None:
        for over in overs.values():
            end_row = next(row for row in reversed(rows) if _over_key(row) == over["over"])
            balls_remaining = (
                max(0, limit * 6 - end_row["legal_balls_after"])
                if limit is not None
                else None
            )
            runs_required = max(0, target - end_row["score_after"])
            chase_by_over.append({
                "over": over["over"],
                "score": f"{end_row['score_after']}/{end_row['wickets_after']}",
                "runs_required": runs_required,
                "balls_remaining": balls_remaining,
                "required_run_rate": (
                    round(runs_required * 6 / balls_remaining, 2)
                    if balls_remaining
                    else 0.0 if balls_remaining == 0 else None
                ),
            })

    partnerships = _make_partnerships(rows)
    batters = _batter_facts(rows, complete)
    for batter in batters:
        batter["partnerships"] = []
    for stand in partnerships:
        for batter in batters:
            if batter["name"] in stand["batters"]:
                batter["partnerships"].append({
                    "runs": stand["runs"],
                    "batters": stand["batters"],
                    "contributions": stand["contributions"],
                    "start_over": stand["start_over"],
                    "end_over": stand["end_over"],
                    "ongoing": bool(stand.get("ongoing")),
                })
    delivery_facts = _delivery_facts(rows)
    result = {
        "innings": str(innings_label),
        "batting_team": _name(innings["batting_team"]),
        "bowling_team": _name(innings["bowling_team"]),
        "score": f"{innings_runs}/{innings_wickets}",
        "overs": innings_overs,
        "run_rate": innings_run_rate,
        "complete": complete,
        "match_format": context.get("match_format"),
        "target": target,
        "required_rate_timeline": chase_by_over,
        "overs_timeline": list(overs.values()),
        "deliveries": delivery_facts,
        "event_timeline": delivery_facts,
        "timeline_event_count": len(rows),
        "timeline_score": f"{innings_runs}/{innings_wickets}",
        "batters": batters,
        "bowlers": _bowler_facts(rows, phases),
        "phases": phase_facts,
        "partnerships": partnerships,
        "turning_points": _turning_points(rows, partnerships),
        "wickets": [
            {
                "batter": row["dismissed_batter"],
                "bowler": row.get("bowler"),
                "bowler_credit": row["bowler_wicket"],
                "over": int(row["over"]),
                "ball": _ball_event(row),
                "dismissal": row["dismissal"],
                "score_before": f"{row['score_before']}/{row['wickets_before']}",
                "score": f"{row['score_after']}/{row['wickets_after']}",
            }
            for row in rows if row["wicket"]
        ],
    }
    if second_innings and len(context["innings"]) >= 2:
        first = context["innings"][0]
        result["target"] = first["runs"] + 1
        result["first_innings_score"] = f"{first['runs']}/{first['wickets']}"
        result["batting_team"] = _name(innings["batting_team"])
        result["bowling_team"] = _name(innings["bowling_team"])
        if complete:
            if innings_runs >= result["target"]:
                result["match_result"] = {
                    "winner": result["batting_team"],
                    "margin": f"{10 - innings_wickets} wickets",
                }
            elif innings_runs == first["runs"]:
                result["match_result"] = {"winner": None, "margin": "scores level"}
            else:
                result["match_result"] = {
                    "winner": result["bowling_team"],
                    "margin": f"{first['runs'] - innings_runs} runs",
                }
    if second_innings and len(chase_by_over) > 1:
        significant_shifts = [
            (before, after)
            for before, after in zip(chase_by_over, chase_by_over[1:])
            if before["required_run_rate"] is not None
            and after["required_run_rate"] is not None
            and abs(after["required_run_rate"] - before["required_run_rate"]) >= 2
        ]
        if significant_shifts:
            before, after = max(
                significant_shifts,
                key=lambda pair: abs(pair[1]["required_run_rate"] - pair[0]["required_run_rate"]),
            )
            direction = "up" if after["required_run_rate"] > before["required_run_rate"] else "down"
            after_rows = [row for row in rows if _over_key(row) == after["over"]]
            over_batter_runs: dict[str, int] = {}
            for row in after_rows:
                batter = row.get("batter") or "Batter unavailable"
                over_batter_runs[batter] = (
                    over_batter_runs.get(batter, 0) + row["batter_runs"]
                )
            over_leader = max(over_batter_runs.items(), key=lambda item: item[1], default=None)
            surge_detail = (
                f" {over_leader[0]} led with {over_leader[1]} off the bat."
                if over_leader and over_leader[1] >= 8
                else ""
            )
            result["turning_points"].append({
                "event_order": after_rows[-1]["event_order"] if after_rows else 0,
                "kind": "required-rate-change",
                "event": (
                    f"At {before['score']} after over {before['over']}, "
                    f"{result['batting_team']} needed "
                    f"{before['required_run_rate']} per over. Over {after['over']} brought "
                    f"{sum(row['total_runs'] for row in after_rows)}, lifting the score to "
                    f"{after['score']} and bringing the required rate {direction} to "
                    f"{after['required_run_rate']}.{surge_detail}"
                ),
            })
    result["turning_points"] = sorted(
        sorted(
            (
                point for point in result["turning_points"]
                if _turning_point_priority(point) >= 2
            ),
            key=lambda point: (
                -_turning_point_priority(point),
                point.get("event_order", 0),
            ),
        )[:3],
        key=lambda point: point.get("event_order", 0),
    )
    return result


def _phase_prose(facts: dict[str, Any], phase: dict[str, Any]) -> str:
    overs = [
        row for row in facts["overs_timeline"]
        if phase["start_over"] <= int(row["over"]) <= phase["end_over"]
    ]
    if not overs:
        return "No deliveries from this phase are in the selected checkpoint."
    first, last = overs[0], overs[-1]
    phase_batters: dict[str, int] = {}
    for over in overs:
        for batter in over["batters"]:
            phase_batters[batter["batter"]] = phase_batters.get(batter["batter"], 0) + batter["runs"]
    leader = max(phase_batters.items(), key=lambda item: item[1], default=None)
    peak = max(overs, key=lambda over: over["runs"])
    peak_batter = max(peak["batters"], key=lambda item: item["runs"], default=None)
    label = phase["phase"].casefold()
    if label == "powerplay":
        opening = overs[:2]
        opening_runs = sum(over["runs"] for over in opening)
        text = (
            f"The powerplay gathered pace after {opening_runs} runs in the first "
            f"{_quantity(len(opening), 'over')}."
        )
        if peak["runs"] >= 12 and peak_batter and peak_batter["runs"]:
            text += (
                f" Over {peak['over']} was the high point, with "
                f"{peak_batter['batter']} scoring {peak_batter['runs']} off the bat."
            )
        else:
            text += " The scoring built steadily rather than around one large over."
    elif label == "middle overs":
        text = (
            f"The middle overs were the innings' most stable passage: "
            f"{facts['batting_team']} moved from {first['entry_score']} to "
            f"{last['end_score']} across the phase."
        )
        if leader:
            text += f" {leader[0]} led the scoring with {leader[1]} off the bat."
        if phase["wickets"]:
            wickets = [wicket for over in overs for wicket in over["wickets"]]
            wicket_details = "; ".join(
                f"{wicket['batter']} fell at {wicket['ball']}, leaving "
                f"{facts['batting_team']} {wicket['score']}"
                for wicket in wickets
            )
            text += f" The stand of the phase ended when {wicket_details}."
        else:
            text += " No wicket fell, allowing the partnership to last through the phase."
    else:
        text = (
            f"The final phase brought {phase['runs']} runs at "
            f"{phase['run_rate']} per over, but {phase['wickets']} "
            f"{'wickets' if phase['wickets'] != 1 else 'wicket'} also fell."
        )
        if leader:
            text += f" {leader[0]} made the largest contribution, scoring {leader[1]} off the bat."
        if peak["runs"] >= 12 and peak_batter and peak_batter["runs"]:
            text += (
                f" The late surge peaked at {peak['runs']} in over {peak['over']}, "
                f"with {peak_batter['batter']} scoring {peak_batter['runs']}."
            )
    wickets = [wicket for over in overs for wicket in over["wickets"]]
    if label == "powerplay" and wickets:
        final_wicket = wickets[-1]
        text += (
            f" The wickets of {_join_names([wicket['batter'] for wicket in wickets])} "
            f"left {facts['batting_team']} {final_wicket['score']} at the close."
        )
    elif label == "death overs" and wickets:
        text += (
            f" The phase ended with {wickets[-1]['batter']} out at "
            f"{wickets[-1]['ball']}, leaving {wickets[-1]['score']}."
        )
    return text


def _batter_prose(batter: dict[str, Any]) -> str:
    name = batter["name"]
    runs, balls = batter["runs"], batter["balls"]
    entry = batter["entry_score"]
    first_over = batter["entry_over"]
    overs = sorted(batter["overs"], key=lambda item: int(item["over"]))
    best = max(overs, key=lambda over: over["runs"], default=None)
    acceleration = batter.get("acceleration")
    pace_change = batter.get("pace_change")
    segment_change = acceleration or (
        pace_change if (pace_change or {}).get("type") == "slowdown" else None
    )
    entry_context = (
        f"{name} came in with the score {entry} in over {first_over}. "
        if entry != "0/0"
        else f"{name} opened the innings. "
    )
    if segment_change:
        first_segment = segment_change.get("first_segment", [])
        later_segment = segment_change.get("later_segment", [])

        def segment_summary(events: list[dict[str, Any]]) -> tuple[str, list[str]]:
            over_numbers = list(dict.fromkeys(str(event["over"]) for event in events))
            bowler_runs: dict[str, int] = {}
            for event in events:
                bowler_runs[event["bowler"]] = bowler_runs.get(event["bowler"], 0) + event["runs"]
            bowlers = [
                bowler
                for bowler, _ in sorted(
                    bowler_runs.items(), key=lambda item: (-item[1], item[0])
                )[:2]
            ]
            over_span = (
                f"overs {over_numbers[0]}-{over_numbers[-1]}"
                if len(over_numbers) > 1
                else f"over {over_numbers[0]}"
            ) if over_numbers else "the recorded overs"
            return over_span, bowlers

        first_span, first_bowlers = segment_summary(first_segment)
        later_span, later_bowlers = segment_summary(later_segment)
        first_rate = segment_change.get(
            "first_half_sr", segment_change.get("first_segment_strike_rate")
        )
        later_rate = segment_change.get(
            "second_half_sr", segment_change.get("later_segment_strike_rate")
        )
        first_balls = segment_change.get("early_balls", segment_change.get("first_segment_balls"))
        later_balls = segment_change.get("later_balls", segment_change.get("later_segment_balls"))
        first_runs = sum(event["runs"] for event in first_segment)
        later_runs = sum(event["runs"] for event in later_segment)
        text = (
            f"{entry_context}The first {_quantity(first_balls, 'ball')} brought "
            f"{first_runs} runs at a strike rate of {first_rate} across {first_span}; "
            f"across the next {_quantity(later_balls, 'ball')}, {later_runs} came "
            f"at {later_rate} across {later_span}."
        )
        if later_bowlers:
            text += (
                f" The acceleration came most clearly against "
                f"{_join_names(later_bowlers)}."
            )
        if first_bowlers and first_bowlers != later_bowlers:
            text += f" Those early runs came against {_join_names(first_bowlers)}."
        text += f" He ended with {runs} from {_quantity(balls, 'ball')}."
    else:
        text = (
            f"{entry_context}{name} finished with {runs} from "
            f"{_quantity(balls, 'ball')} at a strike rate of {batter['strike_rate']}."
        )
    strongest_stand = max(batter["partnerships"], key=lambda item: item["runs"], default=None)
    if strongest_stand and strongest_stand["runs"] >= 20:
        partners = " and ".join(strand for strand in strongest_stand["batters"] if strand != name)
        if partners:
            text += (
                f" {name} shared a {strongest_stand['runs']}-run "
                f"stand with {partners}, from overs {strongest_stand['start_over']}-"
                f"{strongest_stand['end_over']}"
            )
            contribution = next(
                (item for item in strongest_stand["contributions"] if item["batter"] == name),
                None,
            )
            if contribution:
                text += (
                    f", contributing {contribution['runs']} from "
                    f"{_quantity(contribution['balls'], 'ball')} in that stand"
                )
            text += "."
    later_event_overs = {
        int(event["over"])
        for event in (segment_change or {}).get("later_segment", [])
    }
    if best and best["runs"] >= 8 and int(best["over"]) not in later_event_overs:
        text += (
            f" Over {best['over']} was a notable passage, with {name} scoring {best['runs']} from "
            f"{_quantity(best['balls'], 'ball')}"
        )
        text += "."
    if batter["dismissal"]:
        if batter["dismissal"]["bowler_credit"]:
            dismissed_by = batter["dismissal"].get("bowler") or "the bowler"
            dismissal_text = f"was out to {dismissed_by}"
        else:
            dismissal_text = "was dismissed"
        text += (
            f" {name} {dismissal_text} at {batter['dismissal']['ball']} "
            f"({batter['dismissal']['type']}), with the score moving from "
            f"{batter['dismissal']['score_before']} to {batter['end_score']}."
        )
    else:
        text += (
            f" {name} remained unbeaten when the innings ended."
            if batter["innings_complete"]
            else f" {name} was not out at this checkpoint."
        )
    return text


def _bowler_prose(
    bowler: dict[str, Any],
    innings_complete: bool,
    facts: dict[str, Any] | None = None,
) -> str:
    overs = bowler["overs"]
    innings_rate = facts.get("run_rate") if facts else None
    completed_overs, remaining_balls = divmod(bowler["legal_balls"], 6)
    overs_bowled = (
        f"{completed_overs} overs"
        if remaining_balls == 0
        else f"{completed_overs}.{remaining_balls} overs"
    )
    sentences = [
        f"{bowler['name']} gave away {bowler['runs']} from {overs_bowled}, "
        f"an economy rate of {bowler['economy']}."
    ]
    phase_totals = bowler["phase_totals"]
    if len(phase_totals) > 1:
        phase_details = [
            f"{phase.lower()} spell cost {total['runs']} runs"
            + (
                f" and brought {_quantity(total['wickets'], 'wicket')}"
                if total["wickets"] else ""
            )
            for phase, total in phase_totals.items()
        ]
        sentences.append(
            f"The spell changed across phases: {'; then '.join(phase_details)}."
        )
    if bowler["wickets"]:
        events = []
        for wicket in bowler["wickets"]:
            stand = next(
                (
                    partnership
                    for partnership in (facts or {}).get("partnerships", [])
                    if partnership.get("end_wicket", {}).get("ball") == wicket["ball"]
                ),
                None,
            )
            impact = (
                f"ending the {stand['runs']}-run stand"
                if stand
                else f"moving the score from {wicket['score_before']} to {wicket['score']}"
            )
            events.append(
                f"{wicket['batter']} at {wicket['ball']} ({impact})"
            )
        if len(events) == 1:
            sentences.append(
                f"The breakthrough came against {events[0]}, interrupting the innings at "
                f"{bowler['wickets'][0]['score']}."
            )
        else:
            sentences.append(
                f"The breakthroughs arrived in sequence: {'; then '.join(events)}."
            )
    else:
        if innings_rate is not None and bowler["economy"] <= innings_rate - 1.5:
            sentences.append(
                f"That kept the scoring rate well below the innings' "
                f"{innings_rate} runs per over."
            )
    average_over = (
        bowler["runs"] * 6 / bowler["legal_balls"]
        if bowler["legal_balls"]
        else 0
    )
    expensive_overs = [
        over for over in overs
        if over["runs"] >= average_over + 5
    ]
    if expensive_overs:
        if len(expensive_overs) == 1:
            item = expensive_overs[0]
            remaining = [over["runs"] for over in overs if over is not item]
            if remaining and max(remaining) < item["runs"] - 4:
                sentences.append(
                    f"Over {item['over']} was the clear outlier at {item['runs']} runs; "
                    f"the other overs cost {min(remaining)}-{max(remaining)}."
                )
            else:
                sentences.append(
                    f"His most expensive over was {item['over']}, which cost {item['runs']}."
                )
        else:
            phase_names = list(dict.fromkeys(
                over.get("phase") for over in expensive_overs if over.get("phase")
            ))
            location = (
                f" across the {_join_names([phase.lower() for phase in phase_names])}"
                if phase_names else ""
            )
            sentences.append(
                f"The expensive overs were {', '.join(over['over'] for over in expensive_overs)}"
                f"{location}, so the runs were not confined to one passage."
            )
    if bowler["matchups"]:
        most_freely_scored = max(
            bowler["matchups"],
            key=lambda item: item["batter_runs"],
        )
        if most_freely_scored["batter_runs"] >= 8:
            sentences.append(
                f"{most_freely_scored['batter']} took "
                f"{most_freely_scored['batter_runs']} off his bowling."
            )
    if not innings_complete:
        checkpoint = facts.get("overs", "this checkpoint") if facts else "this checkpoint"
        sentences.append(f"The figures are provisional through {checkpoint} overs.")
    return " ".join(sentence.strip() for sentence in sentences)


def _partnership_prose(stand: dict[str, Any], innings_complete: bool) -> str:
    batters = _join_names(stand["batters"]) or "the pair"
    contributions = {
        item["batter"]: item for item in stand["contributions"]
    }
    contribution_text = "; ".join(
        f"{name} made {contributions[name]['runs']} from "
        f"{_quantity(contributions[name]['balls'], 'ball')}"
        for name in stand["batters"] if name in contributions
    )
    result = (
        f"The {stand['runs']}-run partnership between {batters} began at "
        f"{stand['start_score']} and spanned overs {stand['start_over']}-"
        f"{stand['end_over']}. {contribution_text}."
    )
    if stand.get("end_wicket"):
        wicket = stand["end_wicket"]
        result += (
            f" It ended at {wicket['ball']} when {wicket['batter']} was out, "
            f"leaving {wicket['score']}."
        )
    elif innings_complete:
        result += " The pair remained together when the innings ended."
    else:
        result += " The partnership was still intact at the selected checkpoint."
    return result


def _report_prose(facts: dict[str, Any]) -> list[str]:
    overs = facts["overs_timeline"]
    if not overs:
        return ["No delivery-backed account is available at this checkpoint."]

    team = facts["batting_team"]
    opening_overs = overs[:min(2, len(overs))]
    opening_runs = sum(over["runs"] for over in opening_overs)
    powerplay = next(
        (phase for phase in facts["phases"] if phase["phase"] == "Powerplay"),
        None,
    )
    powerplay_overs = [
        over for over in overs
        if powerplay and powerplay["start_over"] <= int(over["over"]) <= powerplay["end_over"]
    ]
    peak_over = max(powerplay_overs or opening_overs, key=lambda over: over["runs"])
    peak_batter = max(peak_over["batters"], key=lambda row: row["runs"], default=None)
    opening = (
        f"{team} made {opening_runs} in the first "
        f"{_quantity(len(opening_overs), 'over')}."
    )
    if facts.get("target") is not None and facts.get("required_rate_timeline"):
        opening += (
            f" Chasing {facts['target']}, they began with a required rate of "
            f"{facts['required_rate_timeline'][0]['required_run_rate']} per over."
        )
    opening_wickets = [
        wicket for wicket in facts.get("wickets", [])
        if int(wicket["over"]) <= (int(opening_overs[-1]["over"]) if opening_overs else 0)
    ]
    if opening_wickets:
        first_over_wickets = [
            wicket for wicket in opening_wickets
            if opening_overs and int(wicket["over"]) == int(opening_overs[0]["over"])
        ]
        if len(first_over_wickets) >= 2:
            names = _join_names([wicket["batter"] for wicket in first_over_wickets])
            bowler = first_over_wickets[0]["bowler"]
            opening += (
                f" {names} both fell in the opening over"
                + (f" to {bowler}" if bowler else "")
                + f", leaving {team} {first_over_wickets[-1]['score']}."
            )
    if peak_over["runs"] >= 12:
        opening += (
            f" The scoring lifted in over {peak_over['over']}, when "
            f"{peak_over['runs']} runs came"
        )
        if facts.get("target") is not None:
            rate_before = next(
                (
                    point for point in reversed(facts.get("required_rate_timeline", []))
                    if int(point["over"]) < int(peak_over["over"])
                    and point.get("required_run_rate") is not None
                ),
                None,
            )
            rate_after = next(
                (
                    point for point in facts.get("required_rate_timeline", [])
                    if int(point["over"]) == int(peak_over["over"])
                    and point.get("required_run_rate") is not None
                ),
                None,
            )
            if (
                rate_before and rate_after
                and abs(rate_after["required_run_rate"] - rate_before["required_run_rate"]) >= 1
            ):
                direction = (
                    "down"
                    if rate_after["required_run_rate"] < rate_before["required_run_rate"]
                    else "up"
                )
                opening += (
                    f", bringing the required rate {direction} from "
                    f"{rate_before['required_run_rate']} after over {rate_before['over']} "
                    f"to {rate_after['required_run_rate']} after over {rate_after['over']}"
                )
        if peak_batter and peak_batter["runs"]:
            opening += f", with {peak_batter['batter']} scoring {peak_batter['runs']} off the bat"
        opening += "."
    first_stand = next(
        (stand for stand in facts["partnerships"] if stand["runs"] >= 20),
        None,
    )
    if first_stand and first_stand.get("end_wicket"):
        wicket = first_stand["end_wicket"]
        dismissed_by = (
            f" to {wicket['bowler']}"
            if wicket.get("bowler_credit") and wicket.get("bowler")
            else ""
        )
        opening += (
            f" The {first_stand['runs']}-run stand between "
            f"{_join_names(first_stand['batters'])} ended at {wicket['ball']}, "
            f"with {wicket['batter']} out{dismissed_by} and the score {wicket['score']}."
        )
    if powerplay_overs:
        powerplay_wickets = [
            wicket for wicket in facts.get("wickets", [])
            if int(wicket["over"]) <= int(powerplay_overs[-1]["over"])
        ]
        if powerplay_wickets:
            final_wicket = powerplay_wickets[-1]
            first_stand_wicket = first_stand.get("end_wicket") if first_stand else None
            if not first_stand_wicket or final_wicket["ball"] != first_stand_wicket["ball"]:
                opening += (
                    f" {final_wicket['batter']} then fell at {final_wicket['ball']}, "
                    f"leaving {team} {final_wicket['score']} at the end of the powerplay."
                )
        elif powerplay_overs[-1]["end_score"]:
            opening += (
                f" They reached {powerplay_overs[-1]['end_score']} by the end "
                "of the powerplay."
            )

    stand = max(facts["partnerships"], key=lambda item: item["runs"], default=None)
    closing = ""
    death = next(
        (phase for phase in facts["phases"] if phase["phase"] == "Death overs"),
        None,
    )
    if death:
        death_overs = [
            over for over in overs if int(over["over"]) >= death["start_over"]
        ]
        if death_overs:
            final_over = death_overs[-1]
            final_batter = max(
                final_over["batters"], key=lambda row: row["runs"], default=None
            )
            death_start = death_overs[0]["entry_score"]
            closing = f"{team} entered the final phase at {death_start}."
            if death["wickets"]:
                closing += (
                    f" They scored {death['runs']} runs but lost "
                    f"{death['wickets']} wickets over that stretch."
                )
            else:
                closing += f" They added {death['runs']} without losing a wicket."
            death_wickets = [
                wicket for wicket in facts.get("wickets", [])
                if int(wicket["over"]) >= death["start_over"]
            ]
            earlier_wickets = death_wickets[:-1] if final_over["wickets"] else death_wickets
            credited_wickets: dict[str, list[dict[str, Any]]] = {}
            for wicket in earlier_wickets:
                bowler = wicket["bowler"] if wicket.get("bowler_credit") else None
                if bowler:
                    credited_wickets.setdefault(bowler, []).append(wicket)
            for bowler, bowler_wickets in credited_wickets.items():
                if len(bowler_wickets) >= 2:
                    details = _join_names([
                        f"{wicket['batter']} at {wicket['ball']}"
                        for wicket in bowler_wickets
                    ])
                    closing += f" {bowler} struck twice, removing {details}."
                    break
            death_runs: dict[str, int] = {}
            for over in death_overs:
                for batter in over["batters"]:
                    death_runs[batter["batter"]] = (
                        death_runs.get(batter["batter"], 0) + batter["runs"]
                    )
            late_name, late_runs = max(
                death_runs.items(), key=lambda item: item[1], default=(None, 0)
            )
            late_batter = {"name": late_name, "runs": late_runs} if late_name else None
            if late_batter and late_batter["runs"] >= 10:
                closing += (
                    f" {late_batter['name']} supplied {late_batter['runs']} runs"
                )
                if final_batter and final_batter["runs"] >= 8:
                    closing += (
                        f", including {final_batter['runs']} in the final over"
                    )
                closing += "."
            if final_over["wickets"]:
                wicket = final_over["wickets"][-1]
                closing += (
                    f" The innings' final wicket fell at {wicket['ball']}: "
                    f"{wicket['batter']} was out"
                    + (
                        f" to {wicket['bowler']}"
                        if wicket["bowler_credit"] and wicket.get("bowler")
                        else ""
                    )
                    + f", leaving {team} {facts['score']}."
                )
            elif facts.get("complete", False):
                closing += f" The innings closed at {facts['score']}."
            if facts.get("target") is not None and facts.get("match_result"):
                result = facts["match_result"]
                if result["winner"]:
                    closing += (
                        f" {result['winner']} completed the chase in {facts['overs']} overs "
                        f"and won by {result['margin']}."
                    )
                else:
                    closing += " The match ended level."
    paragraphs = [opening]
    middle_phase = next(
        (phase for phase in facts["phases"] if phase["phase"] == "Middle overs"),
        None,
    )
    if stand and stand["runs"] >= 20:
        contributions = {
            item["batter"]: item["runs"] for item in stand["contributions"]
        }
        contribution_summary = "; ".join(
            f"{name} added {contributions[name]}"
            for name in stand["batters"] if name in contributions
        )
        middle = (
            f"The defining {stand['runs']}-run stand came from "
            f"{_join_names(stand['batters'])}: {contribution_summary}, lifting {team} from "
            f"{stand['start_score']}."
        )
        if stand.get("end_wicket"):
            wicket = stand["end_wicket"]
            dismissed_by = (
                f" to {wicket['bowler']}"
                if wicket.get("bowler_credit") and wicket.get("bowler")
                else ""
            )
            middle += (
                f" It ended at {wicket['ball']} when {wicket['batter']} was out"
                f"{dismissed_by}, leaving {team} {wicket['score']}."
            )
        if middle_phase:
            middle_end = next(
                (
                    over["end_score"] for over in overs
                    if int(over["over"]) == middle_phase["end_over"]
                ),
                None,
            )
            if middle_end:
                middle += f" The middle phase closed at {middle_end}."
        paragraphs.append(middle)
    if closing:
        paragraphs.append(closing)
    elif not facts.get("complete", False):
        paragraphs.append(
            f"The innings remains in progress at {facts['score']} through "
            f"{facts.get('overs', 'the latest checkpoint')} overs."
        )
    return paragraphs


def _final_assessment(facts: dict[str, Any], batters: list[dict[str, Any]]) -> str:
    team = facts["batting_team"]
    stand = max(facts["partnerships"], key=lambda item: item["runs"], default=None)
    opening = facts["overs_timeline"][:min(2, len(facts["overs_timeline"]))]
    opening_runs = sum(over["runs"] for over in opening)
    top_batter = max(batters, key=lambda row: (row["runs"], row["name"]), default=None)
    wicket_takers = sorted(
        (bowler for bowler in facts["bowlers"] if bowler["wickets"]),
        key=lambda row: (-len(row["wickets"]), row["economy"], row["name"]),
    )
    leading_bowlers = wicket_takers[:2]
    accelerators = [
        batter for batter in batters
        if batter.get("acceleration") and batter["runs"] >= 20
    ]
    accelerator = max(
        accelerators,
        key=lambda batter: (
            batter["acceleration"].get("second_half_sr", 0)
            - batter["acceleration"].get("first_half_sr", 0)
        ),
        default=None,
    )
    strongest_phase = max(
        facts["phases"], key=lambda phase: phase["run_rate"], default=None
    )
    death = next(
        (phase for phase in facts["phases"] if phase["phase"] == "Death overs"),
        None,
    )
    final_over = facts["overs_timeline"][-1] if facts["overs_timeline"] else None
    finisher = max(
        final_over["batters"], key=lambda row: row["runs"], default=None
    ) if final_over else None
    assessment = f"{team} made {opening_runs} in the opening {_quantity(len(opening), 'over')}."
    if facts.get("target") is not None:
        limit = _innings_limit(facts.get("match_format"))
        starting_rate = round(facts["target"] / limit, 2) if limit else None
        result = facts.get("match_result")
        before_target = next(
            (
                point for point in reversed(facts["required_rate_timeline"])
                if point["runs_required"] > 0
                and point["required_run_rate"] is not None
            ),
            None,
        )
        if result:
            if result["winner"]:
                opening_wickets = [
                    wicket for wicket in facts["wickets"]
                    if opening and int(wicket["over"]) <= int(opening[-1]["over"])
                ]
                if opening_wickets:
                    assessment += (
                        f" The early wickets left them "
                        f"{opening_wickets[-1]['score']} after the opening passage."
                    )
                if stand and stand["runs"]:
                    assessment += (
                        f" Their defining stand was {stand['runs']} between "
                        f"{_join_names(stand['batters'])}."
                    )
                if top_batter and top_batter["runs"]:
                    assessment += (
                        f" {top_batter['name']} led with {top_batter['runs']}."
                    )
                if death:
                    assessment += (
                        f" A late surge brought {death['runs']} in the final phase"
                    )
                    if finisher and finisher["runs"] >= 8:
                        assessment += (
                            f", with {finisher['batter']} scoring "
                            f"{finisher['runs']} in the final over"
                        )
                if leading_bowlers:
                    assessment += (
                        f". {facts['bowling_team']} kept the chase under pressure through "
                        + _join_names([
                            f"{bowler['name']} ({len(bowler['wickets'])} "
                            f"{'wicket' if len(bowler['wickets']) == 1 else 'wickets'})"
                            for bowler in leading_bowlers
                        ])
                    )
                if starting_rate is not None:
                    assessment += (
                        f". Set {facts['target']} to win, the required rate began at "
                        f"{starting_rate} per over"
                    )
                if before_target:
                    assessment += (
                        f"; at the end of over {before_target['over']}, "
                        f"{before_target['runs_required']} remained"
                    )
                assessment += (
                    f". {result['winner']} completed the chase in {facts['overs']} overs, "
                    f"winning by {result['margin']}."
                )
            else:
                assessment += f" The match ended level while chasing {facts['target']}."
        elif not facts["complete"]:
            latest_rate = (
                facts["required_rate_timeline"][-1]["required_run_rate"]
                if facts["required_rate_timeline"]
                else None
            )
            assessment += (
                f" The chase remains in progress against a target of {facts['target']}; "
                f"the required rate is "
                f"{latest_rate if latest_rate is not None else 'unavailable'}."
            )
    else:
        powerplay = next(
            (phase for phase in facts["phases"] if phase["phase"] == "Powerplay"),
            None,
        )
        powerplay_end = next(
            (
                over["end_score"] for over in facts["overs_timeline"]
                if powerplay and int(over["over"]) == powerplay["end_over"]
            ),
            None,
        )
        if powerplay_end:
            assessment += f" The powerplay ended at {powerplay_end}."
        if strongest_phase:
            assessment += (
                f" The fastest scoring came in the {strongest_phase['phase'].lower()}, "
                f"which brought {strongest_phase['runs']} runs at "
                f"{strongest_phase['run_rate']} per over."
            )
        if accelerator:
            change = accelerator["acceleration"]
            assessment += (
                f" {accelerator['name']}'s scoring accelerated from "
                f"{change['first_half_sr']} to {change['second_half_sr']}."
            )
        if stand and stand["runs"]:
            assessment += (
                f" The innings' central recovery was the {stand['runs']}-run stand "
                f"between {_join_names(stand['batters'])}."
            )
        if top_batter and top_batter["runs"]:
            assessment += (
                f" {top_batter['name']} led with {top_batter['runs']} from "
                f"{_quantity(top_batter['balls'], 'ball')}."
            )
        if death:
            assessment += (
                f" The closing overs added {death['runs']} while "
                f"{_quantity(death['wickets'], 'wicket')} fell"
            )
            if finisher and finisher["runs"] >= 8:
                assessment += (
                    f"; {finisher['batter']}'s {finisher['runs']} in the final over "
                    "kept the scoring moving"
                )
            assessment += "."
        if leading_bowlers:
            assessment += (
                f" {facts['bowling_team']} repeatedly broke the rhythm through "
                + _join_names([
                    f"{bowler['name']} ({len(bowler['wickets'])} "
                    f"{'wicket' if len(bowler['wickets']) == 1 else 'wickets'})"
                    for bowler in leading_bowlers
                ])
                + "."
            )
        if not facts["complete"]:
            assessment += f" The account remains provisional through {facts['overs']} overs."
    return assessment


def _fallback_sections(facts: dict[str, Any], innings_list: list[dict[str, Any]]) -> list[dict[str, Any]]:
    story_paragraphs = _report_prose(facts)

    phase_paragraphs = {label: [] for label in ("Powerplay story", "Middle-over story", "Death-over story")}
    for phase in facts["phases"]:
        if phase["phase"] == "Powerplay":
            title = "Powerplay story"
        elif phase["phase"] == "Middle overs":
            title = "Middle-over story"
        else:
            title = "Death-over story"
        phase_paragraphs[title].append(_phase_prose(facts, phase))

    significant_batters = {
        name
        for stand in facts["partnerships"] if stand["runs"] >= 15
        for name in stand["batters"]
    }
    significant_batters.update(
        batter["name"] for batter in innings_list if batter["runs"] >= 10
    )
    batter_text = [
        _batter_prose(item) for item in innings_list
        if item["name"] in significant_batters
    ]
    bowlers = facts["bowlers"]
    bowler_text = [_bowler_prose(item, facts["complete"], facts) for item in bowlers]
    major_stands = [
        stand for stand in facts["partnerships"]
        if stand["runs"] >= 20 or (stand.get("ongoing") and stand["runs"] > 0)
    ]
    if not major_stands:
        largest_stand = max(facts["partnerships"], key=lambda item: item["runs"], default=None)
        major_stands = [largest_stand] if largest_stand and largest_stand["runs"] > 0 else []
    partnership_text = [
        _partnership_prose(stand, facts["complete"])
        for stand in major_stands
    ]
    if not partnership_text:
        partnership_text = ["No partnership was completed in the recorded deliveries."]
    turning_text = [point["event"] for point in facts["turning_points"]]
    if not turning_text:
        turning_text = ["No event met the evidence threshold for a turning-point highlight in the recorded deliveries."]
    assessment = _final_assessment(facts, innings_list)
    return [
        {"title": _SECTION_TITLES[0], "paragraphs": story_paragraphs},
        {"title": _SECTION_TITLES[1], "paragraphs": phase_paragraphs["Powerplay story"] or ["No powerplay deliveries are in the selected checkpoint."]},
        {"title": _SECTION_TITLES[2], "paragraphs": phase_paragraphs["Middle-over story"] or ["No middle-over deliveries are in the selected checkpoint."]},
        {"title": _SECTION_TITLES[3], "paragraphs": phase_paragraphs["Death-over story"] or ["No death-over deliveries are in the selected checkpoint."]},
        {"title": _SECTION_TITLES[4], "paragraphs": batter_text or ["No batter has faced a recorded delivery."]},
        {"title": _SECTION_TITLES[5], "paragraphs": bowler_text or ["No bowler has delivered a recorded ball."]},
        {"title": _SECTION_TITLES[6], "paragraphs": partnership_text},
        {"title": _SECTION_TITLES[7], "paragraphs": turning_text},
        {"title": _SECTION_TITLES[8], "paragraphs": [assessment]},
    ]


def _player_facts(facts: dict[str, Any], role: str, player_name: str, player_id: Any) -> dict[str, Any]:
    group = "batters" if role == "batter" else "bowlers"
    player = next((row for row in facts[group] if row["name"] == player_name), None)
    if player is None and player_id is not None:
        player = next(
            (row for row in facts[group] if str(row.get("player_id")) == str(player_id)),
            None,
        )
    return {"role": role, "name": player_name, "player": player}


def _player_section(facts: dict[str, Any], role: str, player_name: str) -> list[dict[str, Any]]:
    group = "batters" if role == "batter" else "bowlers"
    player = next((row for row in facts[group] if row["name"] == player_name), None)
    if player is None:
        return [{"title": "Player story", "paragraphs": [f"No delivery-backed {role} contribution is recorded for {player_name} in this innings."]}]
    text = (
        _batter_prose(player)
        if role == "batter"
        else _bowler_prose(player, facts["complete"], facts)
    )
    return [{"title": "Player story", "paragraphs": [text]}]


def _narrative_plan(facts: dict[str, Any]) -> dict[str, Any]:
    opening = facts["overs_timeline"][:min(2, len(facts["overs_timeline"]))]
    strongest_stand = max(
        facts["partnerships"], key=lambda item: item["runs"], default=None
    )
    finish = next(
        (phase for phase in facts["phases"] if phase["phase"] == "Death overs"),
        None,
    )
    return {
        "opening_runs": sum(over["runs"] for over in opening),
        "defining_partnership": (
            [strongest_stand["runs"], strongest_stand["batters"]]
            if strongest_stand else None
        ),
        "key_events": [
            [point["kind"], point.get("batter"), point.get("ball")]
            for point in facts["turning_points"]
        ],
        "finish": [finish["runs"], finish["wickets"]] if finish else None,
    }


def _narrative_prompt_facts(facts: dict[str, Any]) -> dict[str, Any]:
    def segment_summary(events: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not events:
            return None
        overs = list(dict.fromkeys(item["over"] for item in events))
        bowlers: dict[str, int] = {}
        for event in events:
            bowlers[event["bowler"]] = bowlers.get(event["bowler"], 0) + event["runs"]
        return {
            "balls": len(events),
            "runs": sum(item["runs"] for item in events),
            "overs": overs,
            "ball_range": [events[0]["ball"], events[-1]["ball"]],
            "bowlers": [
                name for name, _ in sorted(
                    bowlers.items(), key=lambda item: (-item[1], item[0])
                )
            ],
        }

    batters = []
    for batter in facts["batters"]:
        change = batter.get("acceleration") or batter.get("pace_change")
        batters.append({
            "name": batter["name"],
            "runs": batter["runs"],
            "balls": batter["balls"],
            "strike_rate": batter["strike_rate"],
            "entry_score": batter["entry_score"],
            "entry_over": batter["entry_over"],
            "dismissal": batter["dismissal"],
            "acceleration": {
                "first_rate": change.get("first_half_sr", change.get("first_segment_strike_rate")),
                "later_rate": change.get("second_half_sr", change.get("later_segment_strike_rate")),
                "first": segment_summary(change.get("first_segment", [])),
                "later": segment_summary(change.get("later_segment", [])),
            } if change else None,
        })

    bowlers = []
    for bowler in facts["bowlers"]:
        bowlers.append({
            "name": bowler["name"],
            "runs": bowler["runs"],
            "legal_balls": bowler["legal_balls"],
            "economy": bowler["economy"],
            "phase_totals": bowler["phase_totals"],
            "overs": [
                [
                    over["over"],
                    over["runs"],
                    over.get("phase"),
                    [
                        [wicket["batter"], wicket["ball"], wicket["score"]]
                        for wicket in over["wickets"]
                    ],
                ]
                for over in bowler["overs"]
            ],
            "matchups": [
                [
                    matchup["batter"],
                    matchup["batter_runs"],
                    matchup["legal_balls"],
                    matchup["wickets"],
                ]
                for matchup in sorted(
                    bowler["matchups"],
                    key=lambda item: (-item["batter_runs"], item["batter"]),
                )[:1]
            ],
        })

    partnerships = [
        {
            "runs": stand["runs"],
            "batters": stand["batters"],
            "contributions": [
                {"batter": item["batter"], "runs": item["runs"], "balls": item["balls"]}
                for item in stand["contributions"]
            ],
            "start_over": stand["start_over"],
            "end_over": stand["end_over"],
            "start_score": stand["start_score"],
            "end_wicket": {
                key: stand["end_wicket"][key]
                for key in ("batter", "ball", "dismissal", "score_before", "score")
                if key in stand["end_wicket"]
            } if stand.get("end_wicket") else None,
        }
        for stand in facts["partnerships"] if stand["runs"] >= 10
    ]

    return {
        "innings": facts["innings"],
        "batting_team": facts["batting_team"],
        "bowling_team": facts["bowling_team"],
        "match_format": facts["match_format"],
        "score": facts["score"],
        "overs": facts["overs"],
        "run_rate": facts["run_rate"],
        "complete": facts["complete"],
        "target": facts["target"],
        "first_innings_score": facts.get("first_innings_score"),
        "match_result": facts.get("match_result"),
        "delivery_fields": [
            "ball", "batter", "bowler", "runs", "bat_runs", "score",
            "wicket",
        ],
        "deliveries": [
            [
                event["ball"].split(" ", 1)[0],
                event["batter"],
                event["bowler"],
                event["runs"],
                event["bat_runs"],
                event["score"],
                [
                    event["wicket"]["batter"],
                    event["wicket"]["dismissal"],
                ] if event["wicket"] else None,
            ]
            for event in facts["deliveries"]
        ],
        "batters": batters,
        "bowlers": bowlers,
        "phases": [
            {
                "phase": phase["phase"],
                "start_over": phase["start_over"],
                "end_over": phase["end_over"],
                "runs": phase["runs"],
                "wickets": phase["wickets"],
                "run_rate": phase["run_rate"],
            }
            for phase in facts["phases"]
        ],
        "partnerships": partnerships,
        "wickets": [
            [
                wicket["batter"],
                wicket["ball"],
                wicket["score_before"],
                wicket["score"],
                wicket["dismissal"],
                wicket.get("bowler"),
            ]
            for wicket in facts["wickets"]
        ],
        "required_rate_fields": [
            "over", "score", "runs_required", "balls_remaining", "required_run_rate",
        ],
        "required_rate_timeline": [
            [
                point["over"],
                point["score"],
                point["runs_required"],
                point["balls_remaining"],
                point["required_run_rate"],
            ]
            for point in facts["required_rate_timeline"]
        ],
    }


def _narrative_providers_to_try() -> list[str]:
    providers = _providers_to_try()
    for provider in ("cerebras", "gemini", "groq", "ollama"):
        if provider not in providers and _provider_config(provider):
            providers.append(provider)
    return providers


def _validated_narrative_output(decoded: str) -> NarrativeOutput:
    start, end = decoded.find("{"), decoded.rfind("}")
    if start < 0 or end < start:
        raise ValueError("Provider response is not a JSON object.")
    payload = json.loads(decoded[start:end + 1])
    if isinstance(payload, dict) and "sections" not in payload:
        by_title = {str(title).casefold(): value for title, value in payload.items()}
        expected = {title.casefold() for title in _SECTION_TITLES}
        if set(by_title) != expected:
            raise ValueError("Provider response did not contain the required narrative sections.")
        payload = {
            "sections": [
                {
                    "title": title,
                    "paragraphs": (
                        [by_title[title.casefold()]]
                        if isinstance(by_title[title.casefold()], str)
                        else by_title[title.casefold()]
                    ),
                }
                for title in _SECTION_TITLES
            ],
        }
    return NarrativeOutput.model_validate(payload)


def _validate_narrative_statistics(answer: str, facts: dict[str, Any]) -> None:
    known_scores = {
        facts["score"],
        *(
            score
            for event in facts["deliveries"]
            for score in (event["score_before"], event["score"])
        ),
    }
    if any(
        match.group(0) not in known_scores
        for match in re.finditer(r"\b\d{1,3}/\d{1,2}\b", answer)
    ):
        raise ValueError("Narrative response included a score not present in the delivery timeline.")

    partnership_runs = {stand["runs"] for stand in facts["partnerships"]}
    if any(
        int(match.group(1)) not in partnership_runs
        for match in re.finditer(
            r"\b(\d+)[\-\u2010-\u2015]run (?:stand|partnership)\b",
            answer,
            flags=re.IGNORECASE,
        )
    ):
        raise ValueError("Narrative response included an unsupported partnership total.")

    batting_pairs: set[tuple[int, int]] = set()

    def add_batting_pair(runs: int, balls: int) -> None:
        if balls > 0:
            batting_pairs.add((runs, balls))

    for batter in facts["batters"]:
        add_batting_pair(batter["runs"], batter["balls"])
        for over in batter["overs"]:
            add_batting_pair(over["runs"], over["balls"])
        for segment in (
            (batter.get("acceleration") or {}).get("first_segment", []),
            (batter.get("acceleration") or {}).get("later_segment", []),
            (batter.get("pace_change") or {}).get("first_segment", []),
            (batter.get("pace_change") or {}).get("later_segment", []),
        ):
            add_batting_pair(
                sum(event["runs"] for event in segment),
                len(segment),
            )
    for stand in facts["partnerships"]:
        add_batting_pair(stand["runs"], stand["balls"])
        for contribution in stand["contributions"]:
            add_batting_pair(contribution["runs"], contribution["balls"])
    for match in re.finditer(
        r"\b(\d+)\s+off\s+(\d+)(?:\s+balls?)?\b|\b(\d+)\s+(?:from|in)\s+(\d+)\s+(?:balls?|deliveries)\b",
        answer,
        flags=re.IGNORECASE,
    ):
        pair = (
            int(match.group(1) or match.group(3)),
            int(match.group(2) or match.group(4)),
        )
        if pair not in batting_pairs:
            raise ValueError("Narrative response included an unsupported batter run/ball figure.")

    if re.search(
        r"\b(?:one|two|three|four|five|six|seven|eight|nine|ten|\d+)\s+(?:sixes|fours)\b",
        answer,
        flags=re.IGNORECASE,
    ):
        raise ValueError("Narrative response included an unverified boundary count.")


def _has_run_ball_stat(text: str, runs: int, balls: int) -> bool:
    runs_text, balls_text = str(runs), str(balls)
    patterns = (
        rf"\b{runs_text}\s+off\s+{balls_text}(?:\s+balls?)?\b",
        rf"\b{runs_text}\s+from\s+{balls_text}\s+balls?\b",
        rf"\b{runs_text}\s*\(\s*{balls_text}\s+balls?\s*\)",
    )
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def _wicket_anchor_present(text: str, wicket: dict[str, Any]) -> bool:
    ball = wicket["ball"].split(" ", 1)[0]
    return ball in text and (
        wicket["score_before"] in text or wicket["score"] in text
    )


def _wicket_is_named(text: str, batter_name: str, aliases: list[str]) -> bool:
    name_patterns = [
        rf"\b{re.escape(alias)}\b"
        for alias in aliases
    ]
    for name in name_patterns:
        patterns = (
            rf"{name}(?:['’]s)?\s+(?:(?:was|is)\s+)?(?:dismissed|removed|out|caught|stumped|bowled|fell|run out)\b",
            rf"\b(?:dismissed|dismissing|removed|removing|caught|stumped|bowled|run out)\b[^.!?]{{0,40}}{name}",
            rf"\b(?:dismissal|wicket)\s+(?:of|for)\s+{name}",
            rf"{name}['’]s\s+(?:dismissal|wicket)\b",
        )
        if any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns):
            return True
    return False


def _number_near_name(text: str, name: str, value: int, distance: int = 45) -> bool:
    name_spans = [
        match.span()
        for match in re.finditer(re.escape(name), text, flags=re.IGNORECASE)
    ]
    value_spans = [
        match.span()
        for match in re.finditer(rf"\b{value}\b", text)
    ]
    return any(
        abs(name_start - value_start) <= distance
        or abs(name_end - value_end) <= distance
        for name_start, name_end in name_spans
        for value_start, value_end in value_spans
    )


def _validate_narrative_quality(
    sections: list[dict[str, Any]],
    answer: str,
    facts: dict[str, Any],
) -> None:
    section_map = {
        section["title"]: section["paragraphs"]
        for section in sections
    }

    if re.search(r"\b(?:one|two|three|four|five|six|seven|eight|nine|ten|\d+)\s+overs?\s+to\s+spare\b", answer, re.IGNORECASE):
        raise ValueError("Narrative response included an unverified overs-to-spare claim.")

    last_name_counts: dict[str, int] = {}
    for batter in facts["batters"]:
        last_name = batter["name"].split()[-1].casefold()
        last_name_counts[last_name] = last_name_counts.get(last_name, 0) + 1
    batter_aliases = {
        batter["name"]: [
            batter["name"],
            *(
                [batter["name"].split()[-1]]
                if last_name_counts[batter["name"].split()[-1].casefold()] == 1
                else []
            ),
        ]
        for batter in facts["batters"]
    }
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", answer):
        for wicket in facts["wickets"]:
            name = wicket["batter"]
            if _wicket_is_named(sentence, name, batter_aliases.get(name, [name])) and not _wicket_anchor_present(sentence, wicket):
                raise ValueError("Narrative response misattributed a dismissal checkpoint.")

    batter_paragraphs = section_map.get("Batter-by-batter contributions", [])
    used_paragraphs: set[int] = set()
    for batter in facts["batters"]:
        if batter["runs"] < 10:
            continue
        paragraph_index = next(
            (
                index for index, paragraph in enumerate(batter_paragraphs)
                if re.search(re.escape(batter["name"]), paragraph, flags=re.IGNORECASE)
                and _has_run_ball_stat(paragraph, batter["runs"], batter["balls"])
            ),
            None,
        )
        if paragraph_index is None or paragraph_index in used_paragraphs:
            raise ValueError("Narrative response omitted a distinct, verified batter account.")
        used_paragraphs.add(paragraph_index)
        paragraph = batter_paragraphs[paragraph_index]
        change = batter.get("acceleration") or batter.get("pace_change")
        if change:
            for segment_key in ("first_segment", "later_segment"):
                events = change.get(segment_key, [])
                if not events:
                    continue
                runs, balls = sum(event["runs"] for event in events), len(events)
                if not _has_run_ball_stat(paragraph, runs, balls):
                    raise ValueError("Narrative response omitted a verified batter scoring segment.")
                bowler_runs: dict[str, int] = {}
                for event in events:
                    bowler_runs[event["bowler"]] = bowler_runs.get(event["bowler"], 0) + event["runs"]
                leading_bowler = max(bowler_runs, key=bowler_runs.get)
                if not re.search(re.escape(leading_bowler), paragraph, flags=re.IGNORECASE):
                    raise ValueError("Narrative response omitted the leading bowler matchup for a scoring segment.")
        wicket = next(
            (item for item in facts["wickets"] if item["batter"] == batter["name"]),
            None,
        )
        if wicket and not _wicket_anchor_present(paragraph, wicket):
            raise ValueError("Narrative response omitted the batter's dismissal checkpoint.")

    partnership_paragraphs = section_map.get("Major partnerships", [])
    used_paragraphs.clear()
    for stand in facts["partnerships"]:
        if stand["runs"] < 20:
            continue
        paragraph_index = next(
            (
                index for index, paragraph in enumerate(partnership_paragraphs)
                if index not in used_paragraphs
                and all(
                    re.search(re.escape(name), paragraph, flags=re.IGNORECASE)
                    for name in stand["batters"]
                )
                and re.search(rf"\b{stand['runs']}\b", paragraph)
            ),
            None,
        )
        if paragraph_index is None:
            raise ValueError("Narrative response omitted a distinct, verified major partnership.")
        used_paragraphs.add(paragraph_index)
        paragraph = partnership_paragraphs[paragraph_index]
        for contribution in stand["contributions"]:
            if not _number_near_name(
                paragraph,
                contribution["batter"],
                contribution["runs"],
            ):
                raise ValueError("Narrative response misattributed a partnership contribution.")

    bowler_paragraphs = section_map.get("Bowler-by-bowler performances", [])
    used_paragraphs.clear()
    for bowler in facts["bowlers"]:
        completed_overs, remaining_balls = divmod(bowler["legal_balls"], 6)
        overs = (
            str(completed_overs)
            if remaining_balls == 0
            else f"{completed_overs}.{remaining_balls}"
        )
        paragraph_index = next(
            (
                index for index, paragraph in enumerate(bowler_paragraphs)
                if index not in used_paragraphs
                and re.search(re.escape(bowler["name"]), paragraph, flags=re.IGNORECASE)
                and re.search(rf"\b{bowler['runs']}\s+runs?\b", paragraph, flags=re.IGNORECASE)
                and re.search(rf"\b{re.escape(overs)}\s+overs?\b", paragraph, flags=re.IGNORECASE)
            ),
            None,
        )
        if paragraph_index is None:
            raise ValueError("Narrative response omitted a distinct, verified bowler spell.")
        used_paragraphs.add(paragraph_index)
        paragraph = bowler_paragraphs[paragraph_index]
        if any(
            not re.search(re.escape(phase), paragraph, flags=re.IGNORECASE)
            for phase in bowler["phase_totals"]
        ):
            raise ValueError("Narrative response omitted a bowler's phase pattern.")
        top_matchup = max(
            bowler["matchups"],
            key=lambda matchup: matchup["batter_runs"],
            default=None,
        )
        if top_matchup and not re.search(
            re.escape(top_matchup["batter"]),
            paragraph,
            flags=re.IGNORECASE,
        ):
            raise ValueError("Narrative response omitted a bowler's leading batter matchup.")
        for wicket in bowler["wickets"]:
            if not _wicket_anchor_present(paragraph, wicket):
                raise ValueError("Narrative response omitted a bowler's credited wicket checkpoint.")


async def _call_provider(provider: str, facts: dict[str, Any], scope: str, player: dict[str, Any] | None) -> list[dict[str, Any]]:
    config = _provider_config(provider)
    if config is None:
        raise ValueError(f"{provider} is not configured")
    report_plan = _narrative_plan(facts)
    player_instruction = (
        f"Write a short, natural player-profile passage for {player['name']} as a {player['role']}. "
        "Explain how the player shaped the innings, using only the supplied entry situation, "
        "scoring progression, matchup, partnership, and dismissal evidence. Avoid a stat-by-stat recital. "
        if player
        else "Write a professional cricket match report for this innings, not a database summary. "
    )
    system = (
        "You are a cricket correspondent and careful editor. The supplied statistics have already been "
        "calculated and reconciled; your job is to explain their cricket significance in readable prose, not "
        "to calculate or re-list them. Use the report_plan to choose the narrative arc and deliveries as "
        "the sole authority for chronology. Tell the full innings chronologically in the overall story; "
        "use phase sections to explain changes in tempo, batter sections for individual journeys, and "
        "bowler sections for spell patterns and breakthroughs. Give turning points and the assessment "
        "different purposes from those sections; do not restate their paragraphs. "
        "Describe observable scoring patterns and their effect on the score; never invent intent, psychology, "
        "shot selection, tactics, field settings, strike rotation, six/four counts, or events. Do not add a match-format label "
        "that is not supplied. Preserve the exact names, scores, overs, batter "
        "runs/balls, partnership contributions, bowling figures, and dismissal details when you use them. "
        "When describing a named dismissal, use its exact ball and score checkpoint from the delivery record. "
        "For a batter with a detected acceleration or slowdown, explain both ball segments with their run/ball "
        "figures, over range, and involved bowlers; do not compress a multi-over segment into a shorter period. "
        "Keep batter runs distinct from team runs and bowler runs conceded. Include only statistics that help "
        "explain a development. delivery_fields lists the slots in each delivery row; ball labels use "
        "over.ball notation and wicket is [dismissed batter, dismissal] or null. The final score field is "
        "the checkpoint after that ball. Deliveries are in canonical "
        "order; required_rate_fields similarly maps the chase-rate rows. Use the reconciled summaries "
        "for analysis. "
        "The separate analytics interface contains the complete over-by-over record. "
        "Do not give an over-by-over list, repeat one event in several sections, or use repetitive templates "
        "such as 'the side added X'. Highlight only turning points with measurable before-and-after impact. "
        "For a chase, explain the target, a meaningful required-rate shift, and the result only when completed. "
        "Do not state how many overs were left unless the supplied facts explicitly establish it. "
        "For an unfinished innings, mark the account as provisional. Return only JSON matching the schema. "
        "For innings scope, use these exact section titles in this order: "
        + "; ".join(_SECTION_TITLES)
        + ". Use no more than three short paragraphs for Overall innings story and one paragraph for each "
        "phase. In Batter-by-batter contributions, give each batter with at least 10 runs a separate, concise "
        "paragraph about their innings journey. In Bowler-by-bowler performances, give every bowler a separate, "
        "concise paragraph starting with runs conceded and overs in the format '33 from 4 overs' or "
        "'30 from 3.2 overs'. Cover each phase, the leading batter matchup, and credited wickets with exact "
        "ball and score checkpoints. In Major partnerships, "
        "explain stands of at least 20 runs with each batter's verified contribution and how the stand ended. "
        "Limit turning points to the supplied selected events; explain the situation before and after without "
        "assigning unsupported causes. Finish with a two-sentence synthesis. "
        "Write for a cricket fan who has not seen the ball-by-ball feed. Use natural transitions and varied "
        "sentence structure; avoid repeated names, numbers, and sentence patterns. Be concise enough to keep "
        "the full response below 1,000 words."
    )
    prompt_facts = _narrative_prompt_facts(facts)
    prompt_player = (
        {"role": player["role"], "name": player["name"]}
        if player
        else None
    )
    user = (
        player_instruction
        + f"Scope: {scope}. Checkpoint: {facts['complete'] and 'completed innings' or 'in-progress innings'}.\n"
        + "Verified report plan and compact reconciled innings context, including every delivery: "
        + json.dumps(
            {
                "report_plan": report_plan,
                "innings": prompt_facts,
                "player": prompt_player,
            },
            ensure_ascii=True,
            separators=(",", ":"),
        )
    )
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    output_limit = 1500 if provider == "cerebras" else 1000
    output_minimum = 1500 if provider == "cerebras" else 900
    output_tokens = min(output_limit, max(output_minimum, settings.llm_max_output_tokens))
    if provider == "ollama":
        body = {
            "model": config["model"],
            "messages": messages,
            "stream": False,
            "think": False,
            "format": NarrativeOutput.model_json_schema(),
            "options": {"temperature": 0.2, "num_predict": output_tokens},
        }
        headers = {}
        url = config["url"]
        params = None
    elif provider == "gemini":
        body = {
            "contents": [{"role": "user", "parts": [{"text": "\n\n".join(
                f"{item['role'].upper()}: {item['content']}" for item in messages
            )}]}],
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": output_tokens,
                "responseMimeType": "application/json",
            },
        }
        headers = {}
        url = config["url"]
        params = {"key": config["key"]}
    else:
        body = {
            "model": config["model"],
            "messages": messages,
            "temperature": 0.2,
            "max_tokens": output_tokens,
            "response_format": {"type": "json_object"},
        }
        if provider == "cerebras" and config["model"] == "gpt-oss-120b":
            body["reasoning_effort"] = "low"
        elif provider == "cerebras" and config["model"] == "qwen-3.8-27b":
            body["reasoning_effort"] = "none"
        elif provider == "groq" and config["model"].startswith("openai/gpt-oss"):
            body["reasoning_effort"] = "low"
        headers = {"Authorization": f"Bearer {config['key']}"}
        url = config["url"]
        params = None

    for attempt in range(max(1, settings.llm_retry_attempts)):
        try:
            async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
                response = await client.post(url, headers=headers, params=params, json=body)
            response.raise_for_status()
            raw = _parse_provider_content(provider, response.json())
            if not isinstance(raw, str):
                raise ValueError("Provider response content is not text.")
            decoded = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.IGNORECASE)
            generated = _validated_narrative_output(decoded)
            sections = [section.model_dump() for section in generated.sections]
            answer = " ".join(
                paragraph for section in sections for paragraph in section["paragraphs"]
            )
            context_numbers = set(re.findall(
                r"\d+(?:\.\d+)?",
                json.dumps({"report_plan": report_plan, "innings": facts}, ensure_ascii=True),
            ))
            if not set(re.findall(r"\d+(?:\.\d+)?", answer)).issubset(context_numbers):
                raise ValueError("Provider response included unsupported numeric claims.")
            _validate_narrative_statistics(answer, facts)
            _validate_narrative_entities(answer, {
                "teams": [{"name": facts["batting_team"]}, {"name": facts["bowling_team"]}],
                "innings_scorecards": [{
                    "batting_scorecard": facts["batters"],
                    "bowling_scorecard": facts["bowlers"],
                }],
                "delivery_records": facts["deliveries"],
            })
            if player:
                return sections
            title_map = {section["title"].casefold(): section for section in sections}
            if any(title.casefold() not in title_map for title in _SECTION_TITLES):
                raise ValueError("Provider response did not include every required innings section.")
            _validate_narrative_quality(sections, answer, facts)
            return [title_map[title.casefold()] for title in _SECTION_TITLES]
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {408, 429, 500, 502, 503, 504} and attempt + 1 < settings.llm_retry_attempts:
                if exc.response.status_code == 429:
                    raise
                await asyncio.sleep(0.25 * (2 ** attempt))
                continue
            raise
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError, ValidationError):
            if attempt + 1 < settings.llm_retry_attempts:
                await asyncio.sleep(0.25 * (2 ** attempt))
                continue
            raise
    raise ValueError("Narrative provider returned no usable response.")


async def generate_narrative(
    state: dict[str, Any],
    innings_label: str,
    scope: str = "innings",
    player_role: str | None = None,
    player_name: str | None = None,
    player_id: Any = None,
) -> dict[str, Any]:
    context = build_match_context(state)
    facts = build_innings_facts(context, innings_label)
    if facts is None:
        return {
            "answer": "No delivery-backed account is available for this innings at the selected checkpoint.",
            "sections": [{"title": "Innings story", "paragraphs": ["No processed deliveries are available for this innings."]}],
            "evidence": [],
            "confidence": "low",
            "limitations": ["The narrative is limited to processed ball-by-ball records."],
            "answer_mode": "DATA-DERIVED ANALYSIS",
            "ai_available": False,
        }

    player = None
    if scope == "player" and player_role and player_name:
        player = _player_facts(facts, player_role, player_name, player_id)
    fallback = (
        _player_section(facts, player_role, player_name)
        if player
        else _fallback_sections(facts, facts["batters"])
    )
    sections = fallback
    ai_available = False
    for provider in _narrative_providers_to_try():
        try:
            sections = await _call_provider(provider, facts, scope, player)
            ai_available = True
            break
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError, ValidationError) as exc:
            status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
            config = _provider_config(provider)
            logger.warning(
                "MatchSense narrative provider %s model=%s failed (status=%s, error_type=%s)",
                provider,
                config["model"] if config else "unconfigured",
                status,
                type(exc).__name__,
            )

    if scope == "player" and player and player["player"] is None:
        sections = fallback
    paragraphs = [paragraph for section in sections for paragraph in section["paragraphs"]]
    evidence = [
        {"label": "Innings score", "value": facts["score"]},
        {"label": "Overs", "value": facts["overs"]},
        {"label": "Delivery records", "value": str(len(facts["deliveries"]))},
    ]
    if facts.get("target") is not None:
        evidence.append({"label": "Target", "value": str(facts["target"])})
    limitations = []
    if not facts["complete"]:
        limitations.append("Provisional: the story includes only deliveries processed at this checkpoint.")
    if not facts["phases"]:
        limitations.append("Formal phase boundaries are unavailable for this match format.")
    return {
        "answer": "\n\n".join(paragraphs),
        "sections": sections,
        "evidence": evidence,
        "confidence": "high" if len(facts["deliveries"]) >= 12 else "medium",
        "limitations": limitations,
        "answer_mode": "AI-GENERATED NARRATIVE" if ai_available else "DATA-DERIVED ANALYSIS",
        "ai_available": ai_available,
        "facts": {
            "innings": facts["innings"],
            "complete": facts["complete"],
            "delivery_count": len(facts["deliveries"]),
        },
    }
