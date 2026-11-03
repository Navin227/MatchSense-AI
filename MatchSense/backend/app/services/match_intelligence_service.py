import math
from typing import Any


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _format_limits(state: dict[str, Any]) -> tuple[int, int, int] | None:
    match_format = str(state.get("match_format") or "").casefold()
    if not match_format:
        return (20, 6, 16) if state.get("context_mode") != "LIVE MATCH SNAPSHOT" else None
    if "t20" in match_format:
        return 20, 6, 16
    if "t10" in match_format:
        return 10, 2, 8
    if "odi" in match_format or "list a" in match_format:
        return 50, 10, 40
    return None


def _overs_to_balls(value: Any) -> int:
    text = str(value or "0.0")
    whole, _, partial = text.partition(".")
    try:
        return int(whole) * 6 + (int(partial[:1]) if partial else 0)
    except ValueError:
        return 0


def _team(state: dict[str, Any], team_id: Any) -> dict[str, Any] | None:
    return next(
        (team for team in (state.get("localteam"), state.get("visitorteam")) if team and team.get("id") == team_id),
        None,
    )


def _latest_over_points(state: dict[str, Any], innings: str, count: int = 3) -> list[dict[str, Any]]:
    overs: dict[str, int] = {}
    for delivery in state.get("deliveries", []):
        if str(delivery.get("innings")) != innings:
            continue
        ball = str(delivery.get("over_ball") or "")
        over = ball.split(".", 1)[0] or "?"
        overs[over] = overs.get(over, 0) + _int(delivery.get("runs_total"))
    return [
        {"over": str(int(over) + 1) if over.isdigit() else over, "runs": runs}
        for over, runs in list(overs.items())[-count:]
    ]


def _innings_bowling_stats(state: dict[str, Any], innings: str) -> list[dict[str, Any]]:
    stats: dict[str, dict[str, Any]] = {}
    for delivery in state.get("deliveries", []):
        if str(delivery.get("innings")) != innings:
            continue
        bowler_id = delivery.get("bowler_id")
        name = delivery.get("bowler") or "Unknown bowler"
        key = str(bowler_id) if bowler_id is not None else name
        bowler = stats.setdefault(key, {
            "name": name,
            "legal_balls": 0,
            "runs": 0,
            "wickets": 0,
        })
        bowler["legal_balls"] += int(bool(delivery.get("is_legal")))
        bowler["runs"] += _int(delivery.get("runs_bowler", delivery.get("runs_batter")))
        bowler["wickets"] += int(bool(delivery.get("bowler_wicket", delivery.get("is_wicket"))))

    for bowler in stats.values():
        legal_balls = bowler["legal_balls"]
        bowler["overs"] = f"{legal_balls // 6}.{legal_balls % 6}"
        bowler["economy"] = round(bowler["runs"] * 6 / legal_balls, 2) if legal_balls else 0.0
    return list(stats.values())


def _innings_batting_stats(state: dict[str, Any], innings: str) -> list[dict[str, Any]]:
    stats: dict[str, dict[str, Any]] = {}
    for delivery in state.get("deliveries", []):
        if str(delivery.get("innings")) != innings:
            continue
        batter_id = delivery.get("batter_id")
        name = delivery.get("batter") or "Unknown batter"
        key = str(batter_id) if batter_id is not None else name
        batter = stats.setdefault(key, {
            "player_id": batter_id,
            "name": name,
            "runs": 0,
            "balls": 0,
            "fours": 0,
            "sixes": 0,
            "is_current": False,
        })
        batter["runs"] += _int(delivery.get("runs_batter"))
        batter["balls"] += int(not delivery.get("is_wide"))
        batter["fours"] += int(bool(delivery.get("four")))
        batter["sixes"] += int(bool(delivery.get("six")))

    current_batters = state.get("current_batters", [])
    current_ids = {str(item.get("player_id")) for item in current_batters if item.get("player_id") is not None}
    current_names = {item.get("name") for item in current_batters if item.get("name")}
    for batter in stats.values():
        batter["strike_rate"] = round(batter["runs"] * 100 / batter["balls"], 2) if batter["balls"] else 0.0
        batter["is_current"] = (
            str(batter["player_id"]) in current_ids
            if batter["player_id"] is not None
            else batter["name"] in current_names
        )
    return list(stats.values())


