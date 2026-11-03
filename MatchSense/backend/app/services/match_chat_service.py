import asyncio
import hashlib
import json
import logging
import re
import time
from collections import OrderedDict
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.config import settings
from app.services.match_context_builder import build_match_context

logger = logging.getLogger(__name__)

SYSTEM_INSTRUCTION = """You are MatchSense, an expert cricket match intelligence assistant.

Answer questions using only the supplied match context.

Rules:
- Never invent player statistics.
- Never confuse players from different teams.
- Never confuse innings.
- Never use statistics beyond the supplied live snapshot or replay checkpoint.
- Distinguish recorded facts from hypothetical scenarios.
- If information is missing, explicitly identify it.
- Never infer player intent, confidence, psychology, field positions, bowling plans, shot selection, or captaincy decisions.
- Explain cricket terminology when necessary.
- Answer the exact question asked.
- Avoid generic filler.
- The current match context is authoritative. Conversation history is only for resolving follow-up references and may describe older checkpoints.
- The supplied mode identifies a live provider snapshot or historical replay checkpoint; never relabel the source.

Return only valid JSON with this exact schema:
{"answer":"Human-readable answer","evidence":[{"label":"Supporting statistic","value":"Actual calculated value"}],"confidence":"high|medium|low","limitations":["limitation, if any"]}

Use plain text in answer, not Markdown. Keep the answer focused and include a direct answer, explanation, supporting evidence, and any relevant limitations. Evidence must be copied from match context; do not calculate or invent new statistics."""

Confidence = Literal["high", "medium", "low"]
ProviderName = Literal["ollama", "cerebras", "gemini", "groq"]


class EvidenceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=120)
    value: str = Field(min_length=1, max_length=300)


class MatchAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str = Field(min_length=12, max_length=1800)
    evidence: list[EvidenceItem] = Field(default_factory=list, max_length=12)
    confidence: Confidence
    limitations: list[str] = Field(default_factory=list, max_length=8)


class ProviderFailure(RuntimeError):
    def __init__(self, provider: str, message: str, status_code: int | None = None):
        super().__init__(message)
        self.provider = provider
        self.status_code = status_code


_answer_cache: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()
_CACHE_LIMIT = 128


def _clean_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    text = value.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", text)
    text = re.sub(r"(?m)^\s*[-*+]\s+", "", text)
    text = re.sub(r"[*_`~]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _is_complete_answer(text: str) -> bool:
    words = re.findall(r"\b[\w'-]+\b", text)
    return (
        len(words) >= 5
        and bool(re.search(r"[.!?][\"')\]]?$", text))
        and not re.search(r"(?:['’]s|because|and|but|the|of|to|with|for)\.?[\"')\]]?$", text, re.IGNORECASE)
    )


def _answer(
    text: str,
    evidence: list[tuple[str, Any]] | None = None,
    confidence: Confidence = "high",
    limitations: list[str] | None = None,
) -> dict[str, Any]:
    return MatchAnswer(
        answer=_clean_text(text),
        evidence=[
            EvidenceItem(label=label, value=str(value))
            for label, value in (evidence or [])
        ],
        confidence=confidence,
        limitations=limitations or [],
    ).model_dump()


def _words(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.casefold()))


def _team_in_question(question: str, context: dict[str, Any]) -> dict[str, Any] | None:
    normalized = f" {_words(question)} "
    matches = []
    for team in context["teams"]:
        names = [team.get("name"), team.get("short_name")]
        for name in names:
            if name and f" {_words(str(name))} " in normalized:
                matches.append(team)
                break
    return max(matches, key=lambda team: len(_words(team.get("name") or "")), default=None)


def _team_display(team: dict[str, Any] | None) -> str:
    return str(team.get("name") or "Unknown team") if team else "the selected team"


def _find_players(question: str, context: dict[str, Any], role: str | None = None) -> list[dict[str, Any]]:
    normalized_question = f" {_words(question)} "
    question_words = set(normalized_question.split())
    results = []
    for innings in context["innings"]:
        for player_role, key in (("batter", "batting_scorecard"), ("bowler", "bowling_scorecard")):
            if role and player_role != role:
                continue
            for player in innings[key]:
                normalized_name = _words(player["name"])
                name_words = normalized_name.split()
                if normalized_name and (
                    f" {normalized_name} " in normalized_question
                    or all(word in question_words for word in name_words)
                ):
                    result = {**player, "role": player_role, "innings": innings}
                    if not any(
                        item["role"] == player_role
                        and str(item.get("player_id")) == str(player.get("player_id"))
                        and item["innings"]["innings"] == innings["innings"]
                        for item in results
                    ):
                        results.append(result)
    return results


def _named_player_phrase(question: str) -> str | None:
    patterns = (
        r"\bwhy\s+(?:did|does|is|was)\s+(.+?)\s+(?:struggle|struggling|fail|failing|score|scoring|play|playing|perform|performing)\b",
        r"\b(?:strike rate|economy|figures|stats?)\s+(?:for|of)\s+(.+?)(?:\?|$)",
        r"\b(?:how did|what did)\s+(.+?)\s+(?:score|bowl|perform)\b",
    )
    for pattern in patterns:
        match = re.search(pattern, question, flags=re.IGNORECASE)
        if match:
            name = re.sub(r"\b(the|batter|batsman|bowler|team)\b", "", match.group(1), flags=re.IGNORECASE)
            return name.strip(" ?.,")
    return None


def _all_batters(context: dict[str, Any]) -> list[dict[str, Any]]:
    results = []
    for innings in context["innings"]:
        for player in innings["batting_scorecard"]:
            results.append({**player, "innings": innings})
    return results


def _all_bowlers(context: dict[str, Any]) -> list[dict[str, Any]]:
    results = []
    for innings in context["innings"]:
        for player in innings["bowling_scorecard"]:
            results.append({**player, "innings": innings})
    return results


def _evidence_for_batter(player: dict[str, Any]) -> list[tuple[str, Any]]:
    return [
        ("Team", _team_display(player.get("team"))),
        ("Innings", player["innings"]["innings"]),
        ("Runs", player["runs"]),
        ("Balls faced", player["balls"]),
        ("Fours / sixes", f"{player['fours']} / {player['sixes']}"),
        ("Strike rate", player["strike_rate"]),
    ]


