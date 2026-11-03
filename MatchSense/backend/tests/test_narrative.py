import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.services.narrative_service import (
    _SECTION_TITLES,
    _call_provider,
    _fallback_sections,
    _narrative_providers_to_try,
    _report_prose,
    _validate_narrative_statistics,
    _validated_narrative_output,
    build_innings_facts,
    generate_narrative,
)
from app.services.match_chat_service import _validate_narrative_entities
from app.services.replay_engine import MatchStateEngine

client = TestClient(app)

FIXTURE = {
    "id": 216,
    "match_format": "T20",
    "localteam": {"id": 10, "name": "India"},
    "visitorteam": {"id": 43, "name": "West Indies"},
    "deliveries": [
        {
            "over_ball": f"0.{ball}",
            "innings": "1",
            "team_id": 43,
            "batter_id": 109 if ball < 4 else 110,
            "batter": "Batter A" if ball < 4 else "Batter B",
            "non_striker": "Batter B" if ball < 4 else "Batter A",
            "bowler_id": 58,
            "bowler": "Bowler A",
            "runs_batter": runs,
            "runs_total": runs,
            "runs_bowler": runs,
            "is_legal": True,
            "is_wicket": ball == 6,
            "bowler_wicket": ball == 6,
            "dismissed_batter": "Batter B",
            "dismissal": "Bowled" if ball == 6 else None,
            "four": runs == 4,
            "six": False,
        }
        for ball, runs in enumerate([1, 0, 4, 2, 1, 0], 1)
    ],
}


def india_innings_regression_fixture():
    players = {
        "Abhishek Sharma": 1,
        "Ishan Kishan": 2,
        "Shreyas Iyer": 3,
        "Batter C": 4,
        "Batter D": 5,
        "Batter F": 6,
        "Batter G": 7,
        "Harshit Rana": 8,
        "Batter H": 9,
    }
    runs_by_event = {index: 0 for index in range(1, 121)}
    for index, runs in enumerate([1] * 8 + [0] * 4, 1):
        runs_by_event[index] = runs
    for index, runs in enumerate([6, 6, 6, 6, 6, 5] + [0] * 6, 13):
        runs_by_event[index] = runs
    for index in range(26, 47):
        runs_by_event[index] = 1
    for index in range(47, 52):
        runs_by_event[index] = 4
    for index in range(61, 68):
        runs_by_event[index] = 4
    for index in range(68, 77):
        runs_by_event[index] = 4
    runs_by_event[77] = 1
    for index in range(105, 115):
        runs_by_event[index] = 4

    wickets = {
        24: "Abhishek Sharma",
        50: "Batter C",
        60: "Batter D",
        104: "Ishan Kishan",
        108: "Batter F",
        115: "Batter G",
        120: "Harshit Rana",
    }
    deliveries = []
    for index in range(1, 121):
        if index <= 24:
            batter, non_striker = "Abhishek Sharma", "Ishan Kishan"
        elif index <= 50:
            batter, non_striker = (
                ("Ishan Kishan", "Batter C") if index < 47
                else ("Batter C", "Ishan Kishan")
            )
        elif index <= 60:
            batter, non_striker = (
                ("Batter D", "Ishan Kishan") if index in {51, 60}
                else ("Ishan Kishan", "Batter D")
            )
        elif index <= 104:
            batter = "Ishan Kishan" if index <= 67 else "Shreyas Iyer"
            if index == 104:
                batter = "Shreyas Iyer"
            non_striker = "Shreyas Iyer" if batter == "Ishan Kishan" else "Ishan Kishan"
        elif index <= 120:
            batter = (
                "Batter F" if index == 108
                else "Batter G" if index == 115
                else "Harshit Rana" if index == 120
                else "Batter H"
            )
            non_striker = "Shreyas Iyer"
        wicket = index in wickets
        is_bye = index == 30
        over_number = (index - 1) // 6
        bowler = (
            "Bowler A" if over_number < 6
            else "Bowler B" if over_number < 12
            else "Bowler C" if over_number < 17
            else "Bowler D"
        )
        runs = runs_by_event[index]
        total_runs = runs + int(is_bye)
        deliveries.append({
            "over_ball": f"{over_number}.{(index - 1) % 6 + 1}",
            "innings": "1",
            "team_id": 10,
            "batter_id": players[batter],
            "batter": batter,
            "non_striker_id": players[non_striker],
            "non_striker": non_striker,
            "bowler_id": 50 + over_number,
            "bowler": bowler,
            "runs_batter": runs,
            "runs_total": total_runs,
            "runs_extras": int(is_bye),
            "extras": {"byes": int(is_bye)},
            "runs_bowler": runs,
            "is_wide": False,
            "is_legal": True,
            "is_wicket": wicket,
            "dismissed_batter_id": players[wickets[index]] if wicket else None,
            "dismissed_batter": wickets.get(index),
            "bowler_wicket": wicket,
            "dismissal": "Bowled" if wicket else None,
            "four": runs == 4,
            "six": runs == 6,
        })
    delayed_events = [
        delivery for delivery in deliveries
        if delivery["over_ball"] not in {"17.5", "17.6"}
    ]
    delayed_events.extend(
        delivery for delivery in deliveries
        if delivery["over_ball"] in {"17.5", "17.6"}
    )
    return {
        "id": 101,
        "match_format": "T20",
        "localteam": {"id": 10, "name": "India"},
        "visitorteam": {"id": 20, "name": "England"},
        "deliveries": delayed_events,
    }