def _player_matchups(state: dict[str, Any], innings: dict[str, Any] | None) -> list[dict[str, Any]]:
    if innings is None:
        return []
    current_innings = str(innings.get("innings"))
    pairs: dict[tuple[str, str], dict[str, Any]] = {}
    for delivery in state.get("deliveries", []):
        if str(delivery.get("innings")) != current_innings:
            continue
        batter = delivery.get("batter") or "Unknown batter"
        bowler = delivery.get("bowler") or "Unknown bowler"
        key = (batter, bowler)
        pair = pairs.setdefault(key, {
            "batter": batter,
            "bowler": bowler,
            "runs": 0,
            "balls": 0,
            "dismissals": 0,
            "boundaries": 0,
            "dots": 0,
        })
        pair["runs"] += _int(delivery.get("runs_batter"))
        pair["balls"] += int(not delivery.get("is_wide"))
        pair["dismissals"] += int(bool(delivery.get("is_wicket")))
        pair["boundaries"] += int(bool(delivery.get("four") or delivery.get("six")))
        pair["dots"] += int(
            bool(delivery.get("is_legal"))
            and _int(delivery.get("runs_total")) == 0
        )

    bowling_stats = {item["name"]: item for item in _innings_bowling_stats(state, current_innings)}
    matchups = []
    for pair in pairs.values():
        bowler = bowling_stats.get(pair["bowler"], {})
        balls = pair["balls"]
        matchups.append({
            **pair,
            "dot_ball_percentage": round(pair["dots"] * 100 / balls, 1) if balls else None,
            "strike_rate": round(pair["runs"] * 100 / balls, 1) if balls else None,
            "bowling_economy": bowler.get("economy"),
            "evidence": "Computed from deliveries processed in this replay checkpoint.",
        })
    return sorted(matchups, key=lambda item: (-item["balls"], item["batter"], item["bowler"]))


def _win_probability(state: dict[str, Any], innings: dict[str, Any] | None) -> dict[str, Any]:
    innings_list = state.get("innings", [])
    if innings is None or len(innings_list) < 2:
        return {
            "available": False,
            "label": "ESTIMATE · NOT AN OFFICIAL PREDICTION",
            "reason": "A chase must be underway and the first-innings total must be available.",
            "batting_team_probability": None,
            "bowling_team_probability": None,
            "factors": [],
        }

    format_limits = _format_limits(state)
    if format_limits is None:
        return {
            "available": False,
            "label": "ESTIMATE · NOT AN OFFICIAL PREDICTION",
            "reason": "The fixture format or its chase length is unavailable; a win probability is not estimated.",
            "batting_team_probability": None,
            "bowling_team_probability": None,
            "factors": [],
        }
    total_overs = format_limits[0]
    if total_overs != 20:
        return {
            "available": False,
            "label": "ESTIMATE · NOT AN OFFICIAL PREDICTION",
            "reason": "The deterministic win model is currently limited to 20-over fixtures.",
            "batting_team_probability": None,
            "bowling_team_probability": None,
            "factors": [],
        }
    first_innings = innings_list[0]
    target = _int(first_innings.get("runs")) + 1
    runs = _int(innings.get("runs"))
    wickets = _int(innings.get("wickets"))
    legal_balls = _int(innings.get("legal_balls"))
    balls_remaining = max(0, total_overs * 6 - legal_balls)
    runs_required = max(0, target - runs)
    required_rate = runs_required * 6 / balls_remaining if balls_remaining else (float("inf") if runs_required else 0)
    current_rate = _float(innings.get("run_rate"))
    wickets_remaining = max(0, 10 - wickets)

    if runs_required == 0:
        batting_probability = 99
    elif balls_remaining == 0 or wickets_remaining == 0:
        batting_probability = 1
    else:
        rate_margin = current_rate - required_rate
        # A transparent, deterministic logistic heuristic; it is not trained/calibrated
        # against a historical outcomes dataset and must not be presented as official odds.
        score = 0.38 * rate_margin + 0.16 * (wickets_remaining - 5) + 0.012 * (balls_remaining - 60)
        batting_probability = max(1, min(99, round(100 / (1 + math.exp(-score)))))

    factors = [
        {"name": "Runs required", "value": runs_required},
        {"name": "Balls remaining", "value": balls_remaining},
        {"name": "Wickets remaining", "value": wickets_remaining},
        {"name": "Required run rate", "value": round(required_rate, 2) if math.isfinite(required_rate) else None},
        {"name": "Current run rate", "value": round(current_rate, 2)},
    ]
    return {
        "available": True,
        "label": "DETERMINISTIC HEURISTIC ESTIMATE · NOT AN OFFICIAL PREDICTION",
        "method": "Logistic heuristic using rate margin, wickets in hand, and balls remaining; not calibrated on historical outcomes.",
        "target": target,
        "runs_required": runs_required,
        "balls_remaining": balls_remaining,
        "wickets_remaining": wickets_remaining,
        "required_run_rate": round(required_rate, 2) if math.isfinite(required_rate) else None,
        "current_run_rate": round(current_rate, 2),
        "batting_team_probability": batting_probability,
        "bowling_team_probability": 100 - batting_probability,
        "factors": factors,
    }