def _deterministic_answer(question: str, context: dict[str, Any]) -> dict[str, Any] | None:
    normalized = _words(question)
    situation = context["current_situation"]
    team = _team_in_question(question, context)

    if "los" in normalized and team and len(context["innings"]) >= 2:
        selected = next(
            (item for item in context["innings"] if str(item["team_id"]) == str(team["id"])),
            None,
        )
        if not context["is_complete"]:
            score = (
                f"{selected['runs']}/{selected['wickets']} in innings {selected['innings']}"
                if selected
                else "no batting deliveries for that team"
            )
            return _answer(
                f"The match result is not established yet: this is replay checkpoint {context['delivery_index']} of {context['total_deliveries']}. {_team_display(team)} has {score}; unprocessed deliveries are excluded.",
                [("Replay checkpoint", f"{context['delivery_index']} of {context['total_deliveries']} deliveries")],
                "low",
                ["A completed result and causal explanation cannot be given before the replay finishes."],
            )
        if selected:
            opposing = next(
                (item for item in context["innings"] if str(item["team_id"]) != str(team["id"])),
                None,
            )
            if opposing and selected["runs"] != opposing["runs"]:
                loser = selected if selected["runs"] < opposing["runs"] else opposing
                winner = opposing if loser is selected else selected
                if loser["innings"] == "1":
                    margin = max(0, 10 - winner["wickets"])
                    result_text = f"{_team_display(winner['batting_team'])} won by {margin} wickets"
                else:
                    result_text = f"{_team_display(winner['batting_team'])} won by {winner['runs'] - loser['runs']} runs"
                top_batter = max(selected["batting_scorecard"], key=lambda player: player["runs"], default=None)
                best_opposition_bowler = max(selected["bowling_scorecard"], key=lambda player: player["wickets"], default=None)
                evidence = [
                    ("Final score", f"{_team_display(selected['batting_team'])} {selected['runs']}/{selected['wickets']}"),
                    ("Opposition score", f"{_team_display(opposing['batting_team'])} {opposing['runs']}/{opposing['wickets']}"),
                ]
                supporting = []
                if top_batter:
                    supporting.append(f"{top_batter['name']} top-scored for {_team_display(team)} with {top_batter['runs']}.")
                    evidence.append((f"{top_batter['name']} runs", top_batter["runs"]))
                if best_opposition_bowler:
                    supporting.append(
                        f"{best_opposition_bowler['name']} took {best_opposition_bowler['wickets']} wicket(s) for {best_opposition_bowler['runs']} runs."
                    )
                    evidence.append((f"{best_opposition_bowler['name']} wickets / runs", f"{best_opposition_bowler['wickets']} / {best_opposition_bowler['runs']}"))
                answer = f"{_team_display(team)} lost: {result_text}. "
                answer += "The processed scorecards show " + " ".join(supporting) if supporting else "The processed scorecards show the final team totals."
                return _answer(
                    answer,
                    evidence,
                    "high",
                    ["Scorecard statistics describe what happened; they cannot establish a single cause for the result."],
                )

    if any(term in normalized for term in ("another wicket", "loses another wicket", "lose another wicket", "losing another wicket")):
        if not situation:
            return _answer("There is no processed innings checkpoint yet, so the effect of another wicket cannot be calculated.", confidence="low", limitations=["No delivery has been processed."])
        wickets_after = min(10, situation["wickets"] + 1)
        remaining_after = max(0, 10 - wickets_after)
        completion_note = (
            " The recorded replay is complete and no later wicket occurred; this is only a hypothetical extension."
            if context["is_complete"] else ""
        )
        evidence = [
            ("Current score", f"{situation['score']}/{situation['wickets']}"),
            ("Wickets remaining now", max(0, 10 - situation["wickets"])),
            ("Wickets remaining after one more wicket", remaining_after),
        ]
        return _answer(
            f"At delivery {context['delivery_index']}, one additional wicket would leave {_team_display(situation['batting_team'])} at {situation['score']}/{wickets_after} with {remaining_after} wickets remaining. This calculation holds the score and balls faced fixed; it does not predict later runs.{completion_note}",
            evidence,
            "high",
            ["Hypothetical arithmetic only; subsequent deliveries and the result are unknown."],
        )

    best_batter_question = (
        ("best" in normalized or "highest" in normalized or "top" in normalized or "most runs" in normalized)
        and any(term in normalized for term in ("batter", "batsman", "scorer", "runs"))
    )
    if best_batter_question:
        batters = _all_batters(context)
        if team:
            batters = [player for player in batters if str(player.get("team_id")) == str(team.get("id"))]
            team_innings = [innings for innings in context["innings"] if str(innings.get("team_id")) == str(team.get("id"))]
            if not team_innings:
                return _answer(
                    f"No {team['name']} batting deliveries have been processed by checkpoint {context['delivery_index']}; its batting scorecard is unavailable at this replay point.",
                    [("Replay checkpoint", f"{context['delivery_index']} of {context['total_deliveries']} deliveries")],
                    "low",
                    ["Future deliveries are excluded from this answer."],
                )
        if not batters:
            return _answer("No batter scorecard is available from the deliveries processed at this replay checkpoint.", confidence="low", limitations=["The replay checkpoint contains no batting deliveries."])
        best = max(batters, key=lambda player: (player["runs"], player["strike_rate"], player["balls"]))
        return _answer(
            f"{best['name']} of {_team_display(best.get('team'))} is the leading batter in the processed scorecard with {best['runs']} runs from {best['balls']} balls (strike rate {best['strike_rate']}).",
            _evidence_for_batter(best),
            "high" if best["balls"] >= 5 else "medium",
            ["Figures include only deliveries processed through this replay checkpoint."],
        )

    if "bowler" in normalized and "advantage" in normalized:
        bowlers = _all_bowlers(context)
        if not bowlers:
            return _answer(
                "No bowler has an observed advantage yet because no bowling deliveries are included in this replay checkpoint.",
                confidence="low",
                limitations=["No processed bowling figures are available."],
            )
        best = min(bowlers, key=lambda player: (player["economy"], -player["wickets"]))
        return _answer(
            f"{best['name']} of {_team_display(best.get('team'))} has the strongest recorded control by economy so far: {best['economy']} runs per over, with {best['wickets']} wicket(s). Economy is a checkpoint comparison, not a prediction.",
            [
                ("Bowling team", _team_display(best.get("team"))),
                ("Overs", best["overs"]),
                ("Runs conceded", best["runs"]),
                ("Wickets", best["wickets"]),
                ("Economy", best["economy"]),
            ],
            "medium" if best["legal_balls"] >= 6 else "low",
            ["A short observed spell cannot establish future advantage."],
        )

    if any(term in normalized for term in ("most wickets", "highest wickets", "best bowling", "best bowling figures", "who took wickets", "who took the most")) and ("wicket" in normalized or "bowling" in normalized):
        bowlers = _all_bowlers(context)
        if not bowlers:
            return _answer("No bowler has recorded figures in the deliveries processed so far.", confidence="low", limitations=["No bowling deliveries are available at this checkpoint."])
        best = max(bowlers, key=lambda player: (player["wickets"], -player["runs"], -player["economy"]))
        return _answer(
            f"{best['name']} of {_team_display(best.get('team'))} has the most credited wickets so far: {best['wickets']} for {best['runs']} in {best['overs']} overs.",
            [
                ("Bowling team", _team_display(best.get("team"))),
                ("Innings bowled", best["innings"]["innings"]),
                ("Wickets", best["wickets"]),
                ("Runs conceded", best["runs"]),
                ("Overs", best["overs"]),
                ("Economy", best["economy"]),
            ],
            "high" if best["legal_balls"] >= 6 else "medium",
            ["Only wickets credited to the bowler are counted; all figures stop at the current checkpoint."],
        )

    if "economy" in normalized:
        bowlers = _all_bowlers(context)
        mentioned = _find_players(question, context, "bowler")
        if mentioned:
            candidates = mentioned
        elif team:
            candidates = [player for player in bowlers if str(player.get("team_id")) == str(team.get("id"))]
        else:
            candidates = bowlers
        if not candidates:
            return _answer("No matching bowler economy is available from deliveries processed at this checkpoint.", confidence="low", limitations=["The named player or team has no observed bowling figures yet."])
        best = min(candidates, key=lambda player: (player["economy"], -player["wickets"]))
        return _answer(
            f"{best['name']} of {_team_display(best.get('team'))} has the lowest recorded economy among the requested bowlers: {best['economy']} runs per over ({best['runs']} runs from {best['overs']} overs).",
            [
                ("Bowling team", _team_display(best.get("team"))),
                ("Innings bowled", best["innings"]["innings"]),
                ("Runs conceded", best["runs"]),
                ("Overs", best["overs"]),
                ("Economy", best["economy"]),
            ],
            "high" if best["legal_balls"] >= 6 else "medium",
            ["Economy is calculated only from legal deliveries processed so far."],
        )

    if "strike rate" in normalized or "strike-rate" in question.casefold():
        batters = _find_players(question, context, "batter")
        if not batters and situation:
            batters = [
                {**player, "role": "batter", "innings": next(
                    (innings for innings in context["innings"] if innings["innings"] == situation["innings"]),
                    {},
                )}
                for player in situation.get("current_batters", [])
            ]
        if not batters:
            name = _named_player_phrase(question)
            subject = f" for {name}" if name else ""
            return _answer(
                f"No processed batting figures{subject} are available at replay checkpoint {context['delivery_index']}.",
                [("Replay checkpoint", f"{context['delivery_index']} of {context['total_deliveries']} deliveries")],
                "low",
                ["A player may not have batted yet, or may not appear in the selected fixture."],
            )
        batter = batters[-1]
        return _answer(
            f"{batter['name']} of {_team_display(batter.get('team'))} has a strike rate of {batter['strike_rate']} from {batter['runs']} runs off {batter['balls']} balls in innings {batter['innings']['innings']}.",
            _evidence_for_batter(batter),
            "high" if batter["balls"] >= 5 else "medium",
            ["Only deliveries processed through this replay checkpoint are included."],
        )

    if "struggl" in normalized or "struggle" in normalized or "fail" in normalized:
        batters = _find_players(question, context, "batter")
        if not batters:
            name = _named_player_phrase(question)
            subject = f" {name}" if name else " the named player"
            return _answer(
                f"I cannot establish why{subject} struggled: no batting deliveries for that player are present through replay checkpoint {context['delivery_index']}.",
                [("Replay checkpoint", f"{context['delivery_index']} of {context['total_deliveries']} deliveries")],
                "low",
                ["No conclusion about the player's performance can be supported without processed innings data."],
            )
        batter = batters[-1]
        innings = batter["innings"]
        dots = sum(
            item["batter"] == batter["name"]
            and item["innings"] == innings["innings"]
            and item["total_runs"] == 0
            and item["is_legal"]
            for item in context["delivery_history"]
        )
        dismissal = batter["dismissals"][-1]["dismissal"] if batter["dismissals"] else None
        if batter["balls"] < 5:
            reason = f"The sample is too small to conclude that {batter['name']} struggled: they faced only {batter['balls']} legal balls and scored {batter['runs']} runs."
        elif batter["strike_rate"] < 100:
            reason = f"{batter['name']} scored at a strike rate of {batter['strike_rate']} ({batter['runs']} from {batter['balls']} balls); {dots} of their faced deliveries were dot balls."
        else:
            reason = f"The processed figures do not show a low scoring rate for {batter['name']}: {batter['runs']} runs from {batter['balls']} balls gives a strike rate of {batter['strike_rate']}."
        if dismissal:
            reason += f" The recorded dismissal was {dismissal}."
        return _answer(
            reason,
            _evidence_for_batter(batter) + [("Dot balls", dots)] + ([("Dismissal", dismissal)] if dismissal else []),
            "medium" if batter["balls"] >= 5 else "low",
            ["These figures describe the replay checkpoint only; they do not establish cause or intent."],
        )

    if re.search(r"\b(?:compare|comparison|versus|vs)\b", normalized):
        players = _find_players(question, context)
        unique = []
        seen = set()
        for player in players:
            key = (player["role"], str(player.get("player_id")), player["innings"]["innings"])
            if key not in seen:
                unique.append(player)
                seen.add(key)
        if len(unique) >= 2:
            comparisons = []
            evidence = []
            for player in unique[:2]:
                if player["role"] == "batter":
                    comparisons.append(f"{player['name']} ({_team_display(player.get('team'))}): {player['runs']} runs from {player['balls']} balls, SR {player['strike_rate']}")
                    evidence.extend(_evidence_for_batter(player))
                else:
                    comparisons.append(f"{player['name']} ({_team_display(player.get('team'))}): {player['wickets']}/{player['runs']}, economy {player['economy']}")
                    evidence.extend([
                        ("Bowler", player["name"]),
                        ("Bowling team", _team_display(player.get("team"))),
                        ("Wickets / runs", f"{player['wickets']}/{player['runs']}"),
                        ("Economy", player["economy"]),
                    ])
            return _answer("At this checkpoint: " + "; ".join(comparisons) + ".", evidence, "high", ["Only innings with processed deliveries are compared."])

    over_match = re.search(r"\bover\s+(\d{1,2})\b", normalized)
    if "over" in normalized and any(term in normalized for term in ("run", "score", "momentum")):
        innings_candidates = context["innings"]
        if team:
            innings_candidates = [item for item in innings_candidates if str(item["team_id"]) == str(team["id"])]
        if over_match:
            number = str(int(over_match.group(1)))
            points = [(innings, point) for innings in innings_candidates for point in innings["over_scoring"] if point["over"] == number]
            if not points:
                return _answer(
                    f"No scoring data for over {number} is available through checkpoint {context['delivery_index']}.",
                    [("Replay checkpoint", f"{context['delivery_index']} of {context['total_deliveries']} deliveries")],
                    "low",
                    ["Overs not yet processed are intentionally excluded."],
                )
            details = [
                f"{_team_display(innings['batting_team'])} scored {point['runs']} runs"
                + (f" and lost {point['wickets']} wicket(s)" if point["wickets"] else "")
                + f" in innings {innings['innings']}"
                for innings, point in points
            ]
            return _answer(
                f"Over {number}: " + "; ".join(details) + " through the current checkpoint.",
                [
                    (f"{_team_display(innings['batting_team'])} innings {innings['innings']} over {number}", f"{point['runs']} runs, {point['wickets']} wickets")
                    for innings, point in points
                ],
                "high",
                ["This over total includes only deliveries already processed."],
            )
        if innings_candidates:
            items = []
            evidence = []
            for innings in innings_candidates:
                overs = innings["over_scoring"]
                if not overs:
                    continue
                summary = ", ".join(
                    f"{point['over']}:{point['runs']}" + (f" ({point['wickets']}w)" if point["wickets"] else "")
                    for point in overs
                )
                team_name = _team_display(innings["batting_team"])
                items.append(f"{team_name} innings {innings['innings']} overs: {summary}")
                evidence.append((f"{team_name} innings {innings['innings']} over runs", summary))
            if not items:
                return _answer("No over scoring data is available because no deliveries have been processed.", confidence="low", limitations=["No processed deliveries."])
            return _answer(
                "Over-wise runs through this replay checkpoint (over: runs; w marks wickets): " + "; ".join(items) + ".",
                evidence,
                "high",
                ["Unprocessed overs and future deliveries are excluded; only innings with processed overs are listed."],
            )

    if ("total" in normalized or "score" in normalized or "runs" in normalized) and (
        team or "team total" in normalized or "innings total" in normalized or "current score" in normalized
    ):
        selected = [
            innings for innings in context["innings"]
            if team is None or str(innings["team_id"]) == str(team["id"])
        ]
        if not selected:
            subject = team["name"] if team else "either team"
            return _answer(
                f"No innings total for {subject} is available from processed deliveries at checkpoint {context['delivery_index']}.",
                [("Replay checkpoint", f"{context['delivery_index']} of {context['total_deliveries']} deliveries")],
                "low",
                ["Do not treat unprocessed final scorecard totals as current replay data."],
            )
        evidence = []
        phrases = []
        for innings in selected:
            phrases.append(f"{_team_display(innings['batting_team'])}: {innings['runs']}/{innings['wickets']} in {innings['overs']} overs")
            evidence.append((f"{_team_display(innings['batting_team'])}, innings {innings['innings']}", f"{innings['runs']}/{innings['wickets']} in {innings['overs']} overs"))
        return _answer("Processed team totals: " + "; ".join(phrases) + ".", evidence, "high", ["Totals include only deliveries through the selected checkpoint."])

    if "wicket" in normalized and any(term in normalized for term in ("count", "how many", "total")):
        selected = [
            innings for innings in context["innings"]
            if team is None or str(innings["team_id"]) == str(team["id"])
        ]
        if selected:
            facts = [f"{_team_display(item['batting_team'])} have lost {item['wickets']} wickets in innings {item['innings']}" for item in selected]
            return _answer("Wicket count at this checkpoint: " + "; ".join(facts) + ".", [(f"{_team_display(item['batting_team'])} wickets", item["wickets"]) for item in selected], "high", ["Wickets from unprocessed deliveries are excluded."])

    return None