class NarrativeApiTests(unittest.TestCase):
    def test_innings_narrative_uses_only_deliveries_through_checkpoint(self):
        state = {
            "id": 216,
            "context_mode": "HISTORICAL REPLAY CHECKPOINT",
            "delivery_index": 1,
            "total_deliveries": 2,
            "deliveries": [
                {"innings": "1", "runs_total": 1, "runs_batter": 1, "is_legal": True},
                {"innings": "1", "runs_total": 4, "runs_batter": 4, "is_legal": True},
            ],
        }
        with patch("app.api.narrative.create_narrative", new_callable=AsyncMock) as answer:
            answer.return_value = {
                "answer": "The innings began with one recorded run.",
                "sections": [{"title": "Overall innings story", "paragraphs": ["The innings began with one recorded run."]}],
                "evidence": [],
                "confidence": "high",
                "limitations": [],
                "answer_mode": "AI-GENERATED NARRATIVE",
                "ai_available": True,
            }
            response = client.post("/api/narrative", json={
                "state": state,
                "scope": "innings",
                "innings": "1",
            })

        self.assertEqual(response.status_code, 200)
        used_state = answer.await_args.args[0]
        self.assertEqual(used_state["delivery_index"], 1)
        self.assertEqual(used_state["total_deliveries"], 2)
        self.assertTrue(used_state["narrative_mode"])
        self.assertEqual(answer.await_args.args[1], "1")
        self.assertEqual(response.json()["delivery_index"], 1)

    def test_player_narrative_requires_a_role_and_name(self):
        response = client.post("/api/narrative", json={
            "state": {"deliveries": [], "delivery_index": 0},
            "scope": "player",
            "innings": "1",
        })

        self.assertEqual(response.status_code, 422)

    def test_delivery_facts_capture_player_progressions_partnerships_and_wickets(self):
        engine = MatchStateEngine(FIXTURE)
        while engine.step() is not None:
            pass
        state = {
            **engine.snapshot(),
            "match_format": "T20",
            "narrative_mode": True,
            "completed_innings": ["1"],
        }

        facts = build_innings_facts(
            __import__("app.services.match_context_builder", fromlist=["build_match_context"])
            .build_match_context(state),
            "1",
        )

        self.assertEqual(facts["score"], "8/1")
        self.assertEqual(len(facts["deliveries"]), 6)
        self.assertEqual({row["name"] for row in facts["batters"]}, {"Batter A", "Batter B"})
        self.assertEqual(facts["batters"][0]["entry_score"], "0/0")
        self.assertEqual(facts["wickets"][0]["batter"], "Batter B")
        self.assertEqual(facts["partnerships"][0]["runs"], 8)
        self.assertEqual(facts["partnerships"][0]["end_wicket"]["score"], "8/1")
        self.assertEqual(facts["bowlers"][0]["overs"][0]["runs"], 8)
        self.assertEqual(facts["phases"][0]["phase"], "Powerplay")

    def test_canonical_timeline_reconciles_score_partnerships_and_dismissal_balls(self):
        engine = MatchStateEngine(india_innings_regression_fixture())
        while engine.step() is not None:
            pass
        state = {
            **engine.snapshot(),
            "narrative_mode": True,
            "completed_innings": ["1"],
        }
        self.assertEqual(
            [row["over_ball"] for row in state["deliveries"][-2:]],
            ["17.5", "17.6"],
        )
        context = __import__(
            "app.services.match_context_builder", fromlist=["build_match_context"]
        ).build_match_context(state)
        facts = build_innings_facts(context, "1")

        self.assertEqual(facts["score"], "190/7")
        self.assertEqual(facts["overs"], "20.0")
        self.assertEqual(facts["timeline_score"], facts["score"])
        self.assertEqual(facts["timeline_event_count"], 120)
        self.assertEqual(facts["event_timeline"], facts["deliveries"])
        self.assertEqual(facts["deliveries"][-1]["ball"], "19.6 (sixth ball of over 20)")
        self.assertEqual(
            [event["ball"] for event in facts["deliveries"][-3:]],
            [
                "19.4 (fourth ball of over 20)",
                "19.5 (fifth ball of over 20)",
                "19.6 (sixth ball of over 20)",
            ],
        )
        self.assertEqual(facts["overs_timeline"][-1]["end_score"], facts["score"])
        self.assertEqual(sum(item["runs"] for item in facts["overs_timeline"]), 190)
        self.assertEqual(sum(len(item["wickets"]) for item in facts["overs_timeline"]), 7)
        self.assertEqual(sum(item["runs"] for item in facts["partnerships"]), 190)
        self.assertEqual(
            sum(item["runs"] for item in facts["batters"])
            + sum(event["extra"] for event in facts["deliveries"]),
            190,
        )
        for over in facts["overs_timeline"]:
            over_events = [
                event for event in facts["deliveries"] if event["over"] == over["over"]
            ]
            self.assertEqual(sum(event["runs"] for event in over_events), over["runs"])
            self.assertEqual(over_events[0]["score_before"], over["entry_score"])
            self.assertEqual(over_events[-1]["score"], over["end_score"])
            self.assertEqual(
                sum(bool(event["wicket"]) for event in over_events),
                len(over["wickets"]),
            )
        for previous, current in zip(facts["deliveries"], facts["deliveries"][1:]):
            self.assertEqual(current["score_before"], previous["score"])
            self.assertGreaterEqual(int(current["over"]), int(previous["over"]))

        batter_totals = {item["name"]: item["runs"] for item in facts["batters"]}
        self.assertEqual(batter_totals["Abhishek Sharma"], 43)
        self.assertEqual(batter_totals["Ishan Kishan"], 49)
        self.assertEqual(batter_totals["Shreyas Iyer"], 37)
        for batter in facts["batters"]:
            self.assertEqual(
                batter["runs"],
                sum(
                    event["bat_runs"]
                    for event in facts["deliveries"]
                    if event["batter"] == batter["name"]
                ),
            )
        for wicket in facts["wickets"]:
            event = next(
                item for item in facts["deliveries"]
                if item["ball"] == wicket["ball"] and item["wicket"]
            )
            self.assertEqual(wicket["score_before"], event["score_before"])
            self.assertEqual(wicket["score"], event["score"])

        stand = next(item for item in facts["partnerships"] if item["runs"] == 65)
        contributions = {item["batter"]: item["runs"] for item in stand["contributions"]}
        self.assertEqual(contributions, {"Ishan Kishan": 28, "Shreyas Iyer": 37})
        self.assertEqual(sum(contributions.values()), stand["runs"])
        for partnership in facts["partnerships"]:
            events = [
                item for item in facts["event_timeline"]
                if partnership["start_event_order"] <= item["event_order"] <= partnership["end_event_order"]
            ]
            self.assertEqual(sum(item["runs"] for item in events), partnership["runs"])
            stand_contributions = {
                item["batter"]: item["runs"]
                for item in partnership["contributions"]
            }
            self.assertEqual(
                stand_contributions,
                {
                    batter: sum(
                        event["bat_runs"] for event in events
                        if event["batter"] == batter
                    )
                    for batter in stand_contributions
                },
            )
            self.assertEqual(
                sum(stand_contributions.values()) + partnership["extras"],
                partnership["runs"],
            )
        partnership_events = [
            item for item in facts["event_timeline"]
            if stand["start_event_order"] <= item["event_order"] <= stand["end_event_order"]
        ]
        self.assertEqual(sum(item["runs"] for item in partnership_events), stand["runs"])
        self.assertEqual(
            {
                name: sum(event["bat_runs"] for event in partnership_events if event["batter"] == name)
                for name in contributions
            },
            contributions,
        )
        self.assertEqual(
            stand["runs"] - sum(contributions.values()),
            stand["extras"],
        )
        self.assertEqual((stand["start_over"], stand["end_over"]), (11, 18))
        wickets = {item["batter"]: item for item in facts["wickets"]}
        self.assertEqual(wickets["Ishan Kishan"]["ball"], "17.2 (second ball of over 18)")
        self.assertEqual(wickets["Ishan Kishan"]["score"], facts["deliveries"][103]["score"])
        self.assertEqual(wickets["Harshit Rana"]["ball"], facts["deliveries"][-1]["ball"])
        self.assertEqual(wickets["Harshit Rana"]["score"], facts["score"])

        abhishek = next(item for item in facts["batters"] if item["name"] == "Abhishek Sharma")
        self.assertEqual(abhishek["acceleration"]["first_half_sr"], 66.7)
        self.assertEqual(abhishek["acceleration"]["second_half_sr"], 291.7)
        sections = _fallback_sections(facts, facts["batters"])
        overall = " ".join(sections[0]["paragraphs"])
        assessment = sections[-1]["paragraphs"][0]
        abhishek_story = next(
            paragraph for paragraph in sections[4]["paragraphs"]
            if paragraph.startswith("Abhishek Sharma")
        )
        self.assertIn("overs 3-4", abhishek_story)
        self.assertIn("Bowler A", abhishek_story)
        self.assertIn("43 from 24", abhishek_story)
        bowler_story = " ".join(sections[5]["paragraphs"])
        self.assertIn("economy rate of", bowler_story)
        self.assertIn("The breakthroughs arrived in sequence", bowler_story)
        self.assertIn("breakthrough", bowler_story)
        self.assertNotIn("the figures were", bowler_story)
        self.assertNotIn("..", bowler_story)
        self.assertIn("17.2 (second ball of over 18)", overall)
        self.assertIn("19.6 (sixth ball of over 20)", overall)
        self.assertNotIn("were together", overall)
        self.assertNotIn("was dismissed at", overall)
        self.assertIn("65-run stand", assessment)
        self.assertIn("fastest scoring came", assessment)
        self.assertIn("closing overs added 40 while 4 wickets fell", assessment)
        self.assertLess(
            overall.index("17.2 (second ball of over 18)"),
            overall.index("19.6 (sixth ball of over 20)"),
        )

    def test_data_derived_fallback_has_all_innings_sections_and_no_provider_diagnostics(self):
        engine = MatchStateEngine(FIXTURE)
        while engine.step() is not None:
            pass
        state = {
            **engine.snapshot(),
            "match_format": "T20",
            "narrative_mode": True,
            "completed_innings": ["1"],
        }
        with patch("app.services.narrative_service._narrative_providers_to_try", return_value=[]):
            result = __import__("asyncio").run(generate_narrative(state, "1"))

        titles = [section["title"] for section in result["sections"]]
        self.assertEqual(len(titles), 9)
        self.assertIn("Batter-by-batter contributions", titles)
        self.assertIn("Batter A", result["answer"])
        self.assertIn("Bowler A", result["answer"])
        self.assertFalse(result["ai_available"])
        self.assertNotIn("AI narrative unavailable", result["answer"])
        self.assertNotIn("Ollama", result["answer"])

    def test_provider_failure_falls_back_without_exposing_diagnostics(self):
        engine = MatchStateEngine(FIXTURE)
        while engine.step() is not None:
            pass
        state = {
            **engine.snapshot(),
            "match_format": "T20",
            "narrative_mode": True,
            "completed_innings": ["1"],
        }
        with (
            patch("app.services.narrative_service._narrative_providers_to_try", return_value=["ollama"]),
            patch(
                "app.services.narrative_service._call_provider",
                side_effect=ValueError("Ollama rejected configured model or access"),
            ),
            self.assertLogs("app.services.narrative_service", level="WARNING") as logs,
        ):
            result = __import__("asyncio").run(generate_narrative(state, "1"))

        self.assertFalse(result["ai_available"])
        self.assertNotIn("Ollama", result["answer"])
        self.assertNotIn("rejected configured model", " ".join(logs.output))

    def test_narrative_tries_each_configured_provider_once(self):
        configured = {"cerebras", "groq"}
        with (
            patch(
                "app.services.narrative_service._providers_to_try",
                return_value=["ollama"],
            ),
            patch(
                "app.services.narrative_service._provider_config",
                side_effect=lambda provider: {"model": "configured"} if provider in configured else None,
            ),
        ):
            self.assertEqual(
                _narrative_providers_to_try(),
                ["ollama", "cerebras", "groq"],
            )

    def test_flat_provider_response_is_normalized_to_narrative_sections(self):
        response = {
            title: f"Evidence-based {title.lower()}."
            for title in _SECTION_TITLES
        }
        output = _validated_narrative_output(__import__("json").dumps(response))

        self.assertEqual(
            [section.title for section in output.sections],
            list(_SECTION_TITLES),
        )
        self.assertEqual(
            output.sections[0].paragraphs,
            ["Evidence-based overall innings story."],
        )

    def test_provider_statistics_must_match_canonical_innings_facts(self):
        engine = MatchStateEngine(FIXTURE)
        while engine.step() is not None:
            pass
        state = {
            **engine.snapshot(),
            "match_format": "T20",
            "narrative_mode": True,
            "completed_innings": ["1"],
        }
        context = __import__(
            "app.services.match_context_builder", fromlist=["build_match_context"]
        ).build_match_context(state)
        facts = build_innings_facts(context, "1")

        _validate_narrative_statistics(
            "The innings finished at 8/1. Batter A made 5 off 3 balls, "
            "and the 8-run stand ended with a wicket.",
            facts,
        )
        with self.assertRaisesRegex(ValueError, "score not present"):
            _validate_narrative_statistics("The innings finished at 10/1.", facts)
        with self.assertRaisesRegex(ValueError, "run/ball figure"):
            _validate_narrative_statistics("Batter A made 8 off 3 balls.", facts)
        with self.assertRaisesRegex(ValueError, "boundary count"):
            _validate_narrative_statistics("Batter A hit two sixes.", facts)

    def test_entity_validation_accepts_dismissal_fielders_and_sentence_boundaries(self):
        context = {
            "teams": [{"name": "India"}],
            "innings_scorecards": [{
                "batting_scorecard": [{"name": "Batter A"}],
                "bowling_scorecard": [{"name": "Sam Curran"}],
            }],
            "delivery_records": [{
                "batter": "Batter A",
                "bowler": "Sam Curran",
                "wicket": {"dismissal": "c Jos Buttler b Sam Curran"},
            }],
        }

        _validate_narrative_entities(
            "Sam Curran's spell ended the innings. Jos Buttler completed the catch.",
            context,
        )

    def test_chase_required_rate_uses_known_match_format_limit(self):
        fixture = {
            **FIXTURE,
            "match_format": "ODI",
            "deliveries": [
                *FIXTURE["deliveries"],
                {
                    "over_ball": "0.1",
                    "innings": "2",
                    "team_id": 10,
                    "batter_id": 210,
                    "batter": "Batter C",
                    "non_striker": "Batter D",
                    "bowler_id": 58,
                    "bowler": "Bowler A",
                    "runs_batter": 1,
                    "runs_total": 1,
                    "runs_bowler": 1,
                    "is_legal": True,
                    "is_wicket": False,
                    "bowler_wicket": False,
                    "four": False,
                    "six": False,
                },
            ],
        }
        engine = MatchStateEngine(fixture)
        while engine.step() is not None:
            pass
        state = {
            **engine.snapshot(),
            "match_format": "ODI",
            "narrative_mode": True,
            "completed_innings": ["1"],
        }
        context = __import__(
            "app.services.match_context_builder", fromlist=["build_match_context"]
        ).build_match_context(state)

        chase = build_innings_facts(context, "2")

        self.assertEqual(chase["target"], 9)
        self.assertEqual(chase["required_rate_timeline"][0]["balls_remaining"], 299)
        self.assertAlmostEqual(chase["required_rate_timeline"][0]["required_run_rate"], 0.16)

    def test_chase_report_explains_required_rate_shift_after_scoring_burst(self):
        facts = {
            "batting_team": "England",
            "target": 191,
            "score": "38/0",
            "partnerships": [],
            "phases": [{
                "phase": "Powerplay",
                "start_over": 1,
                "end_over": 3,
                "runs": 38,
                "wickets": 0,
            }],
            "overs_timeline": [
                {
                    "over": str(over),
                    "runs": runs,
                    "entry_score": entry_score,
                    "end_score": end_score,
                    "batters": batters,
                    "wickets": [],
                }
                for over, runs, entry_score, end_score, batters in [
                    ("1", 5, "0/0", "5/0", []),
                    ("2", 6, "5/0", "11/0", []),
                    ("3", 27, "11/0", "38/0", [
                        {"batter": "Harry Brook", "runs": 26},
                    ]),
                ]
            ],
            "required_rate_timeline": [
                {"over": "1", "required_run_rate": 9.79},
                {"over": "2", "required_run_rate": 10.0},
                {"over": "3", "required_run_rate": 9.0},
            ],
        }

        story = " ".join(_report_prose(facts))

        self.assertIn("bringing the required rate down from 10.0 after over 2 to 9.0 after over 3", story)
        self.assertNotIn("required rate fell from", story)

    def test_batter_acceleration_uses_a_fifteen_ball_opening_segment(self):
        run_sequence = [1] * 15 + [4] * 17 + [1] * 2 + [0] * 11
        fixture = {
            **FIXTURE,
            "deliveries": [
                {
                    "over_ball": f"{(index - 1) // 6}.{(index - 1) % 6 + 1}",
                    "innings": "1",
                    "team_id": 43,
                    "batter_id": 109,
                    "batter": "Batter A",
                    "non_striker": "Batter B",
                    "bowler_id": 58,
                    "bowler": "Bowler A",
                    "runs_batter": runs,
                    "runs_total": runs,
                    "runs_bowler": runs,
                    "is_legal": True,
                    "is_wicket": False,
                    "bowler_wicket": False,
                    "four": runs == 4,
                    "six": False,
                }
                for index, runs in enumerate(run_sequence, 1)
            ],
        }
        engine = MatchStateEngine(fixture)
        while engine.step() is not None:
            pass
        state = {
            **engine.snapshot(),
            "narrative_mode": True,
            "completed_innings": ["1"],
        }
        context = __import__(
            "app.services.match_context_builder", fromlist=["build_match_context"]
        ).build_match_context(state)

        batter = next(
            item for item in build_innings_facts(context, "1")["batters"]
            if item["name"] == "Batter A"
        )

        self.assertEqual(batter["runs"], 85)
        self.assertEqual(batter["acceleration"]["early_balls"], 15)
        self.assertEqual(batter["acceleration"]["later_balls"], 30)
        self.assertGreater(
            batter["acceleration"]["second_half_sr"],
            batter["acceleration"]["first_half_sr"],
        )

    def test_llm_receives_delivery_sequences_and_returns_required_sections(self):
        engine = MatchStateEngine(FIXTURE)
        while engine.step() is not None:
            pass
        state = {
            **engine.snapshot(),
            "match_format": "T20",
            "narrative_mode": True,
            "completed_innings": ["1"],
        }
        context = __import__(
            "app.services.match_context_builder", fromlist=["build_match_context"]
        ).build_match_context(state)
        facts = build_innings_facts(context, "1")
        batter = facts["batters"][0]
        bowler = facts["bowlers"][0]
        matchup = max(bowler["matchups"], key=lambda item: item["batter_runs"])
        wicket = bowler["wickets"][0]
        overs = bowler["legal_balls"] // 6
        dismissal = (
            f"{wicket['batter']} was dismissed at {wicket['ball'].split()[0]}, "
            f"leaving {wicket['score']}."
        )
        generated = {
            "sections": [
                {
                    "title": title,
                    "paragraphs": (
                        [f"{facts['batting_team']} finished at {facts['score']}. Batter A built the opening."]
                        if title == "Overall innings story"
                        else [f"{bowler['name']} conceded {bowler['runs']} runs from {overs} overs in the Powerplay; {matchup['batter']} was the leading matchup. {dismissal}"]
                        if title == "Bowler-by-bowler performances"
                        else [dismissal]
                    ),
                }
                for title in _SECTION_TITLES
            ],
        }

        class FakeResponse:
            status_code = 200

            def raise_for_status(self):
                return None

            def json(self):
                return {"choices": [{"message": {"content": __import__("json").dumps(generated)}}]}

        class FakeClient:
            body = None

            def __init__(self, timeout):
                self.timeout = timeout

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

            async def post(self, url, *, headers, params, json):
                FakeClient.body = json
                return FakeResponse()

        with (
            patch("app.services.narrative_service._provider_config", return_value={
                "url": "https://provider.invalid",
                "model": "test-model",
                "key": "test-key",
            }),
            patch("app.services.narrative_service.httpx.AsyncClient", FakeClient),
        ):
            sections = __import__("asyncio").run(_call_provider("groq", facts, "innings", None))

        self.assertEqual([section["title"] for section in sections], list(_SECTION_TITLES))
        self.assertEqual(len(sections), 9)
        prompt = " ".join(
            message["content"]
            for message in FakeClient.body["messages"]
        )
        self.assertIn('"deliveries"', prompt)
        self.assertIn('"delivery_fields"', prompt)
        self.assertIn('"0.1"', prompt)
        self.assertIn('"partnerships"', prompt)


if __name__ == "__main__":
    unittest.main()
