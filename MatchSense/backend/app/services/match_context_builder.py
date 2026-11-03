import re
from typing import Any


def _number(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _key(value: Any, name: Any = None) -> str:
    return str(value) if value is not None else f"name:{name or 'Unknown player'}"


def _rate(runs: int, balls: int) -> float:
    return round(runs * 100 / balls, 2) if balls else 0.0


def _economy(runs: int, balls: int) -> float:
    return round(runs * 6 / balls, 2) if balls else 0.0


def _over_number(delivery: dict[str, Any], index: int, legal_balls_before: int) -> str:
    label = str(delivery.get("over_ball") or "")
    match = re.match(r"^(\d+)\.", label)
    if match:
        return str(int(match.group(1)) + 1)
    return str(legal_balls_before // 6 + 1)


def _narrative_facts(
    delivery_history: list[dict[str, Any]],
    match_format: Any,
) -> dict[str, Any]:
    bowler_overs: dict[tuple[str, str, str], dict[str, Any]] = {}
    matchups: dict[tuple[str, str, str], dict[str, Any]] = {}
    batter_overs: dict[tuple[str, str, str], dict[str, Any]] = {}
    for delivery in delivery_history:
        innings = str(delivery["innings"])
        over = str(delivery["over"])
        batter_key = _key(delivery.get("batter_id"), delivery.get("batter"))
        bowler_key = _key(delivery.get("bowler_id"), delivery.get("bowler"))
        over_key = (innings, bowler_key, over)
        spell_over = bowler_overs.setdefault(over_key, {
            "innings": innings,
            "over": over,
            "bowler": delivery.get("bowler"),
            "runs": 0,
            "legal_balls": 0,
            "wickets": 0,
            "dots": 0,
            "boundaries": 0,
            "extras": 0,
            "batters": {},
            "entry_score": delivery["score_before"],
            "entry_wickets": delivery["wickets_before"],
            "events": [],
        })
        spell_over["runs"] += delivery["bowler_runs"]
        spell_over["legal_balls"] += int(delivery["is_legal"])
        spell_over["wickets"] += int(delivery["bowler_wicket"])
        spell_over["dots"] += int(delivery["is_legal"] and delivery["total_runs"] == 0)
        spell_over["boundaries"] += int(delivery["four"] or delivery["six"])
        spell_over["extras"] += delivery["extras"]
        batter_name = delivery.get("batter") or "Batter unavailable"
        spell_over["batters"][batter_name] = spell_over["batters"].get(batter_name, 0) + delivery["batter_runs"]
        if delivery["wicket"] or delivery["four"] or delivery["six"]:
            spell_over["events"].append({
                "ball": delivery["over_ball"],
                "batter": delivery["dismissed_batter"] if delivery["wicket"] else batter_name,
                "runs": delivery["total_runs"],
                "wicket": delivery["wicket"],
                "dismissal": delivery["dismissal"],
                "boundary_runs": 6 if delivery["six"] else 4 if delivery["four"] else 0,
            })

        matchup_key = (innings, bowler_key, batter_key)
        matchup = matchups.setdefault(matchup_key, {
            "innings": innings,
            "bowler": delivery.get("bowler"),
            "batter": batter_name,
            "runs_conceded": 0,
            "batter_runs": 0,
            "legal_balls": 0,
            "boundaries": 0,
            "wickets": 0,
        })
        matchup["runs_conceded"] += delivery["bowler_runs"]
        matchup["batter_runs"] += delivery["batter_runs"]
        matchup["legal_balls"] += int(delivery["is_legal"])
        matchup["boundaries"] += int(delivery["four"] or delivery["six"])
        matchup["wickets"] += int(delivery["bowler_wicket"])

        batter_over_key = (innings, batter_key, over)
        batter_over = batter_overs.setdefault(batter_over_key, {
            "innings": innings,
            "over": over,
            "batter": batter_name,
            "runs": 0,
            "balls": 0,
            "boundaries": 0,
        })
        batter_over["runs"] += delivery["batter_runs"]
        batter_over["balls"] += int(delivery["batter_faced_ball"])
        batter_over["boundaries"] += int(delivery["four"] or delivery["six"])

    is_t20 = "t20" in str(match_format or "").casefold()
    phase_totals: dict[str, Any] = {"innings": {}, "batters": {}, "bowlers": {}}
    if is_t20:
        phase_ranges = (("Powerplay", 1, 6), ("Middle", 7, 15), ("Death", 16, 20))
        for label in dict.fromkeys(row["innings"] for row in delivery_history):
            phase_totals["innings"][label] = {}
            for phase, start, end in phase_ranges:
                deliveries = [
                    row for row in delivery_history
                    if row["innings"] == label and start <= int(row["over"]) <= end
                ]
                if deliveries:
                    phase_totals["innings"][label][phase] = {
                        "runs": sum(row["total_runs"] for row in deliveries),
                        "wickets": sum(int(row["wicket"]) for row in deliveries),
                        "legal_balls": sum(int(row["is_legal"]) for row in deliveries),
                        "dots": sum(int(row["is_legal"] and row["total_runs"] == 0) for row in deliveries),
                        "boundaries": sum(int(row["four"] or row["six"]) for row in deliveries),
                    }
        for row in batter_overs.values():
            phase_name = next(
                (phase for phase, start, end in phase_ranges if start <= int(row["over"]) <= end),
                None,
            )
            if phase_name is None:
                continue
            key = (row["innings"], row["batter"], phase_name)
            phase = phase_totals["batters"].setdefault(key, {
                "innings": row["innings"],
                "batter": row["batter"],
                "phase": phase_name,
                "runs": 0,
                "balls": 0,
                "boundaries": 0,
                "overs": [],
            })
            phase["runs"] += row["runs"]
            phase["balls"] += row["balls"]
            phase["boundaries"] += row["boundaries"]
            phase["overs"].append(row["over"])
        for row in bowler_overs.values():
            row["phase"] = next(
                (phase for phase, start, end in phase_ranges if start <= int(row["over"]) <= end),
                None,
            )
            if row["phase"] is None:
                continue
            key = (row["innings"], _key(None, row["bowler"]), row["phase"])
            phase = phase_totals["bowlers"].setdefault(key, {
                "innings": row["innings"],
                "bowler": row["bowler"],
                "phase": row["phase"],
                "runs": 0,
                "legal_balls": 0,
                "wickets": 0,
                "dots": 0,
                "boundaries": 0,
                "extras": 0,
                "overs": [],
            })
            phase["runs"] += row["runs"]
            phase["legal_balls"] += row["legal_balls"]
            phase["wickets"] += row["wickets"]
            phase["dots"] += row["dots"]
            phase["boundaries"] += row["boundaries"]
            phase["extras"] += row["extras"]
            phase["overs"].append(row["over"])
        for innings_phases in phase_totals["innings"].values():
            for phase in innings_phases.values():
                phase["run_rate"] = _economy(phase["runs"], phase["legal_balls"])
        for phase in phase_totals["batters"].values():
            phase["strike_rate"] = _rate(phase["runs"], phase["balls"])
        for phase in phase_totals["bowlers"].values():
            phase["economy"] = _economy(phase["runs"], phase["legal_balls"])

    return {
        "bowler_overs": list(bowler_overs.values()),
        "batter_overs": list(batter_overs.values()),
        "matchups": list(matchups.values()),
        "phase_totals": {
            "innings": phase_totals["innings"],
            "batters": list(phase_totals["batters"].values()),
            "bowlers": list(phase_totals["bowlers"].values()),
        },
        "phase_definitions_available": is_t20,
    }


def build_match_context(state: dict[str, Any]) -> dict[str, Any]:
    """Build scorecards and evidence only from deliveries processed at this replay checkpoint."""
    teams = [
        team for team in (state.get("localteam"), state.get("visitorteam"))
        if isinstance(team, dict)
    ]
    teams_by_id = {str(team.get("id")): team for team in teams if team.get("id") is not None}
    delivery_history = state.get("deliveries") or []
    delivery_index = max(0, _number(state.get("delivery_index")))
    processed_deliveries = delivery_history[:delivery_index]

    player_names = {
        _key(player.get("player_id"), player.get("name")): player.get("name") or "Unknown batter"
        for player in state.get("batting", [])
        if isinstance(player, dict)
    }
    player_names.update({
        _key(player.get("player_id"), player.get("name")): player.get("name") or "Unknown bowler"
        for player in state.get("bowling", [])
        if isinstance(player, dict)
    })

    innings_by_label: dict[str, dict[str, Any]] = {}
    player_associations: dict[tuple[str, str], dict[str, Any]] = {}
    delivery_records: list[dict[str, Any]] = []
    legal_balls_by_innings: dict[str, int] = {}

    for delivery_index_zero, delivery in enumerate(processed_deliveries):
        if not isinstance(delivery, dict):
            continue
        innings_label = str(delivery.get("innings") or "1")
        team_id = delivery.get("team_id")
        batting_team = teams_by_id.get(str(team_id))
        bowling_team = next(
            (team for team in teams if str(team.get("id")) != str(team_id)),
            None,
        )
        innings = innings_by_label.setdefault(innings_label, {
            "innings": innings_label,
            "team_id": team_id,
            "batting_team": batting_team,
            "bowling_team": bowling_team,
            "runs": 0,
            "wickets": 0,
            "legal_balls": 0,
            "overs": "0.0",
            "run_rate": 0.0,
            "batters_by_key": {},
            "bowlers_by_key": {},
            "over_runs": {},
            "wicket_details": [],
            "partnerships": [],
        })

        batter_id, batter_name = delivery.get("batter_id"), delivery.get("batter")
        bowler_id, bowler_name = delivery.get("bowler_id"), delivery.get("bowler")
        batter_key = _key(batter_id, batter_name)
        bowler_key = _key(bowler_id, bowler_name)
        runs_batter = _number(delivery.get("runs_batter"))
        runs_total = _number(delivery.get("runs_total"))
        runs_bowler = _number(delivery.get("runs_bowler", runs_batter))
        is_legal = bool(delivery.get("is_legal"))
        is_wide = bool(delivery.get("is_wide"))
        is_noball = bool(delivery.get("is_noball"))
        batter_faced_ball = not is_wide
        is_wicket = bool(delivery.get("is_wicket"))
        is_bowler_wicket = bool(delivery.get("bowler_wicket", is_wicket))
        score_before = innings["runs"]
        wickets_before = innings["wickets"]
        legal_balls_before = innings["legal_balls"]

        innings["runs"] += runs_total
        innings["wickets"] += int(is_wicket)
        innings["legal_balls"] += int(is_legal)
        innings["overs"] = f"{innings['legal_balls'] // 6}.{innings['legal_balls'] % 6}"
        innings["run_rate"] = round(innings["runs"] * 6 / innings["legal_balls"], 2) if innings["legal_balls"] else 0.0
        legal_balls_by_innings[innings_label] = innings["legal_balls"]
        wicket_count = innings["wickets"]
        legal_ball_count = innings["legal_balls"]
        score_after = innings["runs"]
        rate_after = innings["run_rate"]

        batter = innings["batters_by_key"].setdefault(batter_key, {
            "player_id": batter_id,
            "name": batter_name or player_names.get(batter_key, "Unknown batter"),
            "team_id": team_id,
            "team": batting_team,
            "runs": 0,
            "balls": 0,
            "fours": 0,
            "sixes": 0,
            "dismissals": [],
        })
        batter["runs"] += runs_batter
        batter["balls"] += int(batter_faced_ball)
        batter["fours"] += int(bool(delivery.get("four")))
        batter["sixes"] += int(bool(delivery.get("six")))

        bowler = innings["bowlers_by_key"].setdefault(bowler_key, {
            "player_id": bowler_id,
            "name": bowler_name or player_names.get(bowler_key, "Unknown bowler"),
            "team_id": bowling_team.get("id") if bowling_team else None,
            "team": bowling_team,
            "runs": 0,
            "legal_balls": 0,
            "wickets": 0,
        })
        bowler["runs"] += runs_bowler
        bowler["legal_balls"] += int(is_legal)
        bowler["wickets"] += int(is_bowler_wicket)

        over_number = _over_number(delivery, delivery_index_zero, innings["legal_balls"] - int(is_legal))
        over = innings["over_runs"].setdefault(over_number, {
            "over": over_number,
            "runs": 0,
            "wickets": 0,
            "boundaries": 0,
            "boundary_runs": 0,
            "dot_balls": 0,
            "deliveries": [],
        })
        over["runs"] += runs_total
        over["wickets"] += int(is_wicket)
        boundary_runs = 6 if delivery.get("six") else 4 if delivery.get("four") else 0
        over["boundaries"] += int(boundary_runs > 0)
        over["boundary_runs"] += boundary_runs
        over["dot_balls"] += int(is_legal and runs_total == 0)
        over["deliveries"].append({
            "ball": delivery.get("over_ball"),
            "runs": runs_total,
            "batter_runs": runs_batter,
            "batter": batter_name,
            "bowler": bowler_name,
            "is_legal": is_legal,
            "four": bool(delivery.get("four")),
            "six": bool(delivery.get("six")),
            "wicket": is_wicket,
            "dismissal": delivery.get("dismissal"),
        })

        if is_wicket:
            dismissed_id = delivery.get("dismissed_batter_id") or batter_id
            dismissed_key = _key(dismissed_id, batter_name)
            dismissed_name = player_names.get(dismissed_key) or (
                batter_name if dismissed_key == batter_key else "Unknown batter"
            )
            detail = {
                "batter": dismissed_name,
                "batter_id": dismissed_id,
                "bowler": bowler_name,
                "over_ball": delivery.get("over_ball"),
                "dismissal": delivery.get("dismissal") or "Dismissal type unavailable",
                "bowler_credit": is_bowler_wicket,
            }
            innings["wicket_details"].append(detail)
            dismissed_batter = innings["batters_by_key"].setdefault(dismissed_key, {
                "player_id": dismissed_id,
                "name": dismissed_name,
                "team_id": team_id,
                "team": batting_team,
                "runs": 0,
                "balls": 0,
                "fours": 0,
                "sixes": 0,
                "dismissals": [],
            })
            dismissed_batter["dismissals"].append(detail)
            if dismissed_name != "Unknown batter":
                player_associations[("batter", dismissed_key)] = {
                    "player_id": dismissed_id,
                    "name": dismissed_name,
                    "role": "batter",
                    "team": batting_team,
                    "team_id": team_id,
                }

        if batter_name:
            player_associations[("batter", batter_key)] = {
                "player_id": batter_id,
                "name": batter_name,
                "role": "batter",
                "team": batting_team,
                "team_id": team_id,
            }
        if bowler_name:
            player_associations[("bowler", bowler_key)] = {
                "player_id": bowler_id,
                "name": bowler_name,
                "role": "bowler",
                "team": bowling_team,
                "team_id": bowling_team.get("id") if bowling_team else None,
            }
        delivery_records.append({
            "index": delivery_index_zero + 1,
            "innings": innings_label,
            "over_ball": delivery.get("over_ball"),
            "batting_team": batting_team,
            "batter": batter_name,
            "non_striker": delivery.get("non_striker"),
            "bowler": bowler_name,
            "batter_runs": runs_batter,
            "total_runs": runs_total,
            "bowler_runs": runs_bowler,
            "extras": _number(delivery.get("runs_extras")),
            "four": bool(delivery.get("four")),
            "six": bool(delivery.get("six")),
            "is_legal": is_legal,
            "is_wide": is_wide,
            "is_noball": is_noball,
            "batter_faced_ball": batter_faced_ball,
            "wicket": is_wicket,
            "bowler_wicket": is_bowler_wicket,
            "batter_id": batter_id,
            "bowler_id": bowler_id,
            "dismissed_batter": delivery.get("dismissed_batter") or batter_name,
            "over": over_number,
            "dismissal": delivery.get("dismissal"),
            "score_before": score_before,
            "wickets_before": wickets_before,
            "legal_balls_before": legal_balls_before,
            "score_after": score_after,
            "wickets_after": wicket_count,
            "legal_balls_after": legal_ball_count,
            "run_rate_after": rate_after,
        })

    completed_scorecards = state.get("completed_scorecards") or []
    for scorecard in completed_scorecards:
        if not isinstance(scorecard, dict):
            continue
        innings_label = str(scorecard.get("innings") or "")
        if not innings_label:
            continue
        team_id = scorecard.get("team_id")
        batting_team = teams_by_id.get(str(team_id))
        bowling_team = next(
            (team for team in teams if str(team.get("id")) != str(team_id)),
            None,
        )
        batters = {}
        for player in scorecard.get("batting") or []:
            if not isinstance(player, dict):
                continue
            player_id, name = player.get("player_id"), player.get("name")
            key = _key(player_id, name)
            batters[key] = {
                "player_id": player_id,
                "name": name or "Unknown batter",
                "team_id": team_id,
                "team": batting_team,
                "runs": _number(player.get("runs")),
                "balls": _number(player.get("balls")),
                "fours": _number(player.get("fours")),
                "sixes": _number(player.get("sixes")),
                "strike_rate": player.get("strike_rate"),
                "dismissals": (
                    [{"dismissal": player.get("dismissal")}]
                    if player.get("dismissal")
                    else []
                ),
            }
            if name:
                player_associations[("batter", key)] = {
                    "player_id": player_id,
                    "name": name,
                    "role": "batter",
                    "team": batting_team,
                    "team_id": team_id,
                }
        bowlers = {}
        for player in scorecard.get("bowling") or []:
            if not isinstance(player, dict):
                continue
            player_id, name = player.get("player_id"), player.get("name")
            key = _key(player_id, name)
            bowlers[key] = {
                "player_id": player_id,
                "name": name or "Unknown bowler",
                "team_id": bowling_team.get("id") if bowling_team else None,
                "team": bowling_team,
                "runs": _number(player.get("runs")),
                "legal_balls": _number(player.get("legal_balls")),
                "overs": player.get("overs"),
                "wickets": _number(player.get("wickets")),
                "economy": player.get("economy"),
            }
            if name:
                player_associations[("bowler", key)] = {
                    "player_id": player_id,
                    "name": name,
                    "role": "bowler",
                    "team": bowling_team,
                    "team_id": bowling_team.get("id") if bowling_team else None,
                }
        wicket_details = [
            {
                "batter": wicket.get("batter"),
                "over_ball": wicket.get("over"),
                "dismissal": wicket.get("dismissal"),
                "bowler_credit": None,
            }
            for wicket in scorecard.get("fall_of_wickets") or []
        ]
        existing = innings_by_label.get(innings_label)
        innings_by_label[innings_label] = {
            "innings": innings_label,
            "team_id": team_id,
            "batting_team": batting_team,
            "bowling_team": bowling_team,
            "runs": existing["runs"] if existing and state.get("narrative_mode") else _number(scorecard.get("runs")),
            "wickets": existing["wickets"] if existing and state.get("narrative_mode") else _number(scorecard.get("wickets")),
            "legal_balls": existing["legal_balls"] if existing and state.get("narrative_mode") else _number(scorecard.get("legal_balls")),
            "overs": existing["overs"] if existing and state.get("narrative_mode") else scorecard.get("overs") or "0.0",
            "run_rate": existing["run_rate"] if existing and state.get("narrative_mode") else scorecard.get("run_rate") or 0.0,
            "batters_by_key": existing["batters_by_key"] if existing and existing["batters_by_key"] else batters,
            "bowlers_by_key": existing["bowlers_by_key"] if existing and existing["bowlers_by_key"] else bowlers,
            "over_runs": existing["over_runs"] if existing and existing["over_runs"] else {},
            "wicket_details": existing["wicket_details"] if existing and existing["wicket_details"] else wicket_details,
            "partnerships": scorecard.get("partnerships") or [],
        }

    narrative_partnerships = state.get("narrative_partnerships") or {}
    for label, partnerships in narrative_partnerships.items():
        existing = innings_by_label.get(str(label))
        if existing and isinstance(partnerships, list):
            existing["partnerships"] = partnerships

    innings_list = []
    for innings in innings_by_label.values():
        batters = list(innings.pop("batters_by_key").values())
        bowlers = list(innings.pop("bowlers_by_key").values())
        for batter in batters:
            batter["strike_rate"] = _rate(batter["runs"], batter["balls"])
        for bowler in bowlers:
            bowler["overs"] = f"{bowler['legal_balls'] // 6}.{bowler['legal_balls'] % 6}"
            bowler["economy"] = _economy(bowler["runs"], bowler["legal_balls"])
        innings["batting_scorecard"] = sorted(batters, key=lambda player: (-player["runs"], player["name"].casefold()))
        innings["bowling_scorecard"] = sorted(bowlers, key=lambda player: (-player["wickets"], player["economy"], player["name"].casefold()))
        innings["over_scoring"] = list(innings["over_runs"].values())
        del innings["over_runs"]
        innings_list.append(innings)
    innings_list.sort(
        key=lambda innings: (
            0,
            int(innings["innings"]),
        ) if str(innings["innings"]).isdigit() else (1, str(innings["innings"]))
    )

    current_innings = innings_list[-1] if innings_list else None
    previous_innings = innings_list[-2] if len(innings_list) >= 2 else None
    situation = None
    if current_innings:
        target = previous_innings["runs"] + 1 if previous_innings else None
        balls_remaining = max(0, 120 - current_innings["legal_balls"])
        current_player_ids = {
            str(player.get("player_id"))
            for player in state.get("current_batters", [])
            if player.get("player_id") is not None
        }
        current_player_names = {
            player.get("name")
            for player in state.get("current_batters", [])
            if player.get("name")
        }
        current_batters = [
            batter for batter in current_innings["batting_scorecard"]
            if (
                str(batter.get("player_id")) in current_player_ids
                if batter.get("player_id") is not None
                else batter.get("name") in current_player_names
            )
        ]
        situation = {
            "innings": current_innings["innings"],
            "batting_team": current_innings["batting_team"],
            "bowling_team": current_innings["bowling_team"],
            "score": current_innings["runs"],
            "wickets": current_innings["wickets"],
            "overs": current_innings["overs"],
            "run_rate": current_innings["run_rate"],
            "target": target,
            "runs_required": max(0, target - current_innings["runs"]) if target is not None else None,
            "balls_remaining": balls_remaining,
            "required_run_rate": (
                round(max(0, target - current_innings["runs"]) * 6 / balls_remaining, 2)
                if target is not None and balls_remaining
                else None
            ),
            "current_batters": current_batters,
        }

    return {
        "mode": state.get("context_mode") or "HISTORICAL REPLAY CHECKPOINT",
        "fixture_id": state.get("id"),
        "status": state.get("status"),
        "delivery_index": delivery_index,
        "total_deliveries": _number(state.get("total_deliveries")),
        "is_complete": delivery_index >= _number(state.get("total_deliveries")),
        "narrative_mode": bool(state.get("narrative_mode")),
        "completed_innings": [str(label) for label in state.get("completed_innings", [])],
        "match_format": state.get("match_format"),
        "teams": teams,
        "innings": innings_list,
        "current_situation": situation,
        "player_associations": list(player_associations.values()),
        "delivery_history": delivery_records,
        "narrative_facts": _narrative_facts(delivery_records, state.get("match_format"))
        if state.get("narrative_mode") else {},
    }