def _narrative_fallback(question: str, context: dict[str, Any]) -> dict[str, Any]:
    innings_match = re.search(r"\b(?:in|of) innings ([\w-]+)", question, flags=re.IGNORECASE)
    innings_label = innings_match.group(1) if innings_match else None
    player_role = "batter" if "batter narrative" in question.casefold() else (
        "bowler" if "bowler narrative" in question.casefold() else None
    )
    if player_role:
        player = next((
            item for item in _find_players(question, context, player_role)
            if innings_label is None or str(item["innings"]["innings"]) == innings_label
        ), None)
        if player is None:
            return _answer(
                "A player narrative is unavailable because the selected player has no matching scorecard entry at this checkpoint.",
                confidence="low",
                limitations=["The selected player or innings could not be matched to the processed delivery context."],
            )

        innings = player["innings"]
        rows = [
            row for row in context["delivery_history"]
            if str(row["innings"]) == str(innings["innings"])
            and (
                row["batter"] == player["name"] if player_role == "batter"
                else row["bowler"] == player["name"]
            )
        ]
        if not rows:
            return _answer(
                f"No deliveries for {player['name']} are present in innings {innings['innings']} at this checkpoint.",
                confidence="low",
                limitations=["Player-specific delivery progression is unavailable."],
            )

        first = rows[0]
        over_groups: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            over_groups.setdefault(str(row["over"]), []).append(row)
        innings_complete = str(innings["innings"]) in context["completed_innings"]
        if player_role == "batter":
            runs = sum(row["batter_runs"] for row in rows)
            balls = sum(int(row["batter_faced_ball"]) for row in rows)
            boundaries = sum(int(row["four"]) + int(row["six"]) for row in rows)
            best_over = max(
                over_groups.items(),
                key=lambda item: sum(row["batter_runs"] for row in item[1]),
            )
            strike_rate = round(runs * 100 / balls, 2) if balls else 0.0
            entry = (
                f"{player['name']} first faced a recorded delivery at {first['over_ball'] or 'an unavailable ball label'}, "
                f"with the team at {first['score_before']}/{first['wickets_before']}."
            )
            performance = (
                f"They made {runs} from {balls} balls (strike rate {strike_rate}), including "
                f"{boundaries} boundary deliveries. Their most productive over was over {best_over[0]}, "
                f"with {sum(row['batter_runs'] for row in best_over[1])} runs from their bat."
            )
            dismissal = next(
                (detail for detail in innings["wicket_details"] if detail["batter"] == player["name"]),
                None,
            )
            if dismissal:
                conclusion = (
                    f"The innings ended at {dismissal['over_ball'] or 'an unavailable delivery'} "
                    f"({dismissal['dismissal']}); the recorded scorecard contribution was {runs} runs."
                )
            elif innings_complete:
                conclusion = f"They were not dismissed in the recorded innings, which concluded at {innings['runs']}/{innings['wickets']}."
            else:
                conclusion = f"They remained not out at this checkpoint; the innings is still in progress at {innings['runs']}/{innings['wickets']}."
            partnerships = [
                partnership for partnership in innings["partnerships"]
                if any(
                    contribution.get("name") == player["name"]
                    for contribution in partnership.get("contributions", [])
                )
            ]
            partnership_text = (
                f" Partnership evidence: {max(partnerships, key=lambda row: row.get('runs', 0))['runs']}-run stand."
                if partnerships else ""
            )
            answer = f"{entry} {performance}{partnership_text} {conclusion}"
            limitations = []
            if balls < 6:
                limitations.append("The batter has fewer than six balls faced; rate-based assessment is low-sample.")
            if not partnerships:
                limitations.append("A partnership contribution could not be established from the available scorecard data.")
            return _answer(
                answer,
                [
                    ("Batter", player["name"]),
                    ("Runs / balls", f"{runs} / {balls}"),
                    ("Strike rate", strike_rate),
                    ("Entry score", f"{first['score_before']}/{first['wickets_before']}"),
                    ("Boundary deliveries", boundaries),
                ],
                "medium" if balls >= 6 else "low",
                limitations,
            )

        bowler = player
        over_summaries = []
        for over, deliveries in sorted(over_groups.items(), key=lambda item: int(item[0])):
            runs = sum(row["bowler_runs"] for row in deliveries)
            batters: dict[str, int] = {}
            for row in deliveries:
                batter_name = row["batter"] or "Batter unavailable"
                batters[batter_name] = batters.get(batter_name, 0) + row["batter_runs"]
            leading_batter = max(batters.items(), key=lambda item: item[1]) if batters else None
            wickets = sum(int(row["bowler_wicket"]) for row in deliveries)
            dots = sum(int(row["is_legal"] and row["total_runs"] == 0) for row in deliveries)
            boundaries = sum(int(row["four"] or row["six"]) for row in deliveries)
            extras = sum(row["extras"] for row in deliveries)
            over_summaries.append(
                f"Over {over} conceded {runs} bowler-charged runs, with {dots} dots, "
                f"{boundaries} boundary deliveries, {extras} extras and {wickets} bowler-credit wickets"
                + (f"; {leading_batter[0]} scored {leading_batter[1]} off the bat" if leading_batter else "")
                + ("." if runs <= 6 else ", releasing scoring pressure.")
            )
        bowler_runs = sum(row["bowler_runs"] for row in rows)
        legal_balls = sum(int(row["is_legal"]) for row in rows)
        wickets = sum(int(row["bowler_wicket"]) for row in rows)
        economy = _economy(bowler_runs, legal_balls)
        summary = (
            f"{bowler['name']} entered the attack in over {first['over']} with the innings at "
            f"{first['score_before']}/{first['wickets_before']}. "
            + " ".join(over_summaries)
            + f" Across the recorded spell they conceded {bowler_runs} from {legal_balls} legal balls "
            f"(economy {economy}) and took {wickets} bowler-credit wicket(s)."
        )
        limitations = []
        if not innings_complete:
            limitations.append("The innings and bowling spell may be incomplete at this checkpoint.")
        if legal_balls < 6:
            limitations.append("The bowler has fewer than one legal over; economy comparisons have a small sample.")
        return _answer(
            summary,
            [
                ("Bowler", bowler["name"]),
                ("Runs conceded / wickets", f"{bowler_runs} / {wickets}"),
                ("Legal balls", legal_balls),
                ("Economy", economy),
            ],
            "medium" if legal_balls >= 6 else "low",
            limitations,
        )

    innings = next(
        (item for item in context["innings"] if innings_label is None or str(item["innings"]) == innings_label),
        None,
    )
    if innings is None:
        return _answer(
            "An innings narrative is unavailable because no deliveries have been processed for this innings.",
            confidence="low",
            limitations=["No delivery-backed innings context is available."],
        )

    overs = innings["over_scoring"]
    batting_team = _team_display(innings["batting_team"])
    bowling_team = _team_display(innings["bowling_team"])
    opening = (
        f"The first recorded over produced {overs[0]['runs']} runs and {overs[0]['wickets']} wicket(s)."
        if overs else "No complete over summary is available yet."
    )
    peak_over = max(overs, key=lambda row: row["runs"]) if overs else None
    wicket_over = max(overs, key=lambda row: row["wickets"]) if overs else None
    batters = innings["batting_scorecard"]
    bowlers = innings["bowling_scorecard"]
    leading_batters = ", ".join(
        f"{player['name']} {player['runs']} ({player['balls']})"
        for player in batters[:2]
    ) or "batter figures unavailable"
    leading_bowlers = ", ".join(
        f"{player['name']} {player['wickets']}/{player['runs']}"
        for player in bowlers[:2]
    ) or "bowling figures unavailable"
    wickets = innings["wicket_details"]
    wicket_sequence = "; ".join(
        f"{item['batter']} at {item['over_ball'] or 'an unavailable delivery'}"
        for item in wickets[-4:]
    )
    partnership = max(innings["partnerships"], key=lambda row: row.get("runs", 0), default=None)
    complete = str(innings["innings"]) in context["completed_innings"]
    paragraphs = [
        f"{batting_team} reached {innings['runs']}/{innings['wickets']} after {innings['overs']} overs "
        f"(run rate {innings['run_rate']}). {opening}",
        (
            f"The highest-scoring recorded over was over {peak_over['over']} ({peak_over['runs']} runs); "
            f"over {wicket_over['over']} contained the most wickets ({wicket_over['wickets']})."
            if peak_over and wicket_over else "Over-by-over changes cannot yet be compared."
        ),
        f"Batting contributions were led by {leading_batters}. The defending side's leading figures were {leading_bowlers}.",
    ]
    if wicket_sequence:
        paragraphs.append(f"Recorded dismissals included {wicket_sequence}.")
    if partnership:
        paragraphs.append(
            f"The largest recorded partnership was {partnership.get('runs')} runs "
            f"between {' and '.join(partnership.get('batters') or [])}."
        )
    if str(innings["innings"]) != str(context["innings"][0]["innings"]):
        first_innings = context["innings"][0]
        target = first_innings["runs"] + 1
        situation = context["current_situation"]
        required = situation["runs_required"] if situation and str(situation["innings"]) == str(innings["innings"]) else None
        paragraphs.append(
            f"The chase target was {target}. "
            + (
                f"At this checkpoint {required} runs remained from {situation['balls_remaining']} balls."
                if required is not None and situation else "The required rate at this innings point is unavailable."
            )
        )
    paragraphs.append(
        f"{'This innings is complete' if complete else 'This innings is provisional at the selected checkpoint'}; "
        f"the account uses only recorded deliveries and does not infer tactical causes."
    )
    answer = " ".join(paragraphs)
    return _answer(
        answer,
        [
            ("Innings total", f"{innings['runs']}/{innings['wickets']}"),
            ("Overs / run rate", f"{innings['overs']} / {innings['run_rate']}"),
            ("Batting leaders", leading_batters),
            ("Bowling leaders", leading_bowlers),
        ],
        "medium" if overs else "low",
        [] if overs else ["Over-level delivery summaries are not available at this checkpoint."],
    )


