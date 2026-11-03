import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _number(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _decimal(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _flag(value: Any) -> bool:
    return value is True or value == 1 or (isinstance(value, str) and value.lower() in {"true", "1", "yes"})


def _innings_label(value: Any, fallback: int = 1) -> str:
    label = str(value if value is not None else fallback)
    return label[1:] if label.startswith("S") and label[1:].isdigit() else label


def _rate(runs: int | None, overs: Any) -> float | None:
    if runs is None or overs is None:
        return None
    value = str(overs).strip()
    try:
        whole, _, balls_part = value.partition(".")
        balls = int(whole) * 6 + int((balls_part or "0")[:1])
        return round(runs * 6 / balls, 2) if balls > 0 else None
    except (TypeError, ValueError):
        return None


def _overs_to_balls(overs: Any) -> int | None:
    if overs is None:
        return None
    try:
        whole, _, balls_part = str(overs).strip().partition(".")
        return int(whole) * 6 + int((balls_part or "0")[:1])
    except (TypeError, ValueError):
        return None


def _entity(value: Any) -> dict[str, Any]:
    data = _mapping(value)
    if "data" in data:
        nested = data["data"]
        if isinstance(nested, dict):
            return nested
    for key in ("player", "batsman", "bowler"):
        nested = data.get(key)
        if isinstance(nested, dict):
            return _entity(nested)
    return data


def _name(player: Any) -> str | None:
    data = _entity(player)
    name = data.get("fullname") or data.get("display_name") or data.get("name")
    if name:
        return str(name)
    first = data.get("firstname") or data.get("first_name")
    last = data.get("lastname") or data.get("last_name")
    if first or last:
        return " ".join(str(part) for part in (first, last) if part)
    return None


def _reference_id(value: Any) -> Any:
    data = _entity(value)
    return data.get("id") or data.get("player_id") or data.get("batsman_id") or data.get("bowler_id")


def _player_name_map(fixture: dict[str, Any]) -> dict[str, str]:
    names: dict[str, str] = {}
    seen: set[int] = set()

    def visit(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                visit(item)
            return
        if not isinstance(value, dict) or id(value) in seen:
            return
        seen.add(id(value))
        player_name = _name(value)
        player_id = value.get("player_id") or value.get("batsman_id") or value.get("bowler_id") or value.get("id")
        if player_id is not None and player_name:
            names[str(player_id)] = player_name
        for nested in value.values():
            visit(nested)

    for key in ("lineup", "players", "batting", "bowling", "balls", "included"):
        visit(fixture.get(key))
    return names


def _resolved_name(value: Any, player_id: Any, names: dict[str, str], role: str) -> str | None:
    name = _name(value)
    if name:
        return name
    if player_id is not None:
        name = names.get(str(player_id))
        if name:
            return name
        logger.warning("Sportmonks delivery references unresolved %s id %s", role, player_id)
    return None


def _dismissal_detail(
    value: Any,
    bowler: str | None,
    fielder: str | None,
    runner: str | None,
) -> str | None:
    if value is None:
        return None
    description = (_name(value) or str(value)).strip()
    kind = description.casefold().replace("-", " ")
    if "run out" in kind:
        return f"Run out ({runner or 'player unavailable'})"
    if "catch" in kind or "caught" in kind:
        if fielder and bowler and fielder == bowler:
            return f"c & b {bowler}"
        return f"c {fielder or 'Fielder unavailable'} b {bowler or 'Bowler unavailable'}"
    if "stump" in kind:
        return f"st {fielder or 'Fielder unavailable'} b {bowler or 'Bowler unavailable'}"
    if "lbw" in kind:
        return f"lbw b {bowler or 'Bowler unavailable'}"
    if "bowled" in kind:
        return f"b {bowler or 'Bowler unavailable'}"
    if "hit wicket" in kind:
        return f"hit wicket b {bowler or 'Bowler unavailable'}"
    if "retired hurt" in kind:
        return "Retired hurt"
    if "retired out" in kind:
        return "Retired out"
    if "obstruct" in kind:
        return "Obstructing the field"
    return description


def _nested_number(source: dict[str, Any], keys: tuple[str, ...], extras: dict[str, Any]) -> int:
    for key in keys:
        value = _number(source.get(key))
        if value is not None:
            return value
        value = _number(extras.get(key))
        if value is not None:
            return value
    return 0


def _optional_nested_number(
    source: dict[str, Any],
    keys: tuple[str, ...],
    extras: dict[str, Any],
) -> int | None:
    for key in keys:
        value = _number(source.get(key))
        if value is not None:
            return value
        value = _number(extras.get(key))
        if value is not None:
            return value
    return None


def _team(team: Any) -> dict[str, Any] | None:
    data = _mapping(team)
    if not data:
        return None
    return {
        "id": data.get("id"),
        "name": data.get("name") or data.get("fullname") or "Unknown team",
        "short_name": data.get("code") or data.get("short_name"),
        "image_path": data.get("image_path"),
    }


def _innings_limit(match_format: Any) -> int | None:
    if isinstance(match_format, dict):
        match_format = _name(match_format) or match_format.get("name")
    text = str(match_format or "").casefold()
    if re.search(r"\bt20i?\b", text):
        return 20
    if re.search(r"\bt10\b", text):
        return 10
    if "odi" in text or "list a" in text:
        return 50
    return None


def _powerplay_boundary_value(data: dict[str, Any]) -> int | None:
    for key in ("powerplay_legal_balls", "powerplay_balls", "pp_balls"):
        value = _number(data.get(key))
        if value is not None and value >= 0:
            return value
    for key in ("powerplay_overs", "pp_overs", "powerplay"):
        value = data.get(key)
        if isinstance(value, dict):
            nested_value = _powerplay_boundary_value(value)
            if nested_value is not None:
                return nested_value
            value = value.get("overs") or value.get("label")
        if value is None:
            continue
        range_match = re.search(r"(\d+)\s*-\s*(\d+)", str(value))
        if range_match:
            return int(range_match.group(2)) * 6
        text = str(value).strip()
        if text.isdigit():
            return int(text) * 6
        if "." in text:
            balls = _overs_to_balls(text)
            if balls is not None:
                return balls
    return None


def _powerplay_boundaries(
    fixture: dict[str, Any],
    normalized_innings: list[dict[str, Any]],
    default: int | None,
) -> dict[str, int | None]:
    boundaries: dict[str, int | None] = {}
    for row in _items(fixture.get("runs")):
        inning_value = row.get("inning") or row.get("scoreboard")
        if inning_value is None:
            continue
        boundary = _powerplay_boundary_value(row)
        if boundary is not None:
            boundaries[_innings_label(inning_value)] = boundary
    for key in ("powerplay", "powerplays", "scoreboards"):
        for row in _items(fixture.get(key)):
            inning_value = row.get("inning") or row.get("scoreboard")
            if inning_value is None:
                continue
            if key == "scoreboards" and "powerplay" not in str(row.get("type") or "").casefold():
                continue
            boundary = _powerplay_boundary_value(row)
            if boundary is not None:
                boundaries[_innings_label(inning_value)] = boundary
    return {
        str(inning["innings"]): boundaries.get(str(inning["innings"]), default)
        for inning in normalized_innings
    }


def _validate_fixture(
    fixture: dict[str, Any],
    innings: list[dict[str, Any]],
    scorecards: list[dict[str, Any]],
    batting_stats: list[dict[str, Any]],
    bowling_stats: list[dict[str, Any]],
    deliveries: list[dict[str, Any]],
) -> dict[str, Any]:
    issues: list[dict[str, str]] = []
    seen_issues: set[tuple[str, str]] = set()

    def flag(code: str, message: str) -> None:
        identity = (code, message)
        if identity not in seen_issues:
            issues.append({"code": code, "message": message})
            seen_issues.add(identity)

    teams = [team for team in (_team(fixture.get("localteam")), _team(fixture.get("visitorteam"))) if team]
    team_ids = {str(team["id"]) for team in teams if team.get("id") is not None}
    deliveries_by_innings: dict[str, list[dict[str, Any]]] = {}
    for delivery in deliveries:
        deliveries_by_innings.setdefault(str(delivery["innings"]), []).append(delivery)

    lineup_teams: dict[str, str] = {}
    for item in _items(fixture.get("lineup")):
        nested_lineup = _mapping(item.get("lineup"))
        player_id = item.get("player_id") or item.get("id") or _reference_id(
            item.get("player") or item.get("batsman") or item.get("bowler")
        )
        player_team_id = item.get("team_id") or nested_lineup.get("team_id")
        if player_id is not None and player_team_id is not None:
            lineup_teams[str(player_id)] = str(player_team_id)

    def validate_fielder(
        player_id: Any,
        batting_team_id: Any,
        innings_label: str,
        role: str,
    ) -> None:
        if player_id is None:
            return
        associated_team = lineup_teams.get(str(player_id))
        if associated_team is None:
            flag(
                f"{role}_unresolved",
                f"{role.replace('_', ' ').capitalize()} ID {player_id} in innings {innings_label} is not present in the fixture lineup.",
            )
        elif batting_team_id is not None and associated_team == str(batting_team_id):
            flag(
                f"{role}_team_mismatch",
                f"{role.replace('_', ' ').capitalize()} ID {player_id} in innings {innings_label} belongs to the batting team.",
            )

    for inning in innings:
        label = str(inning["innings"])
        batting_team_id = inning.get("team_id")
        balls = deliveries_by_innings.get(label, [])
        if batting_team_id is not None and str(batting_team_id) not in team_ids:
            flag("unknown_innings_team", f"Innings {label} references team ID {batting_team_id}, which is not a fixture team.")
        if (
            inning.get("scoreboard_team_id") is not None
            and batting_team_id is not None
            and str(inning["scoreboard_team_id"]) != str(batting_team_id)
        ):
            flag("scoreboard_team_mismatch", f"Innings {label} team ID differs between runs and scoreboards.")
        if (
            inning.get("scoreboard_total") is not None
            and inning.get("runs") is not None
            and inning["scoreboard_total"] != inning["runs"]
        ):
            flag(
                "scoreboard_total_mismatch",
                f"Innings {label} total is {inning['runs']} in runs but {inning['scoreboard_total']} in scoreboards.",
            )
        observed_team_ids = {str(ball["team_id"]) for ball in balls if ball.get("team_id") is not None}
        if len(observed_team_ids) > 1 or (
            batting_team_id is not None
            and observed_team_ids
            and observed_team_ids != {str(batting_team_id)}
        ):
            flag("innings_team_mismatch", f"Innings {label} team ID does not match the ball-by-ball team IDs.")
        if not balls:
            continue

        delivery_runs = sum(ball["runs_total"] for ball in balls)
        if inning.get("runs") is not None and delivery_runs != inning["runs"]:
            flag("innings_total_mismatch", f"Innings {label} total is {inning['runs']} in runs but {delivery_runs} from balls.")
        legal_balls = sum(bool(ball["is_legal"]) for ball in balls)
        computed_rate = round(delivery_runs * 6 / legal_balls, 2) if legal_balls else None
        if inning.get("run_rate") is not None and computed_rate is not None and abs(inning["run_rate"] - computed_rate) > 0.02:
            flag("run_rate_mismatch", f"Innings {label} run rate does not reconcile with {legal_balls} legal balls.")

        card = next((item for item in scorecards if str(item["innings"]) == label), None)
        if card is not None:
            charged_runs = sum(row.get("runs") or 0 for row in card["bowling"])
            extras = card["extras"]
            uncharged_extras = (
                (extras.get("byes") or 0)
                + (extras.get("leg_byes") or 0)
                + (extras.get("penalty") or 0)
            )
            if charged_runs + uncharged_extras != delivery_runs:
                flag(
                    "bowling_runs_mismatch",
                    f"Innings {label} has {delivery_runs} runs, but bowler-charged runs plus byes/leg-byes total {charged_runs + uncharged_extras}.",
                )
            over_totals: dict[str, int] = {}
            for index, ball in enumerate(balls):
                over_label = str(ball.get("over_ball") or "").split(".", 1)[0]
                if not over_label:
                    over_label = str(index // 6)
                over_totals[over_label] = over_totals.get(over_label, 0) + ball["runs_total"]
            if sum(over_totals.values()) != delivery_runs:
                flag("over_total_mismatch", f"Innings {label} over totals do not reconcile with the delivery total.")

        for ball in balls:
            batter_id = ball.get("batter_id")
            bowler_id = ball.get("bowler_id")
            dismissed_batter_id = ball.get("dismissed_batter_id")
            if dismissed_batter_id is not None:
                dismissed_team = lineup_teams.get(str(dismissed_batter_id))
                if dismissed_team is not None and batting_team_id is not None and dismissed_team != str(batting_team_id):
                    flag(
                        "dismissed_batter_team_mismatch",
                        f"Dismissed batter ID {dismissed_batter_id} in innings {label} is not associated with the batting team.",
                    )
            if batter_id is not None and str(batter_id) in lineup_teams:
                if batting_team_id is not None and lineup_teams[str(batter_id)] != str(batting_team_id):
                    flag("batter_team_mismatch", f"Batter ID {batter_id} is not associated with the batting team in innings {label}.")
            if bowler_id is not None and str(bowler_id) in lineup_teams and batting_team_id is not None:
                if lineup_teams[str(bowler_id)] == str(batting_team_id):
                    flag("bowler_team_mismatch", f"Bowler ID {bowler_id} is associated with the batting team in innings {label}.")
            if ball.get("is_wicket"):
                validate_fielder(ball.get("fielder_id"), batting_team_id, label, "fielder")
                validate_fielder(ball.get("runout_by_id"), batting_team_id, label, "runout_by")

    for stat in batting_stats:
        stat_innings = stat.get("innings")
        stat_team_id = stat.get("team_id")
        candidate_cards = [
            card for card in scorecards
            if (
                str(card["innings"]) == str(stat_innings)
                if stat_innings is not None
                else stat_team_id is not None
                and card.get("team_id") is not None
                and str(card["team_id"]) == str(stat_team_id)
            )
        ]
        if len(candidate_cards) != 1:
            continue
        card = candidate_cards[0]
        player = next(
            (row for row in card["batting"] if (
                stat.get("player_id") is not None
                and row.get("player_id") is not None
                and str(row["player_id"]) == str(stat["player_id"])
            ) or row.get("name") == stat.get("name")),
            None,
        )
        if player is None:
            continue
        for field in ("runs", "balls", "fours", "sixes"):
            provider_value = stat.get(field)
            ball_value = player.get(field)
            if provider_value is not None and ball_value is not None and provider_value != ball_value:
                flag(
                    f"batter_{field}_mismatch",
                    f"{stat['name']} in innings {card['innings']}: Sportmonks {field} is {provider_value}, ball-by-ball figures are {ball_value}.",
                )

    for stat in bowling_stats:
        stat_innings = stat.get("innings")
        stat_team_id = stat.get("team_id")
        candidate_cards = [
            card for card in scorecards
            if (
                str(card["innings"]) == str(stat_innings)
                if stat_innings is not None
                else stat_team_id is not None
                and card.get("team_id") is not None
                and str(card["team_id"]) != str(stat_team_id)
            )
        ]
        if len(candidate_cards) != 1:
            continue
        card = candidate_cards[0]
        player = next(
            (row for row in card["bowling"] if (
                stat.get("player_id") is not None
                and row.get("player_id") is not None
                and str(row["player_id"]) == str(stat["player_id"])
            ) or row.get("name") == stat.get("name")),
            None,
        )
        if player is None:
            continue
        label = str(card["innings"])
        for field in ("runs", "wickets", "wides", "no_balls"):
            provider_value = stat.get(field)
            ball_value = player.get(field)
            if provider_value is not None and ball_value is not None and provider_value != ball_value:
                flag(
                    f"bowler_{field}_mismatch",
                    f"{stat['name']} in innings {label}: Sportmonks {field.replace('_', ' ')} is {provider_value}, ball-by-ball figures are {ball_value}.",
                )
        provider_balls = _overs_to_balls(stat.get("overs"))
        ball_balls = player.get("legal_balls")
        if provider_balls is not None and ball_balls is not None and provider_balls != ball_balls:
            flag(
                "bowler_overs_mismatch",
                f"{stat['name']} in innings {label}: Sportmonks overs represent {provider_balls} legal balls, ball-by-ball figures represent {ball_balls}.",
            )

    target = None
    required_runs = None
    required_balls = None
    required_run_rate = None
    current_run_rate = None
    if len(innings) >= 2:
        first_innings = innings[0]
        current = innings[-1]
        if first_innings.get("runs") is not None:
            target = first_innings["runs"] + 1
            required_runs = max(0, target - (current.get("runs") or 0))
            current_balls = sum(bool(ball["is_legal"]) for ball in deliveries_by_innings.get(str(current["innings"]), []))
            current_runs = sum(ball["runs_total"] for ball in deliveries_by_innings.get(str(current["innings"]), []))
            current_run_rate = round(current_runs * 6 / current_balls, 2) if current_balls else None
            innings_limit = _innings_limit(fixture.get("type") or fixture.get("format") or fixture.get("league_type"))
            if innings_limit is not None:
                required_balls = max(0, innings_limit * 6 - current_balls)
                if required_balls:
                    required_run_rate = round(required_runs * 6 / required_balls, 2)

    return {
        "status": "warning" if issues else "ok",
        "issues": issues,
        "target": target,
        "required_runs": required_runs,
        "required_balls": required_balls,
        "required_run_rate": required_run_rate,
        "current_run_rate": current_run_rate,
    }


def _innings(runs: Any) -> list[dict[str, Any]]:
    normalized = []
    for index, item in enumerate(_items(runs), start=1):
        score = _number(item.get("score", item.get("runs")))
        wickets = _number(item.get("wicket", item.get("wickets")))
        overs = item.get("overs")
        extras_data = _mapping(item.get("extras"))
        normalized.append({
            "id": item.get("id"),
            "team_id": item.get("team_id"),
            "innings": _innings_label(item.get("inning") or item.get("scoreboard"), index),
            "runs": score,
            "wickets": wickets,
            "overs": str(overs) if overs is not None else None,
            "run_rate": _rate(score, overs),
            "extras": {
                "wides": _optional_nested_number(item, ("wides", "wide"), extras_data),
                "no_balls": _optional_nested_number(item, ("no_balls", "noballs", "noball_runs"), extras_data),
                "byes": _optional_nested_number(item, ("byes", "bye"), extras_data),
                "leg_byes": _optional_nested_number(item, ("leg_byes", "leg_bye"), extras_data),
                "penalty": _optional_nested_number(item, ("penalty", "penalty_runs"), extras_data),
            },
        })
    return normalized


def _scoreboard_rows(value: Any) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for item in _items(value):
        label = _innings_label(item.get("scoreboard") or item.get("inning"))
        row = grouped.setdefault(label, {
            "innings": label,
            "team_id": item.get("team_id"),
            "total": None,
            "wickets": None,
            "overs": None,
            "extras": {},
        })
        if row.get("team_id") is None:
            row["team_id"] = item.get("team_id")
        kind = str(item.get("type") or "").casefold()
        if kind == "total":
            row["total"] = _number(item.get("total", item.get("score", item.get("runs"))))
            row["wickets"] = _number(item.get("wickets", item.get("wicket")))
            row["overs"] = item.get("overs")
        elif kind == "extra":
            row["extras"] = {
                "wides": _optional_nested_number(item, ("wide", "wides"), {}),
                "no_balls": _optional_nested_number(item, ("noball_runs", "no_balls", "noball"), {}),
                "byes": _optional_nested_number(item, ("bye", "byes"), {}),
                "leg_byes": _optional_nested_number(item, ("leg_bye", "leg_byes"), {}),
                "penalty": _optional_nested_number(item, ("penalty", "penalty_runs"), {}),
            }
    return list(grouped.values())


def _batting_stats(batting: Any) -> list[dict[str, Any]]:
    normalized = []
    for item in _items(batting):
        player = item.get("batsman") or item.get("player")
        score = _number(item.get("score", item.get("runs")))
        balls = _number(item.get("ball", item.get("balls")))
        normalized.append({
            "player_id": item.get("player_id") or item.get("batsman_id") or _reference_id(player),
            "team_id": item.get("team_id"),
            "innings": (
                _innings_label(item.get("inning") or item.get("scoreboard"))
                if item.get("inning") is not None or item.get("scoreboard") is not None else None
            ),
            "name": _name(player) or item.get("name") or "Unknown batter",
            "runs": score,
            "balls": balls,
            "fours": _number(item.get("four_x", item.get("fours"))),
            "sixes": _number(item.get("six_x", item.get("sixes"))),
            "strike_rate": _decimal(item.get("rate", item.get("strike_rate"))),
            "is_current": _flag(item.get("active")),
            "dismissal": item.get("dismissal") or item.get("how_out") or item.get("out"),
        })
    return normalized


def _bowling_stats(bowling: Any) -> list[dict[str, Any]]:
    normalized = []
    for item in _items(bowling):
        player = item.get("bowler") or item.get("player")
        normalized.append({
            "player_id": item.get("player_id") or item.get("bowler_id") or _reference_id(player),
            "team_id": item.get("team_id"),
            "innings": (
                _innings_label(item.get("inning") or item.get("scoreboard"))
                if item.get("inning") is not None or item.get("scoreboard") is not None else None
            ),
            "name": _name(player) or item.get("name") or "Unknown bowler",
            "overs": str(item["overs"]) if item.get("overs") is not None else None,
            "maidens": _number(item.get("medians", item.get("maidens"))),
            "runs": _number(item.get("runs")),
            "wickets": _number(item.get("wickets")),
            "economy": _decimal(item.get("rate", item.get("economy"))),
            "is_current": _flag(item.get("active")),
            "wides": _number(item.get("wides", item.get("wide"))),
            "no_balls": _number(item.get("no_balls", item.get("noballs", item.get("noball")))),
        })
    return normalized


def _deliveries(balls: Any, player_names: dict[str, str] | None = None) -> list[dict[str, Any]]:
    normalized = []
    totals: dict[str, int] = {}
    seen_ids: set[str] = set()
    player_names = player_names or {}

    for index, item in enumerate(_items(balls)):
        score = _mapping(item.get("score"))
        source_id = item.get("id")
        if source_id is not None:
            identity = str(source_id)
            if identity in seen_ids:
                continue
            seen_ids.add(identity)

        innings = _innings_label(item.get("inning") or item.get("scoreboard"))
        extras_data = _mapping(score.get("extras"))
        name_text = " ".join(
            str(score.get(key) or "") for key in ("name", "type", "description", "kind")
        ).lower()
        wide_value = _nested_number(score, ("wide", "wides", "wide_runs"), extras_data)
        noball_value = _nested_number(score, ("noball_runs", "no_ball_runs", "noball", "no_ball"), extras_data)
        bye_value = _nested_number(score, ("bye", "byes"), extras_data)
        leg_bye_value = _nested_number(score, ("leg_bye", "legbyes", "leg_byes"), extras_data)
        penalty_value = _nested_number(score, ("penalty", "penalty_runs"), extras_data)
        is_wide = wide_value > 0 or "wide" in name_text
        is_noball = noball_value > 0 or "no ball" in name_text or "noball" in name_text
        score_runs = _number(score.get("batter_runs", score.get("batsman_runs", score.get("runs")))) or 0
        if is_wide and wide_value == 0:
            wide_value = max(1, score_runs)
            batter_runs = 0
        elif is_noball and noball_value > 0 and re.match(r"^\s*\d+\s+no[\s-]?ball\b", name_text):
            batter_runs = max(0, score_runs - noball_value)
        elif "leg bye" in name_text and leg_bye_value == 0:
            leg_bye_value = score_runs
            batter_runs = 0
        elif "bye" in name_text and bye_value == 0:
            bye_value = score_runs
            batter_runs = 0
        else:
            batter_runs = score_runs
        if is_noball and noball_value == 0:
            noball_value = 1
        runs_value = _number(score.get("total", score.get("total_runs")))
        runs = (
            runs_value
            if runs_value is not None
            else batter_runs + wide_value + noball_value + bye_value + leg_bye_value + penalty_value
        )
        extras = wide_value + noball_value + bye_value + leg_bye_value + penalty_value
        bowler_runs = _number(score.get("bowler_runs"))
        if bowler_runs is None:
            bowler_runs = batter_runs + wide_value + noball_value
        is_wicket = _flag(score.get("is_wicket")) or _flag(item.get("is_wicket"))
        dismissal_value = (
            score.get("name") or score.get("dismissal") or score.get("description")
            or item.get("dismissal") or item.get("how_out")
        )
        is_retired_hurt = "retired hurt" in name_text
        if is_retired_hurt:
            is_wicket = False
        batter_id = item.get("batsman_id") or item.get("batter_id") or _reference_id(item.get("batsman"))
        bowler_id = item.get("bowler_id") or _reference_id(item.get("bowler"))
        non_striker = item.get("non_striker") or item.get("nonstriker")
        non_striker_id = (
            item.get("non_striker_id") or item.get("nonstriker_id") or _reference_id(non_striker)
        )
        batter_name = _resolved_name(item.get("batsman") or item.get("batter"), batter_id, player_names, "batter")
        bowler_name = _resolved_name(item.get("bowler"), bowler_id, player_names, "bowler")
        non_striker_name = _resolved_name(non_striker, non_striker_id, player_names, "non-striker")
        fielder_id = (
            item.get("catchstump_id") or item.get("catch_stump_id")
            or item.get("catch_stump_player_id") or score.get("catchstump_id")
            or score.get("catch_stump_id") or score.get("catch_stump_player_id")
        )
        runner_id = item.get("runout_by_id") or score.get("runout_by_id")
        dismissed_batter_id = (
            item.get("batsmanout_id") or item.get("dismissed_batter_id")
            or score.get("batsmanout_id") or score.get("dismissed_batter_id")
        )
        fielder_name = _resolved_name(
            item.get("catchstump") or item.get("catch_stump") or score.get("catchstump"),
            fielder_id,
            player_names,
            "fielder",
        )
        runner_name = _resolved_name(
            item.get("runout_by") or score.get("runout_by"),
            runner_id,
            player_names,
            "run-out fielder",
        )
        dismissal = (
            _dismissal_detail(
                dismissal_value,
                bowler_name,
                fielder_name,
                runner_name,
            )
            if (is_wicket or is_retired_hurt) else None
        )
        total = totals.get(innings, 0) + runs
        totals[innings] = total
        over_ball = str(item.get("ball")) if item.get("ball") is not None else None
        legal_source = score.get("ball", score.get("is_legal", item.get("is_legal")))
        is_legal = _flag(legal_source) if legal_source is not None else not (is_wide or is_noball)
        if is_wide or is_noball:
            is_legal = False
        normalized.append({
            "source_id": source_id if source_id is not None else index,
            "over_ball": over_ball,
            "innings": innings,
            "team_id": item.get("team_id"),
            "batter_id": batter_id,
            "non_striker_id": non_striker_id,
            "bowler_id": bowler_id,
            "dismissed_batter_id": dismissed_batter_id,
            "dismissed_batter": _resolved_name(
                item.get("batsmanout") or item.get("dismissed_batter") or score.get("batsmanout"),
                dismissed_batter_id,
                player_names,
                "dismissed batter",
            ),
            "fielder_id": fielder_id,
            "fielder": fielder_name,
            "runout_by_id": runner_id,
            "runout_by": runner_name,
            "batter": batter_name,
            "non_striker": non_striker_name,
            "bowler": bowler_name,
            "runs_batter": batter_runs,
            "runs_total": runs,
            "runs_extras": extras,
            "runs_bowler": bowler_runs,
            "is_wide": is_wide,
            "is_noball": is_noball,
            "extras": {
                "wides": wide_value,
                "no_balls": noball_value,
                "byes": bye_value,
                "leg_byes": leg_bye_value,
                "penalty": penalty_value,
            },
            "innings_runs": total,
            "four": _flag(score.get("four")) or _number(score.get("four")) == 1,
            "six": _flag(score.get("six")) or _number(score.get("six")) == 1,
            "is_wicket": is_wicket,
            "bowler_wicket": is_wicket and not any(
                kind in str(dismissal or "").lower()
                for kind in ("run out", "retired hurt", "retired out", "obstructing")
            ),
            "is_retired_hurt": is_retired_hurt,
            "is_legal": is_legal,
            "dismissal": dismissal,
        })

    return normalized


def _scorecards(
    innings: list[dict[str, Any]],
    deliveries: list[dict[str, Any]],
    lineup: Any,
    batting_stats: list[dict[str, Any]],
    bowling_stats: list[dict[str, Any]],
    powerplay_legal_balls: int | None = None,
    powerplay_boundaries: dict[str, int | None] | None = None,
    current_innings_label: str | None = None,
    is_finished: bool = False,
    innings_limit: int | None = None,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for delivery in deliveries:
        grouped.setdefault(delivery["innings"], []).append(delivery)
    roster = []
    for item in _items(lineup):
        player = item.get("player") or item.get("batsman") or item.get("bowler") or item
        player_id = item.get("player_id") or _reference_id(player)
        name = _name(player) or item.get("fullname") or item.get("name")
        if player_id is not None and name:
            lineup_data = _mapping(item.get("lineup"))
            roster.append({
                "player_id": player_id,
                "name": str(name),
                "team_id": item.get("team_id") or lineup_data.get("team_id") or _reference_id(item.get("team")),
            })

    scorecards = []
    team_ids = {str(item.get("team_id")) for item in innings if item.get("team_id") is not None}
    for innings_data in innings:
        label = str(innings_data["innings"])
        balls = grouped.get(label, [])
        team_id = innings_data.get("team_id") or next(
            (ball.get("team_id") for ball in balls if ball.get("team_id") is not None), None
        )

        def stat_belongs_to_innings(player: dict[str, Any], is_batting: bool) -> bool:
            if player.get("innings") is not None:
                return str(player["innings"]) == label
            player_team = player.get("team_id")
            if player_team is not None and team_id is not None:
                if is_batting:
                    return str(player_team) == str(team_id)
                return len(team_ids) == 2 and str(player_team) != str(team_id)
            return len(innings) == 1

        batters: dict[str, dict[str, Any]] = {}
        bowlers: dict[str, dict[str, Any]] = {}
        extras = {"wides": 0, "no_balls": 0, "byes": 0, "leg_byes": 0, "penalty": 0}
        fall_of_wickets = []
        over_figures: dict[tuple[str, str], dict[str, int]] = {}
        partnership_runs = 0
        partnership_balls = 0
        partnership_contributions: dict[str, dict[str, Any]] = {}
        partnerships = []

        legal_balls_in_innings = sum(int(bool(ball["is_legal"])) for ball in balls)
        wickets_in_innings = sum(int(bool(ball["is_wicket"])) for ball in balls)
        innings_powerplay_balls = (
            powerplay_boundaries.get(label, powerplay_legal_balls)
            if powerplay_boundaries is not None
            else powerplay_legal_balls
        )
        current_partnership_active = (
            label == current_innings_label
            and not is_finished
            and wickets_in_innings < 10
            and (innings_limit is None or legal_balls_in_innings < innings_limit * 6)
        )

        def partnership_record(
            *,
            wicket: int | None = None,
            completed: bool = True,
            current: bool = False,
        ) -> dict[str, Any]:
            contributions = list(partnership_contributions.values())
            return {
                "number": len(partnerships) + 1,
                "wicket": wicket,
                "completed": completed,
                "current": current,
                "batters": [item["name"] for item in contributions],
                "runs": partnership_runs,
                "balls": partnership_balls,
                "contributions": [
                    {
                        "player_id": item["player_id"],
                        "name": item["name"],
                        "runs": item["runs"],
                        "balls": item["balls"],
                    }
                    for item in contributions
                ],
            }

        def batter_row(player_id: Any, name: str | None) -> dict[str, Any]:
            key = str(player_id) if player_id is not None else f"name:{name or 'Unknown'}"
            if key not in batters:
                batters[key] = {
                    "player_id": player_id,
                    "name": name or "Unknown batter",
                    "dismissal": None,
                    "runs": 0,
                    "balls": 0,
                    "fours": 0,
                    "sixes": 0,
                    "strike_rate": None,
                    "is_current": True,
                    "current_role": None,
                }
            return batters[key]

        def bowler_row(player_id: Any, name: str | None) -> dict[str, Any]:
            key = str(player_id) if player_id is not None else f"name:{name or 'Unknown'}"
            if key not in bowlers:
                bowlers[key] = {
                    "player_id": player_id,
                    "name": name or "Unknown bowler",
                    "legal_balls": 0,
                    "overs": "0.0",
                    "maidens": 0,
                    "runs": 0,
                    "wickets": 0,
                    "economy": None,
                    "wides": 0,
                    "no_balls": 0,
                    "_overs": {},
                }
            return bowlers[key]

        for index, delivery in enumerate(balls):
            batter = batter_row(delivery.get("batter_id"), delivery.get("batter"))
            if delivery.get("non_striker_id") is not None or delivery.get("non_striker"):
                non_striker = batter_row(delivery.get("non_striker_id"), delivery.get("non_striker"))
                non_striker_key = (
                    str(non_striker["player_id"])
                    if non_striker["player_id"] is not None
                    else f"name:{non_striker['name']}"
                )
                partnership_contributions.setdefault(non_striker_key, {
                    "player_id": non_striker["player_id"],
                    "name": non_striker["name"],
                    "runs": 0,
                    "balls": 0,
                })
            bowler = bowler_row(delivery.get("bowler_id"), delivery.get("bowler"))
            batter["runs"] += delivery["runs_batter"] or 0
            batter["balls"] += int(not delivery["is_wide"])
            batter["fours"] += int(delivery["four"])
            batter["sixes"] += int(delivery["six"])
            if delivery.get("is_retired_hurt"):
                batter["dismissal"] = delivery.get("dismissal")
            if delivery.get("is_wicket") and delivery.get("dismissed_batter_id") is not None:
                out_row = batter_row(delivery["dismissed_batter_id"], delivery.get("dismissed_batter"))
                out_row["dismissal"] = delivery.get("dismissal")
            elif delivery.get("is_wicket"):
                batter["dismissal"] = delivery.get("dismissal")

            bowler["runs"] += delivery["runs_bowler"] or 0
            bowler["legal_balls"] += int(bool(delivery["is_legal"]))
            bowler["wickets"] += int(bool(delivery["bowler_wicket"]))
            delivery_extras = delivery.get("extras") or {}
            for key in extras:
                value = delivery_extras.get(key, 0)
                extras[key] += value
                if key in {"wides", "no_balls"}:
                    bowler[key] += value

            over_label = (delivery.get("over_ball") or "").split(".", 1)[0]
            if not over_label:
                over_label = str((index // 6) + 1)
            over_key = (str(delivery.get("bowler_id") or delivery.get("bowler")), over_label)
            over = over_figures.setdefault(over_key, {"legal_balls": 0, "runs": 0})
            over["legal_balls"] += int(bool(delivery["is_legal"]))
            over["runs"] += delivery["runs_bowler"] or 0

            partnership_runs += delivery["runs_total"]
            partnership_balls += int(not delivery["is_wide"])
            contribution_key = (
                str(delivery.get("batter_id"))
                if delivery.get("batter_id") is not None
                else f"name:{delivery.get('batter') or 'Unknown'}"
            )
            contribution = partnership_contributions.setdefault(contribution_key, {
                "player_id": delivery.get("batter_id"),
                "name": delivery.get("batter") or "Unknown batter",
                "runs": 0,
                "balls": 0,
            })
            contribution["runs"] += delivery["runs_batter"] or 0
            contribution["balls"] += int(not delivery["is_wide"])
            if delivery.get("is_retired_hurt") and not delivery.get("is_wicket"):
                if partnership_contributions:
                    partnerships.append(partnership_record(completed=True))
                partnership_runs = 0
                partnership_balls = 0
                partnership_contributions = {}
            if delivery.get("is_wicket"):
                if partnership_contributions:
                    partnerships.append(partnership_record(wicket=len(fall_of_wickets) + 1, completed=True))
                dismissed_id = delivery.get("dismissed_batter_id")
                if dismissed_id is not None:
                    batter_row(dismissed_id, delivery.get("dismissed_batter"))["dismissal"] = delivery.get("dismissal")
                else:
                    batter["dismissal"] = delivery.get("dismissal")
                fall_of_wickets.append({
                    "wicket": len(fall_of_wickets) + 1,
                    "score": delivery["innings_runs"],
                    "over": delivery.get("over_ball"),
                    "batter": delivery.get("dismissed_batter") or delivery.get("batter"),
                    "batter_id": delivery.get("dismissed_batter_id") or delivery.get("batter_id"),
                    "dismissal": delivery.get("dismissal"),
                    "fielder_id": delivery.get("fielder_id"),
                    "fielder": delivery.get("fielder"),
                    "bowler_id": delivery.get("bowler_id"),
                    "bowler": delivery.get("bowler"),
                    "runout_by_id": delivery.get("runout_by_id"),
                    "runout_by": delivery.get("runout_by"),
                })
                partnership_runs = 0
                partnership_balls = 0
                partnership_contributions = {}

        for row in batters.values():
            row["strike_rate"] = round(row["runs"] * 100 / row["balls"], 2) if row["balls"] else None
        for row in bowlers.values():
            row["overs"] = f"{row['legal_balls'] // 6}.{row['legal_balls'] % 6}"
            row["economy"] = round(row["runs"] * 6 / row["legal_balls"], 2) if row["legal_balls"] else None
            for (bowler_key, _), over in over_figures.items():
                if bowler_key == str(row["player_id"] or row["name"]) and over["legal_balls"] == 6 and over["runs"] == 0:
                    row["maidens"] += 1

        if balls:
            for player in batting_stats:
                if not player.get("is_current") or not stat_belongs_to_innings(player, True):
                    continue
                key = (
                    str(player["player_id"])
                    if player.get("player_id") is not None
                    else f"name:{player['name']}"
                )
                if key not in batters:
                    batters[key] = {
                        "player_id": player.get("player_id"),
                        "name": player["name"],
                        "dismissal": player.get("dismissal"),
                        "runs": player.get("runs"),
                        "balls": player.get("balls"),
                        "fours": player.get("fours"),
                        "sixes": player.get("sixes"),
                        "strike_rate": player.get("strike_rate"),
                        "is_current": player.get("is_current", False),
                        "current_role": None,
                    }
            latest = balls[-1]
            current_batter_ids = {
                str(player_id) if player_id is not None else f"name:{name or 'Unknown'}":
                role
                for player_id, name, role in (
                    (latest.get("batter_id"), latest.get("batter"), "striker"),
                    (latest.get("non_striker_id"), latest.get("non_striker"), "non_striker"),
                )
            }
            if (
                latest.get("non_striker_id") is None
                and not latest.get("non_striker")
                and not latest.get("is_wicket")
                and not latest.get("is_retired_hurt")
            ):
                striker_key = (
                    str(latest["batter_id"])
                    if latest.get("batter_id") is not None
                    else f"name:{latest.get('batter') or 'Unknown'}"
                )
                active_batter_keys = {
                    key for key, row in batters.items()
                    if row.get("dismissal") is None
                    and (row.get("is_current") or (row.get("balls") or 0) > 0)
                }
                if len(active_batter_keys) == 2 and striker_key in active_batter_keys:
                    current_batter_ids = {
                        striker_key: "striker",
                        next(iter(active_batter_keys - {striker_key})): "non_striker",
                    }
            if latest.get("is_wicket") or latest.get("is_retired_hurt"):
                dismissed_id = latest.get("dismissed_batter_id") or latest.get("batter_id")
                dismissed_name = latest.get("dismissed_batter") or latest.get("batter")
                current_batter_ids.pop(
                    str(dismissed_id) if dismissed_id is not None else f"name:{dismissed_name or 'Unknown'}",
                    None,
                )
                current_batter_ids = {key: None for key in current_batter_ids}
            for key, row in batters.items():
                row["current_role"] = current_batter_ids.get(key)
                row["is_current"] = key in current_batter_ids
            partnership = None
            pair = [name for name in (latest.get("batter"), latest.get("non_striker")) if name]
            if len(pair) == 2 and not latest.get("is_wicket") and not latest.get("is_retired_hurt"):
                active_contributions = [
                    item for item in partnership_contributions.values()
                    if item["name"] in pair
                ]
                if active_contributions:
                    partnership = partnership_record(
                        completed=not current_partnership_active,
                        current=current_partnership_active,
                    )
            if partnership_contributions and not latest.get("is_wicket") and not latest.get("is_retired_hurt"):
                partnerships.append(partnership or partnership_record(
                    completed=not current_partnership_active,
                    current=current_partnership_active,
                ))
        else:
            partnership = None

        if balls and innings_data.get("extras"):
            extras["penalty"] = innings_data["extras"].get("penalty") or 0
        inning_runs = sum(ball["runs_total"] for ball in balls)
        wickets = sum(int(bool(ball["is_wicket"])) for ball in balls)
        legal_balls = sum(int(bool(ball["is_legal"])) for ball in balls)
        powerplay = None
        if innings_powerplay_balls is not None:
            powerplay_runs = 0
            powerplay_wickets = 0
            powerplay_balls = 0
            for delivery in balls:
                if powerplay_balls >= innings_powerplay_balls:
                    break
                powerplay_runs += delivery["runs_total"]
                powerplay_wickets += int(bool(delivery["is_wicket"]))
                powerplay_balls += int(bool(delivery["is_legal"]))
            whole_overs, remaining_powerplay_balls = divmod(innings_powerplay_balls, 6)
            overs_label = (
                f"1-{whole_overs}"
                if remaining_powerplay_balls == 0 and whole_overs > 0
                else None
            )
            description = (
                f"First {whole_overs} overs"
                if remaining_powerplay_balls == 0
                else f"First {innings_powerplay_balls} legal balls"
            )
            powerplay = {
                "label": description,
                "overs": overs_label,
                "boundary_legal_balls": innings_powerplay_balls,
                "runs": powerplay_runs,
                "wickets": powerplay_wickets,
                "legal_balls": powerplay_balls,
                "run_rate": round(powerplay_runs * 6 / powerplay_balls, 2) if powerplay_balls else None,
                "complete": powerplay_balls >= innings_powerplay_balls,
            }
        if not balls:
            for player in batting_stats:
                if stat_belongs_to_innings(player, True):
                    batters[str(player.get("player_id") or player["name"])] = {
                        "player_id": player.get("player_id"),
                        "name": player["name"],
                        "dismissal": player.get("dismissal"),
                        "runs": player.get("runs"),
                        "balls": player.get("balls"),
                        "fours": player.get("fours"),
                        "sixes": player.get("sixes"),
                        "strike_rate": player.get("strike_rate"),
                        "is_current": player.get("is_current", False),
                        "current_role": None,
                    }
            for player in bowling_stats:
                if stat_belongs_to_innings(player, False):
                    bowlers[str(player.get("player_id") or player["name"])] = {
                        "player_id": player.get("player_id"),
                        "name": player["name"],
                        "legal_balls": _overs_to_balls(player.get("overs")),
                        "overs": player.get("overs"),
                        "maidens": player.get("maidens"),
                        "runs": player.get("runs"),
                        "wickets": player.get("wickets"),
                        "economy": player.get("economy"),
                        "wides": player.get("wides"),
                        "no_balls": player.get("no_balls"),
                    }
            if innings_data.get("extras"):
                extras = {
                    key: innings_data["extras"].get(key)
                    for key in extras
                }
            legal_balls = _overs_to_balls(innings_data.get("overs"))
        batting_ids = {str(row["player_id"]) for row in batters.values() if row["player_id"] is not None}
        did_not_bat = [
            {"player_id": player["player_id"], "name": player["name"]}
            for player in roster
            if str(player.get("team_id")) == str(team_id)
            and str(player["player_id"]) not in batting_ids
        ] if team_id is not None else []
        scorecards.append({
            "id": innings_data.get("id"),
            "team_id": team_id,
            "innings": label,
            "runs": inning_runs if balls else innings_data.get("runs"),
            "wickets": wickets if balls else innings_data.get("wickets"),
            "legal_balls": legal_balls,
            "overs": f"{legal_balls // 6}.{legal_balls % 6}" if balls else innings_data.get("overs"),
            "run_rate": round(inning_runs * 6 / legal_balls, 2) if balls and legal_balls
            else innings_data.get("run_rate"),
            "extras": extras,
            "batting": list(batters.values()),
            "bowling": [
                {key: value for key, value in row.items() if not key.startswith("_")}
                for row in bowlers.values()
            ],
            "fall_of_wickets": fall_of_wickets,
            "did_not_bat": did_not_bat,
            "partnership": partnership,
            "partnerships": partnerships,
            "powerplay": powerplay,
        })
    return scorecards


def normalize_fixture(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize the common Sportmonks fixture envelope without inventing missing values."""
    fixture: Any = payload.get("data", payload)
    if isinstance(fixture, list):
        fixture = fixture[0] if fixture else {}
    fixture = _mapping(fixture)
    player_names = _player_name_map(fixture)
    innings = _innings(fixture.get("runs"))
    scoreboard_rows = _scoreboard_rows(fixture.get("scoreboards"))
    for scoreboard in scoreboard_rows:
        inning = next((item for item in innings if str(item["innings"]) == scoreboard["innings"]), None)
        if inning is None:
            if scoreboard["total"] is None:
                continue
            inning = {
                "id": None,
                "team_id": scoreboard["team_id"],
                "innings": scoreboard["innings"],
                "runs": scoreboard["total"],
                "wickets": scoreboard["wickets"],
                "overs": str(scoreboard["overs"]) if scoreboard["overs"] is not None else None,
                "run_rate": _rate(scoreboard["total"], scoreboard["overs"]),
                "extras": scoreboard["extras"],
            }
            innings.append(inning)
        else:
            inning["scoreboard_team_id"] = scoreboard["team_id"]
            inning["scoreboard_total"] = scoreboard["total"]
            if inning.get("runs") is None:
                inning["runs"] = scoreboard["total"]
            if inning.get("wickets") is None:
                inning["wickets"] = scoreboard["wickets"]
            if inning.get("overs") is None and scoreboard["overs"] is not None:
                inning["overs"] = str(scoreboard["overs"])
                inning["run_rate"] = _rate(inning.get("runs"), inning["overs"])
            inning["extras"] = {
                key: value if value is not None else scoreboard["extras"].get(key)
                for key, value in inning.get("extras", {}).items()
            }
    batting = _batting_stats(fixture.get("batting"))
    bowling = _bowling_stats(fixture.get("bowling"))
    deliveries = _deliveries(fixture.get("balls"), player_names)
    for label in dict.fromkeys(delivery["innings"] for delivery in deliveries):
        if not any(str(item["innings"]) == label for item in innings):
            innings.append({
                "id": None,
                "team_id": next(
                    (delivery.get("team_id") for delivery in deliveries
                     if delivery["innings"] == label and delivery.get("team_id") is not None),
                    None,
                ),
                "innings": label,
                "runs": None,
                "wickets": None,
                "overs": None,
                "run_rate": None,
            })
    match_format = fixture.get("type") or fixture.get("format") or fixture.get("league_type")
    if isinstance(match_format, dict):
        match_format = _name(match_format) or match_format.get("name")
    match_format = str(match_format).strip() if match_format else None
    format_text = (match_format or "").casefold()
    innings_limit = _innings_limit(match_format)
    powerplay_legal_balls = (
        36 if re.search(r"\bt20i?\b", format_text)
        else 12 if re.search(r"\bt10\b", format_text)
        else 60 if "odi" in format_text or "list a" in format_text
        else None
    )
    powerplay_boundaries = _powerplay_boundaries(fixture, innings, powerplay_legal_balls)
    status = fixture.get("status")
    status_text = str(status or fixture.get("note") or "").lower()
    is_finished = any(
        term in status_text
        for term in ("finished", "complete", "result", "abandoned", "aban.", "cancelled", "cancl.")
    )
    current_innings_label = str(innings[-1]["innings"]) if innings else None
    scorecards = _scorecards(
        innings,
        deliveries,
        fixture.get("lineup"),
        batting,
        bowling,
        powerplay_legal_balls,
        powerplay_boundaries,
        current_innings_label,
        is_finished,
        innings_limit,
    )
    validation = _validate_fixture(fixture, innings, scorecards, batting, bowling, deliveries)
    scorecard_by_innings = {str(item["innings"]): item for item in scorecards}
    for item in innings:
        scorecard = scorecard_by_innings.get(str(item["innings"]))
        if scorecard and scorecard["legal_balls"] is not None:
            item.update({
                "team_id": scorecard["team_id"],
                "runs": scorecard["runs"],
                "wickets": scorecard["wickets"],
                "legal_balls": scorecard["legal_balls"],
                "overs": scorecard["overs"],
                "run_rate": scorecard["run_rate"],
            })

    progression = []
    momentum: dict[tuple[str, str], int] = {}
    for delivery in deliveries:
        label = delivery["over_ball"] or str(len(progression) + 1)
        progression.append({
            "innings": delivery["innings"],
            "over_ball": label,
            "runs": delivery["innings_runs"],
        })
        over = label.split(".", 1)[0]
        key = (delivery["innings"], over)
        momentum[key] = momentum.get(key, 0) + delivery["runs_total"]

    momentum_points = [
        {"innings": innings_label, "over": str(int(over) + 1) if over.isdigit() else over, "runs": runs}
        for (innings_label, over), runs in momentum.items()
    ]
    current_innings_label = str(innings[-1]["innings"]) if innings else None
    latest_delivery = next(
        (delivery for delivery in reversed(deliveries) if delivery["innings"] == current_innings_label),
        None,
    )
    current_scorecard = scorecard_by_innings.get(current_innings_label or "")
    current_batters = [
        {key: value for key, value in player.items() if key != "dismissal"}
        for player in (current_scorecard or {}).get("batting", [])
        if player["is_current"]
    ]
    if not current_batters and latest_delivery is None:
        current_team_id = innings[-1].get("team_id") if innings else None
        current_batters = [
            player for player in batting
            if player["is_current"] and (
                str(player.get("innings")) == current_innings_label
                or (
                    player.get("innings") is None
                    and player.get("team_id") is not None
                    and str(player["team_id"]) == str(current_team_id)
                )
                or (
                    len(innings) == 1
                    and player.get("innings") is None
                    and player.get("team_id") is None
                )
            )
        ]
    current_bowler = None
    if latest_delivery is not None:
        current_bowler = next(
            (player for player in (current_scorecard or {}).get("bowling", [])
             if (
                 str(player.get("player_id")) == str(latest_delivery.get("bowler_id"))
                 if latest_delivery.get("bowler_id") is not None
                 else player.get("name") == latest_delivery.get("bowler")
             )),
            None,
        )
        if current_bowler is not None:
            current_bowler = {**current_bowler, "is_current": True}
    if current_bowler is None and latest_delivery is None:
        current_bowler = next((
            player for player in bowling
            if player["is_current"] and (
                str(player.get("innings")) == current_innings_label
                or (
                    len(innings) == 1
                    and player.get("innings") is None
                    and player.get("team_id") is None
                )
            )
        ), None)

    if is_finished:
        for player in batting + bowling:
            player["is_current"] = False
        for scorecard in scorecards:
            for player in scorecard["batting"]:
                player["is_current"] = False
                player["current_role"] = None
        current_batters = []
        current_bowler = None

    return {
        "id": fixture.get("id"),
        "match_format": match_format,
        "status": status,
        "note": fixture.get("note"),
        "is_finished": is_finished,
        "localteam": _team(fixture.get("localteam")),
        "visitorteam": _team(fixture.get("visitorteam")),
        "innings": innings,
        "batting": batting,
        "bowling": bowling,
        "scorecards": scorecards,
        "current_batters": current_batters,
        "current_bowler": current_bowler,
        "deliveries": deliveries,
        "validation": validation,
        "progression": progression,
        "momentum": momentum_points,
    }


def normalize_live_matches(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data", [])
    if not isinstance(data, list):
        return []

    matches = []
    for item in data:
        if not isinstance(item, dict):
            continue
        normalized = normalize_fixture(item)
        current_innings = normalized["innings"][-1] if normalized["innings"] else {}
        team_scores: dict[str, dict[str, Any]] = {}
        for innings in normalized["innings"]:
            if innings.get("team_id") is not None:
                team_scores[str(innings["team_id"])] = {
                    "runs": innings.get("runs"),
                    "wickets": innings.get("wickets"),
                    "overs": innings.get("overs"),
                    "run_rate": innings.get("run_rate"),
                }

        def score_for(team: dict[str, Any] | None) -> dict[str, Any] | None:
            if team is None or team.get("id") is None:
                return None
            return team_scores.get(str(team["id"]))

        matches.append({
            "id": normalized["id"],
            "status": normalized["status"],
            "note": normalized["note"],
            "localteam": normalized["localteam"],
            "visitorteam": normalized["visitorteam"],
            "localteam_score": score_for(normalized["localteam"]),
            "visitorteam_score": score_for(normalized["visitorteam"]),
            "score": {
                "runs": current_innings.get("runs"),
                "wickets": current_innings.get("wickets"),
                "overs": current_innings.get("overs"),
                "run_rate": current_innings.get("run_rate"),
            },
        })
    return matches