def _situation(state: dict[str, Any]) -> dict[str, Any]:
    innings_list = state.get("innings", [])
    current = innings_list[-1] if innings_list else None
    if current is None:
        return {
            "summary": "No deliveries have been processed; current match situation is not available yet.",
            "batting_team": None,
            "bowling_team": None,
            "current_innings": None,
            "score": None,
            "pressure": "No current checkpoint yet.",
            "factors": [],
            "strengths": [],
            "weaknesses": [],
            "momentum": [],
            "momentum_shifts": [],
            "recent_over_events": [],
            "measured_metrics": {
                "legal_balls": 0,
                "balls_faced": 0,
                "dot_balls": 0,
                "dot_ball_percentage": None,
                "boundary_deliveries": 0,
                "boundary_percentage": None,
                "wickets": 0,
                "balls_per_wicket": None,
            },
            "match_phase": None,
            "current_partnership": None,
            "available_bowlers": [],
            "observations": [],
        }

    batting_team = _team(state, current.get("team_id"))
    bowling_team = next(
        (team for team in (state.get("localteam"), state.get("visitorteam")) if team and team != batting_team),
        None,
    )
    runs = _int(current.get("runs"))
    wickets = _int(current.get("wickets"))
    legal_balls = _int(current.get("legal_balls"))
    format_limits = _format_limits(state)
    total_overs = format_limits[0] if format_limits else None
    overs_left = max(0, total_overs * 6 - legal_balls) if total_overs is not None else None
    current_rate = _float(current.get("run_rate"))
    innings_number = str(current.get("innings"))
    recent_overs = _latest_over_points(state, innings_number)
    matchup_rows = _player_matchups(state, current)
    batter_stats = _innings_batting_stats(state, innings_number)
    bowlers = _innings_bowling_stats(state, innings_number)
    innings_deliveries = [
        delivery for delivery in state.get("deliveries", [])
        if str(delivery.get("innings")) == innings_number
    ]
    legal_deliveries = [delivery for delivery in innings_deliveries if bool(delivery.get("is_legal"))]
    dot_balls = sum(
        bool(delivery.get("is_legal")) and _int(delivery.get("runs_total")) == 0
        for delivery in innings_deliveries
    )
    boundaries = sum(bool(delivery.get("four") or delivery.get("six")) for delivery in innings_deliveries)
    balls_faced = sum(not delivery.get("is_wide") for delivery in innings_deliveries)
    wicket_deliveries = sum(bool(delivery.get("is_wicket")) for delivery in innings_deliveries)
    metrics = {
        "legal_balls": len(legal_deliveries),
        "balls_faced": balls_faced,
        "dot_balls": dot_balls,
        "dot_ball_percentage": round(dot_balls * 100 / len(legal_deliveries), 1) if legal_deliveries else None,
        "boundary_deliveries": boundaries,
        "boundary_percentage": round(
            boundaries * 100 / balls_faced,
            1,
        ) if balls_faced else None,
        "wickets": wicket_deliveries,
        "balls_per_wicket": round(len(legal_deliveries) / wicket_deliveries, 1) if wicket_deliveries else None,
    }
    recent_over_events = []
    if innings_deliveries:
        latest_over = str(innings_deliveries[-1].get("over_ball") or "").split(".", 1)[0]
        latest_over_deliveries = [
            delivery for delivery in innings_deliveries
            if str(delivery.get("over_ball") or "").split(".", 1)[0] == latest_over
        ]
        latest_over_runs = sum(_int(delivery.get("runs_total")) for delivery in latest_over_deliveries)
        recent_over_events.append(
            f"Over {int(latest_over) + 1 if latest_over.isdigit() else latest_over}: {latest_over_runs} runs from {len(latest_over_deliveries)} recorded deliveries."
        )
        for delivery in latest_over_deliveries:
            if delivery.get("is_wicket"):
                recent_over_events.append(
                    f"Wicket: {delivery.get('dismissal') or 'dismissal type unavailable'} ({delivery.get('batter') or 'batter unavailable'})."
                )
            elif delivery.get("six"):
                recent_over_events.append(f"Six by {delivery.get('batter') or 'batter unavailable'}.")
            elif delivery.get("four"):
                recent_over_events.append(f"Four by {delivery.get('batter') or 'batter unavailable'}.")
        if latest_over_runs >= 12:
            recent_over_events.append(f"Measured high-scoring over: {latest_over_runs} runs (12+ run threshold).")
    momentum_shifts = []
    for previous, latest in zip(recent_overs, recent_overs[1:]):
        difference = latest["runs"] - previous["runs"]
        if abs(difference) >= 4:
            direction = "increased" if difference > 0 else "decreased"
            momentum_shifts.append(
                f"Scoring {direction} by {abs(difference)} runs from over {previous['over']} ({previous['runs']}) to over {latest['over']} ({latest['runs']})."
            )
    current_partnership = None
    if len(state.get("current_batters", [])) >= 2:
        pair = state["current_batters"][:2]
        latest_wicket_index = max(
            (index for index, delivery in enumerate(innings_deliveries) if delivery.get("is_wicket")),
            default=-1,
        )
        partnership_deliveries = innings_deliveries[latest_wicket_index + 1:]
        contributions = []
        for player in pair:
            key = str(player.get("player_id")) if player.get("player_id") is not None else player.get("name")
            batter_deliveries = [
                delivery for delivery in partnership_deliveries
                if (
                    str(delivery.get("batter_id")) == key
                    if delivery.get("batter_id") is not None
                    else delivery.get("batter") == player.get("name")
                )
            ]
            contributions.append({
                "name": player.get("name"),
                "runs": sum(_int(delivery.get("runs_batter")) for delivery in batter_deliveries),
                "balls": sum(bool(delivery.get("is_legal")) for delivery in batter_deliveries),
            })
        current_partnership = {
            "batters": [player.get("name") for player in pair],
            "runs": sum(_int(delivery.get("runs_total")) for delivery in partnership_deliveries),
            "balls": sum(not delivery.get("is_wide") for delivery in partnership_deliveries),
            "contributions": contributions,
        }
    for bowler in bowlers:
        bowler["is_current"] = bowler["name"] == (state.get("current_bowler") or {}).get("name")
    recent_runs = sum(item["runs"] for item in recent_overs[-2:])
    average_recent = recent_runs / len(recent_overs[-2:]) if recent_overs else 0
    required_rate: float | None = None
    completed_overs = legal_balls // 6
    powerplay_overs = format_limits[1] if format_limits else None
    middle_overs = format_limits[2] if format_limits else None
    phase = (
        "Powerplay" if powerplay_overs is not None and completed_overs < powerplay_overs
        else "Middle overs" if middle_overs is not None and completed_overs < middle_overs
        else "Death overs" if middle_overs is not None
        else None
    )

    factors = [
        f"{runs}/{wickets} after {current.get('overs', '0.0')} overs ({current_rate:.2f} runs per over).",
        (
            f"{10 - wickets} wickets remain and {overs_left} legal deliveries remain in a {total_overs}-over innings."
            if total_overs is not None
            else f"{10 - wickets} wickets remain; innings length is unavailable for this match format."
        ),
    ]
    if len(state.get("innings", [])) >= 2:
        target = _int(state["innings"][0].get("runs")) + 1
        needed = max(0, target - runs)
        if overs_left is not None:
            required_rate = needed * 6 / overs_left if overs_left else (float("inf") if needed else 0)
        factors.append(
            f"Chase target {target}: {needed} runs required"
            + (
                f" from {overs_left} balls at {required_rate:.2f} per over."
                if required_rate is not None and math.isfinite(required_rate)
                else f" from {overs_left} balls."
                if overs_left is not None
                else "; required rate unavailable because the match length is unknown."
            )
        )

    strengths = []
    weaknesses = []
    observations = []
    if batter_stats:
        best_batter = max(batter_stats, key=lambda item: _int(item.get("strike_rate")))
        strengths.append(
            f"{best_batter['name']} has {best_batter.get('runs', 0)} runs from {best_batter.get('balls', 0)} balls "
            f"(strike rate {best_batter.get('strike_rate', 0)})."
        )
        current_batters = [item for item in batter_stats if item.get("is_current")]
        for batter in current_batters:
            sr = _float(batter.get("strike_rate"))
            if batter.get("balls", 0) >= 5:
                observation = f"{batter['name']} is scoring at {sr:.1f} from {batter['balls']} balls."
                (strengths if sr >= 120 else weaknesses).append(observation)
    if wickets >= 6:
        weaknesses.append(f"{wickets} wickets have fallen; only {10 - wickets} remain.")
    if overs_left is not None and overs_left <= 30:
        weaknesses.append(f"Only {overs_left} legal balls remain to complete this innings.")
    if average_recent:
        direction = "above" if average_recent > current_rate else "at or below"
        momentum = (
            f"The last {len(recent_overs[-2:])} completed/in-progress overs produced {recent_runs} runs "
            f"({average_recent:.1f} per over), {direction} the innings rate of {current_rate:.2f}."
        )
        observations.append(momentum)
    if not strengths:
        strengths.append("No batter has faced a delivery in this checkpoint yet.")
    if not weaknesses:
        weaknesses.append("No specific weakness is established by the available checkpoint statistics.")

    best_bowler = min(bowlers, key=lambda item: _float(item.get("economy"))) if bowlers else None
    if best_bowler:
        observations.append(
            f"{best_bowler['name']} is the most economical bowler used so far at "
            f"{_float(best_bowler.get('economy')):.2f} runs per over "
            f"({best_bowler.get('wickets', 0)} wickets)."
        )
    pressure = "Elevated" if wickets >= 6 or (
        required_rate is not None and required_rate > current_rate + 2
    ) else (
        "Building" if wickets >= 4 or (
            required_rate is not None and required_rate > current_rate
        ) else "Measured"
    )

    return {
        "summary": f"{batting_team['name'] if batting_team else 'Batting side'} are {runs}/{wickets} after {current.get('overs', '0.0')} overs; the innings rate is {current_rate:.2f}.",
        "batting_team": batting_team,
        "bowling_team": bowling_team,
        "current_innings": current,
        "score": {"runs": runs, "wickets": wickets, "overs": current.get("overs"), "run_rate": current_rate},
        "pressure": pressure,
        "factors": factors,
        "strengths": strengths,
        "weaknesses": weaknesses,
        "momentum": recent_overs,
        "momentum_shifts": momentum_shifts,
        "recent_over_events": recent_over_events,
        "measured_metrics": metrics,
        "match_phase": phase,
        "current_partnership": current_partnership,
        "observations": observations,
        "available_bowlers": [
            {"name": item["name"], "overs": item.get("overs"), "legal_balls": item.get("legal_balls"), "runs": item.get("runs"), "wickets": item.get("wickets"), "economy": item.get("economy")}
            for item in bowlers
        ],
        "matchups": matchup_rows,
        "recent_deliveries": state.get("deliveries", [])[-12:],
    }