def _reasoning_fallback(question: str, context: dict[str, Any]) -> dict[str, Any]:
    if context.get("narrative_mode"):
        return _narrative_fallback(question, context)
    normalized = _words(question)
    innings = context["current_situation"]
    if "momentum" in normalized and innings:
        current = next((item for item in context["innings"] if item["innings"] == innings["innings"]), None)
        overs = current["over_scoring"] if current else []
        recent = overs[-4:]
        evidence = [
            (f"Over {item['over']} runs", item["runs"])
            for item in recent
        ]
        evidence.extend(
            (f"Over {item['over']} wickets", item["wickets"])
            for item in recent if item["wickets"]
        )
        evidence.extend(
            (f"Over {item['over']} boundaries", item["boundaries"])
            for item in recent
        )
        if len(recent) >= 2:
            previous, latest = recent[-2], recent[-1]
            delta = latest["runs"] - previous["runs"]
            direction = "increased" if delta > 0 else "decreased" if delta < 0 else "was unchanged"
            answer = (
                f"Scoring momentum {direction} from {previous['runs']} runs in over {previous['over']} "
                f"to {latest['runs']} in over {latest['over']} (a {abs(delta)}-run change). "
                f"The current innings rate is {innings['run_rate']} runs per over at {innings['score']}/{innings['wickets']}."
            )
            if latest["boundaries"] != previous["boundaries"]:
                answer += (
                    f" The later over contained {latest['boundaries']} boundary(ies) "
                    f"({latest['boundary_runs']} boundary runs), compared with {previous['boundaries']} "
                    f"({previous['boundary_runs']} boundary runs) in the prior over."
                )
            if latest["dot_balls"] != previous["dot_balls"]:
                answer += f" Dot balls changed from {previous['dot_balls']} to {latest['dot_balls']}."
            if latest["wickets"]:
                answer += f" {latest['wickets']} wicket(s) also fell in over {latest['over']}."
        elif recent:
            item = recent[-1]
            answer = f"Only one over has scoring data at this checkpoint: over {item['over']} produced {item['runs']} runs. There is not yet a prior over to compare for a momentum change."
        else:
            answer = "No over scoring has been processed yet, so a momentum change cannot be assessed."
        return _answer(answer, evidence, "medium", ["Over totals describe observed deliveries and do not predict future scoring."])

    if "another wicket" in normalized or "lose" in normalized:
        return _deterministic_answer(question, context) or _answer(
            "A wicket scenario cannot be calculated until the replay has processed at least one delivery.",
            confidence="low",
            limitations=["No current match checkpoint is available."],
        )

    situation = innings
    if situation:
        summary = (
            f"The current checkpoint has {_team_display(situation['batting_team'])} at "
            f"{situation['score']}/{situation['wickets']} after {situation['overs']} overs, "
            f"scoring at {situation['run_rate']} runs per over."
        )
        evidence = [
            ("Current score", f"{situation['score']}/{situation['wickets']}"),
            ("Overs", situation["overs"]),
            ("Run rate", situation["run_rate"]),
        ]
    else:
        summary = f"No deliveries have been processed at replay checkpoint {context['delivery_index']}."
        evidence = []
    return _answer(
        summary,
        evidence,
        "low",
        ["A language model is unavailable; this answer is limited to direct checkpoint facts."],
    )


def _reasoning_evidence(question: str, context: dict[str, Any]) -> list[dict[str, str]]:
    evidence: list[tuple[str, Any]] = []
    situation = context["current_situation"]
    players = _find_players(question, context)
    for player in players[:3]:
        if player["role"] == "batter":
            evidence.extend(_evidence_for_batter(player))
        else:
            evidence.extend([
                ("Bowler", player["name"]),
                ("Bowling team", _team_display(player.get("team"))),
                ("Wickets / runs conceded", f"{player['wickets']} / {player['runs']}"),
                ("Overs / economy", f"{player['overs']} / {player['economy']}"),
            ])
    if "momentum" in _words(question) and context["innings"]:
        for point in context["innings"][-1]["over_scoring"][-4:]:
            evidence.append((f"Over {point['over']} runs", point["runs"]))
            if point["wickets"]:
                evidence.append((f"Over {point['over']} wickets", point["wickets"]))
            evidence.append((f"Over {point['over']} boundaries", point["boundaries"]))
            evidence.append((f"Over {point['over']} dot balls", point["dot_balls"]))
    if situation:
        evidence.extend([
            ("Current batting team", _team_display(situation["batting_team"])),
            ("Current score", f"{situation['score']}/{situation['wickets']}"),
            ("Overs", situation["overs"]),
            ("Run rate", situation["run_rate"]),
        ])
        if situation["target"] is not None:
            evidence.extend([
                ("Runs required", situation["runs_required"]),
                ("Balls remaining", situation["balls_remaining"]),
                ("Required run rate", situation["required_run_rate"] if situation["required_run_rate"] is not None else "Unavailable"),
            ])
    else:
        evidence.append(("Replay checkpoint", f"{context['delivery_index']} of {context['total_deliveries']} deliveries"))

    unique = []
    seen = set()
    for label, value in evidence:
        key = (label, str(value))
        if key not in seen:
            unique.append({"label": label, "value": str(value)})
            seen.add(key)
        if len(unique) >= 12:
            break
    return unique