def build_intelligence(state: dict[str, Any]) -> dict[str, Any]:
    situation = _situation(state)
    innings = situation.get("current_innings")
    matchups = situation.get("matchups", [])
    batting = _innings_batting_stats(state, str(innings.get("innings"))) if innings else []
    bowlers = situation.get("available_bowlers", [])
    has_checkpoint = innings is not None

    if not has_checkpoint:
        return {
            "situation": situation,
            "debate": {"available": False, "reason": "Play at least one delivery to establish a match situation."},
            "decision": {"available": False, "reason": "There are no current checkpoint statistics to support a recommendation."},
            "matchups": {"available": False, "reason": "No batter-bowler deliveries have been processed yet.", "items": []},
            "field": {"available": False, "label": "AI-SUGGESTED FIELD SETUP · NOT AN ACTUAL FIELD", "reason": "A current batter and bowling pattern are required.", "positions": []},
            "win_probability": _win_probability(state, None),
        }

    current_batters = [item for item in batting if item.get("is_current")]
    current_batter = current_batters[-1] if current_batters else None
    current_bowler = next((item for item in bowlers if item["name"] == (state.get("current_bowler") or {}).get("name")), None)
    relevant_matchups = [item for item in matchups if item["batter"] == (current_batter or {}).get("name")]
    best_vs_bowler = max(relevant_matchups, key=lambda item: item["runs"] / item["balls"] if item["balls"] else 0, default=None)
    most_economical = min(bowlers, key=lambda item: _float(item.get("economy")), default=None)
    runs, wickets = _int(innings.get("runs")), _int(innings.get("wickets"))

    aggressive_benefit = "Use the batter's observed scoring rate to increase run production."
    aggressive_risk = "A wicket reduces the remaining batting resources; this checkpoint cannot predict the next delivery."
    controlled_benefit = "Limit wicket exposure while retaining the current scoring base."
    controlled_risk = "A low scoring rate may increase the required rate later in the innings."
    batting_team_name = (situation.get("batting_team") or {}).get("name") or "Batting team"
    if current_batter:
        aggressive_evidence = [
            f"{current_batter['name']}: {current_batter.get('runs', 0)} runs off {current_batter.get('balls', 0)} balls "
            f"(SR {current_batter.get('strike_rate', 0)})."
        ]
    else:
        aggressive_evidence = ["No current batter has an established delivery sample."]
    if best_vs_bowler:
        aggressive_evidence.append(
            f"Checkpoint matchup: {best_vs_bowler['runs']} batter runs from {best_vs_bowler['balls']} balls "
            f"versus {best_vs_bowler['bowler']} ({best_vs_bowler['strike_rate']} SR)."
        )
    controlled_evidence = [
        f"{10 - wickets} wickets remain at {runs}/{wickets}.",
        *(
            [f"{most_economical['name']} has conceded at {most_economical['economy']} per over across {most_economical['overs']} overs."]
            if most_economical else ["No completed bowler comparison is available."]
        ),
    ]
    debate = {
        "available": True,
        "strategy_a": {
            "name": f"Aggressive · {batting_team_name}",
            "objective": "Raise the scoring rate against a bowling option with a documented matchup opportunity.",
            "action": (
                f"Consider attacking {best_vs_bowler['bowler']} with {current_batter['name']}."
                if best_vs_bowler and current_batter
                else "Accelerate selectively using the current batter's measured strike rate; no direct matchup advantage is established yet."
            ),
            "evidence": aggressive_evidence,
            "expected_advantage": aggressive_benefit,
            "risk": aggressive_risk,
            "conditions": "Best considered when the required rate is rising, enough wickets remain, and the batter's checkpoint strike rate supports the attack.",
        },
        "strategy_b": {
            "name": f"Controlled · {batting_team_name}",
            "objective": "Protect wickets and rotate strike before choosing an evidence-backed scoring opportunity.",
            "action": "Prioritize low-risk scoring against the most economical option; use boundaries only when the matchup data supports it.",
            "evidence": controlled_evidence,
            "expected_advantage": controlled_benefit,
            "risk": controlled_risk,
            "conditions": "Best considered when few wickets remain or the current required rate is manageable.",
        },
        "comparison": (
            f"{batting_team_name}'s aggression trades wicket risk for scoring upside; control preserves wickets but can leave a larger future rate demand. "
            f"The checkpoint is {runs}/{wickets}; these are scenarios, not forecasts."
        ),
    }

    if wickets >= 7:
        decision = {
            "available": True,
            "recommendation": f"For {batting_team_name}, prioritize low-risk strike rotation while protecting the {10 - wickets} remaining wickets.",
            "reasoning": f"{wickets} wickets have fallen; this recommendation responds to batting resources rather than repeating an economy comparison.",
            "supporting_statistics": [
                f"Current score {runs}/{wickets} after {innings.get('overs')} overs; run rate {innings.get('run_rate')}.",
                *([f"{current_batter['name']}: {current_batter['runs']} runs from {current_batter['balls']} balls, strike rate {current_batter['strike_rate']}."]
                  if current_batter else []),
            ],
            "expected_benefit": "Preserves the remaining batting resources while continuing to score.",
            "risk": "A conservative approach can leave more runs to score later; this checkpoint cannot predict the next delivery.",
            "confidence": "Low: based on wickets fallen in one provider snapshot; no calibrated prediction model.",
            "is_team_instruction": False,
        }
    elif current_batter and current_batter.get("balls", 0) >= 5:
        decision = {
            "available": True,
            "recommendation": f"Use {current_batter['name']} as {batting_team_name}'s reference batter; let the partner take the higher-variance scoring options.",
            "reasoning": f"{current_batter['name']} has a measured sample of {current_batter['balls']} balls; this is a role allocation suggestion, not a forecast.",
            "supporting_statistics": [
                f"Current score {runs}/{wickets}; run rate {innings.get('run_rate')}.",
                f"{current_batter['name']}: {current_batter['runs']} runs from {current_batter['balls']} balls, strike rate {current_batter['strike_rate']}.",
            ],
            "expected_benefit": "Keeps one established batter available while another player can pursue scoring opportunities.",
            "risk": "A small sample does not guarantee continued performance; it may reduce scoring if the anchor is out of form.",
            "confidence": "Low: a tactical interpretation of the current batter sample only.",
            "is_team_instruction": False,
        }
    else:
        decision = {
            "available": True,
            "recommendation": f"{batting_team_name} should gather more batter-bowler evidence before assigning a player-specific role.",
            "reasoning": "The current checkpoint does not yet contain a sufficiently established batter sample or bowler comparison.",
            "supporting_statistics": [
                f"Current score {runs}/{wickets} after {innings.get('overs')} overs.",
                f"Current innings rate {innings.get('run_rate')}; {10 - wickets} wickets remain.",
                *([f"{current_batter['name']}: {current_batter['runs']} runs from {current_batter['balls']} balls, strike rate {current_batter['strike_rate']}."]
                  if current_batter else []),
            ],
            "expected_benefit": "Avoids claiming an advantage unsupported by observed data.",
            "risk": "This cautious recommendation does not forecast the next ball.",
            "confidence": "Low: limited checkpoint evidence.",
            "is_team_instruction": False,
        }

    recent_batter = current_batter or {}
    legal_balls = _int(innings.get("legal_balls"))
    completed_overs = legal_balls // 6
    phase = situation.get("match_phase") or "Not established"
    batter_balls = _int(recent_batter.get("balls"))
    batter_boundaries = _int(recent_batter.get("fours")) + _int(recent_batter.get("sixes"))
    boundary_rate = batter_boundaries / batter_balls if batter_balls else None
    boundary_positions = (
        [
            {"name": "Third", "x": 18, "y": 19, "reason": f"Boundary protection option; {recent_batter.get('name')} has scored {batter_boundaries} boundaries from {batter_balls} balls in this checkpoint. Direction is not known."},
            {"name": "Fine leg", "x": 82, "y": 81, "reason": "Conventional boundary protection option; no shot-direction data is available."},
        ]
        if boundary_rate is not None and boundary_rate >= 0.25
        else [
            {"name": "Point", "x": 21, "y": 45, "reason": "Conventional inner-ring option; no shot-direction data is available."},
            {"name": "Midwicket", "x": 70, "y": 60, "reason": "Conventional inner-ring option; no shot-direction data is available."},
        ]
    )
    field_positions = [
        {"name": "Wicketkeeper", "x": 50, "y": 17, "reason": "Standard wicketkeeping position; no shot-direction data is available."},
        {"name": "Bowler", "x": 50, "y": 49, "reason": "Standard bowling position."},
    ]
    field_positions.extend([
        {"name": "Cover", "x": 26, "y": 34, "reason": "Conventional inner-ring position; no directional shot data is available."},
        {"name": "Mid-off", "x": 65, "y": 36, "reason": "Conventional inner-ring position; no directional shot data is available."},
        {"name": "Mid-on", "x": 36, "y": 67, "reason": "Conventional inner-ring position; no directional shot data is available."},
        {"name": "Square leg", "x": 75, "y": 58, "reason": "Conventional leg-side position; no directional shot data is available."},
        *boundary_positions,
    ])

    return {
        "situation": situation,
        "debate": debate,
        "decision": decision,
        "matchups": {
            "available": bool(matchups),
            "reason": None if matchups else "No batter-bowler deliveries have been processed in the current innings.",
            "evidence_scope": "Current innings deliveries processed so far only; no external head-to-head data is loaded.",
            "items": matchups,
        },
        "field": {
            "available": True,
            "label": "AI-SUGGESTED FIELD SETUP · NOT AN ACTUAL FIELD",
            "basis": [
                f"Match phase: {phase} ({legal_balls} legal balls processed in this innings).",
                f"Current batter: {recent_batter.get('name', 'not established')}."
                + (f" Checkpoint strike rate {recent_batter.get('strike_rate')}; {batter_boundaries} boundaries from {batter_balls} balls." if recent_batter else ""),
                f"Current bowler: {(current_bowler or {}).get('name', 'not established')}."
                + (f" Economy {current_bowler.get('economy')} so far." if current_bowler else ""),
                "Bowler type is not present in the available feed, so no pace/spin-specific placement is inferred.",
                "No shot-direction/wagon-wheel data is available; placements are conventional suggestions, not learned coordinates.",
            ],
            "positions": field_positions,
        },
        "win_probability": _win_probability(state, innings),
    }