def _llm_context(context: dict[str, Any]) -> dict[str, Any]:
    is_narrative = context.get("narrative_mode", False)
    return {
        "mode": context["mode"],
        "fixture_id": context["fixture_id"],
        "checkpoint": {
            "delivery_index": context["delivery_index"],
            "total_deliveries": context["total_deliveries"],
            "is_complete": context["is_complete"],
        },
        "completed_innings": context["completed_innings"],
        "match_format": context["match_format"],
        "teams": context["teams"],
        "innings_scorecards": [
            {
                "innings": innings["innings"],
                "batting_team": innings["batting_team"],
                "bowling_team": innings["bowling_team"],
                "score": f"{innings['runs']}/{innings['wickets']}",
                "overs": innings["overs"],
                "run_rate": innings["run_rate"],
                "batting_scorecard": innings["batting_scorecard"],
                "bowling_scorecard": innings["bowling_scorecard"],
                "partnerships": innings["partnerships"],
                "wicket_details": innings["wicket_details"] if is_narrative else innings["wicket_details"][-12:],
                "over_scoring": innings["over_scoring"] if is_narrative else innings["over_scoring"][-12:],
            }
            for innings in context["innings"]
        ],
        "current_situation": context["current_situation"],
        "recent_delivery_history": (
            context["delivery_history"]
            if is_narrative
            else context["delivery_history"][-36:]
        ),
        "narrative_facts": context["narrative_facts"] if is_narrative else None,
        "processed_delivery_history_count": len(context["delivery_history"]),
    }


def _provider_config(provider: str) -> dict[str, Any] | None:
    if provider == "ollama":
        return {
            "url": f"{settings.ollama_base_url.rstrip('/')}/api/chat",
            "model": settings.ollama_model,
            "key": None,
        }
    if provider == "cerebras" and settings.cerebras_api_key:
        return {"url": "https://api.cerebras.ai/v1/chat/completions", "model": settings.cerebras_model, "key": settings.cerebras_api_key}
    if provider == "groq" and settings.groq_api_key:
        return {"url": "https://api.groq.com/openai/v1/chat/completions", "model": settings.groq_model, "key": settings.groq_api_key}
    if provider == "gemini" and settings.gemini_api_key:
        return {
            "url": f"https://generativelanguage.googleapis.com/v1beta/models/{settings.gemini_model}:generateContent",
            "model": settings.gemini_model,
            "key": settings.gemini_api_key,
        }
    return None


def _providers_to_try() -> list[str]:
    configured = settings.llm_provider.casefold()
    if configured not in {"ollama", "cerebras", "gemini", "groq"}:
        configured = "ollama"
    providers = []
    if _provider_config(configured):
        providers.append(configured)
    if len(providers) < 2:
        fallback_order = ["cerebras", "gemini", "groq", "ollama"]
        fallback = next(
            (name for name in fallback_order if name != configured and _provider_config(name)),
            None,
        )
        if fallback:
            providers.append(fallback)
    return providers


def _parse_provider_content(provider: str, payload: dict[str, Any]) -> str:
    if provider == "ollama":
        return payload["message"]["content"]
    if provider == "gemini":
        return "".join(part["text"] for part in payload["candidates"][0]["content"]["parts"] if "text" in part)
    return payload["choices"][0]["message"]["content"]


def _validate_narrative_entities(answer: str, context: dict[str, Any]) -> None:
    allowed = {
        _words(team.get("name", ""))
        for team in context.get("teams", [])
        if isinstance(team, dict) and team.get("name")
    }
    for innings in context.get("innings_scorecards", []):
        if not isinstance(innings, dict):
            continue
        for group in ("batting_scorecard", "bowling_scorecard"):
            allowed.update(
                _words(player.get("name", ""))
                for player in innings.get(group, [])
                if isinstance(player, dict) and player.get("name")
            )
    for delivery in context.get("delivery_records", []):
        if not isinstance(delivery, dict):
            continue
        allowed.update(
            _words(delivery[key])
            for key in ("batter", "non_striker", "bowler")
            if delivery.get(key)
        )
        wicket = delivery.get("wicket")
        dismissal = wicket.get("dismissal", "") if isinstance(wicket, dict) else ""
        allowed.update(
            _words(match.group(0))
            for match in re.finditer(
                r"\b[A-Z][A-Za-z.'’-]+(?:\s+[A-Z][A-Za-z.'’-]+)+\b",
                dismissal,
            )
        )
    allowed.update({
        "the story", "the innings", "the over", "the chase", "the target",
        "powerplay overs", "middle overs", "death overs", "first innings",
        "second innings", "required rate", "run rate", "strike rate",
    })
    sentence_openers = {"the", "this", "that", "in", "at", "from", "during", "while", "as", "when", "with", "for", "if", "however", "after", "before", "over", "across", "each", "by", "given"}
    for sentence in re.split(r"(?<=[.!?])\s+", answer):
        for match in re.finditer(
            r"\b[A-Z][A-Za-z.'’-]+(?:\s+[A-Z][A-Za-z.'’-]+)+\b",
            sentence,
        ):
            phrase = _words(re.sub(r"['’]s$", "", match.group(0), flags=re.IGNORECASE))
            if phrase in allowed:
                continue
            if phrase.split()[0] in sentence_openers:
                continue
            raise ValueError(
                "Narrative response includes a player or team name absent from the supplied match context."
            )