def simulate_scenario(state: dict[str, Any], scenario: dict[str, Any]) -> dict[str, Any]:
    innings_list = state.get("innings", [])
    current = innings_list[-1] if innings_list else None
    if current is None:
        raise ValueError("Process at least one delivery before simulating a scenario.")

    original_runs = _int(current.get("runs"))
    original_wickets = _int(current.get("wickets"))
    legal_balls = _int(current.get("legal_balls"))
    original_balls_remaining = max(0, 120 - legal_balls)
    kind = scenario["kind"]
    assumptions = dict(scenario)
    modified_runs = original_runs
    modified_wickets = original_wickets
    if kind == "additional_runs":
        modified_runs += scenario["runs"]
    elif kind == "wicket_not_fallen":
        if original_wickets == 0:
            raise ValueError("No wicket has fallen in the current checkpoint.")
        modified_wickets -= 1
    elif kind == "bowler_runs_saved":
        modified_runs = max(0, modified_runs - scenario["runs"])
    elif kind == "earlier_acceleration":
        modified_runs += scenario["runs"]
    elif kind == "alternative_bowler":
        raise ValueError("Alternative-bowler scenarios need complete over-by-over bowler allocation, which this fixture feed does not provide.")
    else:
        raise ValueError("Unsupported scenario type.")

    target = _int(innings_list[0].get("runs")) + 1 if len(innings_list) >= 2 else None
    runs_required = max(0, target - modified_runs) if target is not None else None
    required_rate = (
        round(runs_required * 6 / original_balls_remaining, 2)
        if runs_required is not None and original_balls_remaining > 0 else
        (0.0 if runs_required == 0 else None)
    )
    original_required = max(0, target - original_runs) if target is not None else None
    original_rate = (
        round(original_required * 6 / original_balls_remaining, 2)
        if original_required is not None and original_balls_remaining > 0 else
        (0.0 if original_required == 0 else None)
    )
    difference = modified_runs - original_runs
    return {
        "label": "DETERMINISTIC WHAT-IF · NOT A PREDICTION",
        "scenario": assumptions,
        "original": {
            "runs": original_runs,
            "wickets": original_wickets,
            "overs": current.get("overs"),
            "balls_remaining": original_balls_remaining,
            "runs_required": original_required,
            "required_run_rate": original_rate,
        },
        "modified": {
            "runs": modified_runs,
            "wickets": modified_wickets,
            "overs": current.get("overs"),
            "balls_remaining": original_balls_remaining,
            "wickets_remaining": 10 - modified_wickets,
            "runs_required": runs_required,
            "required_run_rate": required_rate,
        },
        "difference": {
            "runs": difference,
            "wickets": modified_wickets - original_wickets,
            "required_run_rate": round(required_rate - original_rate, 2) if required_rate is not None and original_rate is not None else None,
        },
        "explanation": (
            f"This scenario changes the checkpoint by {difference:+d} runs while holding all other completed deliveries constant. "
            "It does not model or claim to know how later deliveries would change."
        ),
        "uncertainty": "Historical completed deliveries are held fixed; any future consequences are unknown and not simulated.",
    }