async def _provider_request(
    provider: str,
    context: dict[str, Any],
    question: str,
    history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    config = _provider_config(provider)
    if config is None:
        raise ProviderFailure(provider, f"{provider} is not configured.")

    model_context = _llm_context(context)
    messages = [{"role": "system", "content": SYSTEM_INSTRUCTION}]
    prior_questions = "\n".join(
        f"- At delivery {turn['delivery_index']}: {turn['question']}"
        for turn in (history or [])[-4:]
    )
    user_content = (
        "Use the following checkpoint-scoped match context to answer the question. "
        "Do not infer statistics from unprocessed deliveries.\n"
        + (f"RECENT USER QUESTIONS (reference resolution only):\n{prior_questions}\n" if prior_questions else "")
        + f"MATCH_CONTEXT={json.dumps(model_context, ensure_ascii=True, separators=(',', ':'))}\n"
        + f"QUESTION={question}"
    )
    messages.append({
        "role": "user",
        "content": user_content,
    })
    if provider == "ollama":
        body = {
            "model": config["model"],
            "messages": messages,
            "stream": False,
            "format": MatchAnswer.model_json_schema(),
            "options": {"temperature": 0.1, "num_predict": settings.llm_max_output_tokens},
        }
        headers = {}
        url = config["url"]
    elif provider == "gemini":
        body = {
            "contents": [
                {"role": "user", "parts": [{"text": "\n\n".join(
                    f"{message['role'].upper()}: {message['content']}" for message in messages
                )}]}
            ],
            "generationConfig": {
                "temperature": 0.1,
                "maxOutputTokens": settings.llm_max_output_tokens,
                "responseMimeType": "application/json",
            },
        }
        headers = {}
        url = config["url"]
    else:
        body = {
            "model": config["model"],
            "messages": messages,
            "temperature": 0.1,
            "max_tokens": settings.llm_max_output_tokens,
            "response_format": {"type": "json_object"},
        }
        headers = {"Authorization": f"Bearer {config['key']}"}
        url = config["url"]

    retries = max(1, settings.llm_retry_attempts)
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
                response = await client.post(
                    url,
                    headers=headers,
                    params={"key": config["key"]} if provider == "gemini" else None,
                    json=body,
                )
            if response.status_code in {408, 429, 500, 502, 503, 504} and attempt + 1 < retries:
                await asyncio.sleep(0.25 * (2 ** attempt))
                continue
            response.raise_for_status()
            payload = response.json()
            raw_content = _parse_provider_content(provider, payload)
            if not isinstance(raw_content, str):
                raise ValueError("Provider content is not text.")
            decoded = raw_content.strip()
            decoded = re.sub(r"^```(?:json)?\s*|\s*```$", "", decoded, flags=re.IGNORECASE)
            start, end = decoded.find("{"), decoded.rfind("}")
            if start < 0 or end < start:
                raise ValueError("Provider response is not a JSON object.")
            parsed = MatchAnswer.model_validate_json(decoded[start:end + 1])
            cleaned = _clean_text(parsed.answer)
            if not _is_complete_answer(cleaned):
                raise ValueError("Provider returned an incomplete answer.")
            context_numbers = set(re.findall(r"\d+(?:\.\d+)?", json.dumps(model_context, ensure_ascii=True)))
            answer_numbers = set(re.findall(r"\d+(?:\.\d+)?", cleaned))
            if not answer_numbers.issubset(context_numbers):
                raise ValueError("Provider answer includes statistics outside the supplied match context.")
            if model_context.get("narrative_mode"):
                _validate_narrative_entities(cleaned, model_context)
            answer = parsed.model_dump()
            answer["answer"] = cleaned
            answer["evidence"] = _reasoning_evidence(question, context)
            return answer
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            last_error = ProviderFailure(provider, _provider_http_message(provider, status), status)
            if status in {408, 429, 500, 502, 503, 504} and attempt + 1 < retries:
                await asyncio.sleep(0.25 * (2 ** attempt))
                continue
            raise last_error from exc
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError, ValidationError) as exc:
            last_error = ProviderFailure(provider, f"{provider} returned an invalid response or is unavailable.")
            if attempt + 1 < retries:
                await asyncio.sleep(0.25 * (2 ** attempt))
                continue
            raise last_error from exc
    raise last_error or ProviderFailure(provider, f"{provider} is unavailable.")


def _provider_http_message(provider: str, status: int) -> str:
    if status == 401:
        return f"{provider} rejected its API key."
    if status in {403, 404}:
        return f"{provider} rejected the configured model or access."
    if status == 429:
        return f"{provider} rate limit or usage quota was reached."
    if status >= 500:
        return f"{provider} is temporarily unavailable."
    return f"{provider} rejected the request (HTTP {status})."


def _cache_key(
    question: str,
    context: dict[str, Any],
    provider: str,
    history: list[dict[str, Any]],
) -> str:
    data = {
        "provider": provider,
        "model": (_provider_config(provider) or {}).get("model"),
        "question": _words(question),
        "fixture_id": context["fixture_id"],
        "delivery_index": context["delivery_index"],
        "context": _llm_context(context),
        "history": history[-4:],
    }
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def _cache_get(key: str) -> dict[str, Any] | None:
    entry = _answer_cache.get(key)
    if entry is None:
        return None
    expires, value = entry
    if expires <= time.monotonic():
        del _answer_cache[key]
        return None
    _answer_cache.move_to_end(key)
    return value.copy()


def _cache_set(key: str, value: dict[str, Any]) -> None:
    _answer_cache[key] = (time.monotonic() + settings.llm_cache_ttl_seconds, value.copy())
    _answer_cache.move_to_end(key)
    while len(_answer_cache) > _CACHE_LIMIT:
        _answer_cache.popitem(last=False)


async def answer_match_question(
    question: str,
    match_state: dict[str, Any],
    history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    context = build_match_context(match_state)
    is_live = context["mode"] == "LIVE MATCH SNAPSHOT"
    mode_phrase = "current live snapshot" if is_live else "replay checkpoint"

    def label_text(text: str) -> str:
        for old, new in (
            ("replay checkpoint", mode_phrase),
            ("Replay checkpoint", "Live snapshot"),
            ("this replay", "this live snapshot"),
            ("the replay", "the live snapshot"),
            ("through this checkpoint", "through this live snapshot"),
        ):
            text = text.replace(old, new)
        return text

    def label_response(result: dict[str, Any]) -> dict[str, Any]:
        if not is_live:
            return result
        for field in ("answer", "answer_mode"):
            value = result.get(field)
            if isinstance(value, str):
                result[field] = label_text(value)
        result["limitations"] = [
            label_text(item) if isinstance(item, str) else item
            for item in result.get("limitations", [])
        ]
        for item in result.get("evidence", []):
            if isinstance(item, dict):
                for key in ("label", "value"):
                    if isinstance(item.get(key), str):
                        item[key] = label_text(item[key])
        return result

    direct = None if context.get("narrative_mode") else _deterministic_answer(question, context)
    if direct:
        return label_response({**direct, "answer_mode": "MATCHSENSE · MATCH DATA", "delivery_index": context["delivery_index"]})

    fallback = _reasoning_fallback(question, context)
    conversation = history[-4:] if history else []
    providers = _providers_to_try()
    if not providers:
        return label_response({
            **fallback,
            "answer_mode": "RULE-BASED · NO LLM PROVIDER CONFIGURED",
            "delivery_index": context["delivery_index"],
        })

    primary = providers[0]
    key = _cache_key(question, context, primary, conversation)
    cached = _cache_get(key)
    if cached:
        return label_response({
            **cached,
            "answer_mode": f"{cached['answer_mode']} · CACHED",
            "delivery_index": context["delivery_index"],
        })

    errors = []
    for provider in providers:
        try:
            result = await _provider_request(provider, context, question, conversation)
            result = MatchAnswer.model_validate(result).model_dump()
            response = {
                **result,
                "answer_mode": f"{provider.upper()} · CHECKPOINT GROUNDED",
                "delivery_index": context["delivery_index"],
            }
            _cache_set(key, response)
            return label_response(response)
        except ProviderFailure as exc:
            errors.append(str(exc))
            logger.warning("MatchSense provider %s failed: %s", provider, exc)

    fallback["limitations"] = [
        *fallback["limitations"],
        "LLM providers were unavailable; this response uses deterministic checkpoint facts.",
        *errors[:2],
    ]
    return label_response({
        **fallback,
        "answer_mode": "RULE-BASED · PROVIDER UNAVAILABLE",
        "delivery_index": context["delivery_index"],
    })
