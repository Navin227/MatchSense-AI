import asyncio
import copy
import json
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient

from app.main import app
from app.services.match_chat_service import (
    MatchAnswer,
    ProviderFailure,
    _deterministic_answer,
    _reasoning_fallback,
    answer_match_question,
)
from app.services.match_context_builder import build_match_context
from app.services.match_intelligence_service import build_intelligence, simulate_scenario
from app.services.replay_engine import MatchStateEngine


def ball(index, innings, team_id, batter_id, batter, bowler_id, bowler, runs, wicket=False, four=False):
    innings_ball = index if str(innings) == "1" else index - 6
    return {
        "source_id": index,
        "over_ball": f"{(innings_ball - 1) // 6}.{(innings_ball - 1) % 6 + 1}",
        "innings": str(innings),
        "team_id": team_id,
        "batter_id": batter_id,
        "bowler_id": bowler_id,
        "batter": batter,
        "bowler": bowler,
        "runs_batter": runs,
        "runs_total": runs,
        "runs_bowler": runs,
        "is_legal": True,
        "is_wicket": wicket,
        "bowler_wicket": wicket,
        "four": four,
        "six": False,
        "dismissal": "Bowled" if wicket else None,
    }


TWO_INNINGS_FIXTURE = {
    "id": 216,
    "localteam": {"id": 10, "name": "India", "short_name": "IND"},
    "visitorteam": {"id": 43, "name": "West Indies", "short_name": "WI"},
    "batting": [
        {"player_id": 109, "name": "Batter A"},
        {"player_id": 110, "name": "Batter B"},
    ],
    "bowling": [
        {"player_id": 58, "name": "Bowler A"},
        {"player_id": 59, "name": "Bowler B"},
    ],
    "deliveries": [
        ball(1, 1, 43, 109, "Batter A", 58, "Bowler A", 4, four=True),
        ball(2, 1, 43, 109, "Batter A", 58, "Bowler A", 0),
        ball(3, 1, 43, 109, "Batter A", 58, "Bowler A", 1),
        ball(4, 1, 43, 110, "Batter B", 58, "Bowler A", 2),
        ball(5, 1, 43, 110, "Batter B", 58, "Bowler A", 1),
        ball(6, 1, 43, 110, "Batter B", 58, "Bowler A", 4, four=True),
        ball(7, 2, 10, 210, "Batter C", 59, "Bowler B", 1),
        ball(8, 2, 10, 210, "Batter C", 59, "Bowler B", 0),
        ball(9, 2, 10, 211, "Batter D", 59, "Bowler B", 3, wicket=True),
        ball(10, 2, 10, 211, "Batter D", 59, "Bowler B", 0),
        ball(11, 2, 10, 210, "Batter C", 59, "Bowler B", 1),
        ball(12, 2, 10, 211, "Batter D", 59, "Bowler B", 0),
        ball(13, 2, 10, 210, "Batter C", 59, "Bowler B", 0),
        ball(14, 2, 10, 211, "Batter D", 59, "Bowler B", 4, four=True),
        ball(15, 2, 10, 210, "Batter C", 59, "Bowler B", 0),
        ball(16, 2, 10, 210, "Batter C", 59, "Bowler B", 0),
        ball(17, 2, 10, 211, "Batter D", 59, "Bowler B", 0),
        ball(18, 2, 10, 210, "Batter C", 59, "Bowler B", 1),
        ball(19, 2, 10, 211, "Batter D", 59, "Bowler B", 0),
        ball(20, 2, 10, 210, "Batter C", 59, "Bowler B", 0),
        ball(21, 2, 10, 211, "Batter D", 59, "Bowler B", 4, four=True),
    ],
}


class MatchIntelligenceServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = MatchStateEngine(TWO_INNINGS_FIXTURE)
        for _ in range(9):
            self.engine.step()
        self.state = self.engine.snapshot()

    def test_intelligence_and_debate_use_current_checkpoint_statistics(self):
        result = build_intelligence(self.state)

        self.assertIn("4/1", result["situation"]["summary"])
        self.assertEqual(result["situation"]["current_innings"]["runs"], 4)
        self.assertEqual(result["situation"]["pressure"], "Measured")
        self.assertTrue(any("Batter D" in item for item in result["situation"]["strengths"]))
        self.assertFalse(any("Batter B" in item for item in result["situation"]["strengths"]))
        self.assertTrue(result["debate"]["available"])
        self.assertNotEqual(result["debate"]["strategy_a"]["action"], result["debate"]["strategy_b"]["action"])
        self.assertTrue(result["debate"]["strategy_a"]["evidence"])
        self.assertIn("checkpoint", result["debate"]["comparison"].lower())

    def test_matchups_and_win_estimate_are_deterministic_and_evidence_scoped(self):
        result = build_intelligence(self.state)
        matchups = result["matchups"]["items"]
        win = result["win_probability"]

        self.assertTrue(result["matchups"]["available"])
        self.assertTrue(any(item["batter"] == "Batter D" and item["bowler"] == "Bowler B" for item in matchups))
        self.assertEqual(result["situation"]["available_bowlers"], [{
            "name": "Bowler B",
            "legal_balls": 3,
            "runs": 4,
            "wickets": 1,
            "overs": "0.3",
            "economy": 8.0,
        }])
        self.assertEqual(next(item for item in matchups if item["batter"] == "Batter D")["bowling_economy"], 8.0)
        self.assertEqual(result["matchups"]["evidence_scope"], "Current innings deliveries processed so far only; no external head-to-head data is loaded.")
        self.assertTrue(win["available"])
        self.assertEqual(win["target"], 13)
        self.assertEqual(win["runs_required"], 9)
        self.assertEqual(win["balls_remaining"], 117)
        self.assertEqual(win["batting_team_probability"] + win["bowling_team_probability"], 100)
        self.assertIn("not calibrated", win["method"])

    def test_live_unknown_format_does_not_assume_twenty_over_limits(self):
        state = copy.deepcopy(self.state)
        state["context_mode"] = "LIVE MATCH SNAPSHOT"
        state.pop("match_format", None)

        result = build_intelligence(state)

        self.assertIsNone(result["situation"]["match_phase"])
        self.assertTrue(any("innings length is unavailable" in item for item in result["situation"]["factors"]))
        self.assertFalse(result["win_probability"]["available"])
        self.assertIn("format", result["win_probability"]["reason"])

    def test_win_estimate_is_withheld_for_non_t20_formats(self):
        state = copy.deepcopy(self.state)
        state["match_format"] = "ODI"

        result = build_intelligence(state)

        self.assertIn("297 legal deliveries remain", " ".join(result["situation"]["factors"]))
        self.assertFalse(result["win_probability"]["available"])
        self.assertIn("20-over", result["win_probability"]["reason"])

    def test_recommendation_and_field_are_labeled_and_supported(self):
        result = build_intelligence(self.state)
        decision = result["decision"]
        field = result["field"]

        self.assertTrue(decision["available"])
        self.assertTrue(decision["supporting_statistics"])
        self.assertFalse(decision["is_team_instruction"])
        self.assertIn("Low", decision["confidence"])
        self.assertIn("NOT AN ACTUAL FIELD", field["label"])
        self.assertTrue(field["positions"])
        self.assertTrue(any("No shot-direction" in item for item in field["basis"]))

    def test_scenario_recalculates_rate_consistently_without_predicting_future(self):
        result = simulate_scenario(self.state, {"kind": "additional_runs", "runs": 15})

        self.assertEqual(result["original"]["runs"], 4)
        self.assertEqual(result["modified"]["runs"], 19)
        self.assertEqual(result["difference"]["runs"], 15)
        self.assertEqual(result["modified"]["runs_required"], 0)
        self.assertEqual(result["modified"]["required_run_rate"], 0)
        self.assertEqual(result["original"]["balls_remaining"], result["modified"]["balls_remaining"])
        self.assertIn("does not model", result["explanation"])
        with self.assertRaises(ValueError):
            simulate_scenario(self.state, {"kind": "alternative_bowler", "runs": 15})

    def test_scenario_variants_preserve_deliveries_and_only_change_selected_assumptions(self):
        wicket_scenario = simulate_scenario(self.state, {"kind": "wicket_not_fallen", "runs": 15})
        saved_runs = simulate_scenario(self.state, {"kind": "bowler_runs_saved", "runs": 2})
        acceleration = simulate_scenario(self.state, {"kind": "earlier_acceleration", "runs": 6})

        self.assertEqual(wicket_scenario["modified"]["runs"], wicket_scenario["original"]["runs"])
        self.assertEqual(wicket_scenario["modified"]["wickets"], wicket_scenario["original"]["wickets"] - 1)
        self.assertEqual(wicket_scenario["modified"]["balls_remaining"], wicket_scenario["original"]["balls_remaining"])
        self.assertEqual(saved_runs["modified"]["runs"], 2)
        self.assertEqual(acceleration["modified"]["runs"], 10)
        self.assertIn("future consequences are unknown", wicket_scenario["uncertainty"])

    def test_no_checkpoint_does_not_read_completed_scorecard_figures(self):
        result = build_intelligence(MatchStateEngine(TWO_INNINGS_FIXTURE).snapshot())

        self.assertFalse(result["debate"]["available"])
        self.assertFalse(result["win_probability"]["available"])
        self.assertEqual(result["situation"]["current_innings"], None)

    def test_field_suggestion_uses_checkpoint_boundary_rate_and_discloses_missing_bowler_type(self):
        fixture = {**TWO_INNINGS_FIXTURE, "deliveries": list(TWO_INNINGS_FIXTURE["deliveries"])}
        fixture["deliveries"][6] = {
            **fixture["deliveries"][6],
            "runs_batter": 4,
            "runs_total": 4,
            "runs_bowler": 4,
            "four": True,
            "is_wicket": False,
            "bowler_wicket": False,
            "dismissal": None,
        }
        engine = MatchStateEngine(fixture)
        for _ in range(9):
            engine.step()
        field = build_intelligence(engine.snapshot())["field"]

        self.assertIn("Match phase: Powerplay", field["basis"][0])
        self.assertTrue(any(position["name"] == "Third" for position in field["positions"]))
        self.assertTrue(any("Bowler type is not present" in item for item in field["basis"]))

    def test_match_context_contains_both_innings_scorecards_and_team_associations(self):
        engine = MatchStateEngine(TWO_INNINGS_FIXTURE)
        while engine.step() is not None:
            pass
        context = build_match_context(engine.snapshot())

        self.assertEqual(len(context["innings"]), 2)
        self.assertEqual(context["innings"][0]["batting_team"]["name"], "West Indies")
        self.assertEqual(context["innings"][1]["batting_team"]["name"], "India")
        self.assertEqual(
            {player["name"] for player in context["innings"][0]["batting_scorecard"]},
            {"Batter A", "Batter B"},
        )
        self.assertEqual(
            {player["name"] for player in context["innings"][1]["batting_scorecard"]},
            {"Batter C", "Batter D"},
        )
        self.assertEqual(context["innings"][0]["bowling_scorecard"][0]["team"]["name"], "India")
        self.assertEqual(context["innings"][1]["bowling_scorecard"][0]["team"]["name"], "West Indies")
        self.assertEqual(len(context["delivery_history"]), 21)
        self.assertEqual(len(context["innings"][1]["wicket_details"]), 1)

    def test_match_context_excludes_deliveries_after_selected_checkpoint(self):
        engine = MatchStateEngine(TWO_INNINGS_FIXTURE)
        for _ in range(9):
            engine.step()
        state = copy.deepcopy(engine.snapshot())
        future_ball = copy.deepcopy(TWO_INNINGS_FIXTURE["deliveries"][9])
        future_ball.update({"batter_id": 999, "batter": "Future Player"})
        state["deliveries"].append(future_ball)
        context = build_match_context(state)

        self.assertEqual(context["delivery_index"], 9)
        self.assertEqual(len(context["delivery_history"]), 9)
        self.assertNotIn(
            "Future Player",
            {player["name"] for player in context["innings"][1]["batting_scorecard"]},
        )

    def test_narrative_facts_include_checkpoint_scoped_over_matchup_and_phase_stats(self):
        engine = MatchStateEngine(TWO_INNINGS_FIXTURE)
        while engine.step() is not None:
            pass
        state = {
            **engine.snapshot(),
            "match_format": "T20",
            "narrative_mode": True,
            "completed_innings": ["1", "2"],
        }

        facts = build_match_context(state)["narrative_facts"]
        first_innings = facts["phase_totals"]["innings"]["1"]["Powerplay"]
        batter_bowler = next(
            item for item in facts["matchups"]
            if item["batter"] == "Batter A" and item["bowler"] == "Bowler A"
        )
        batter_phase = next(
            item for item in facts["phase_totals"]["batters"]
            if item["batter"] == "Batter A" and item["phase"] == "Powerplay"
        )
        bowler_phase = next(
            item for item in facts["phase_totals"]["bowlers"]
            if item["bowler"] == "Bowler A" and item["phase"] == "Powerplay"
        )

        self.assertTrue(facts["phase_definitions_available"])
        self.assertEqual(first_innings, {
            "runs": 12,
            "wickets": 0,
            "legal_balls": 6,
            "dots": 1,
            "boundaries": 2,
            "run_rate": 12.0,
        })
        self.assertEqual(
            (batter_bowler["batter_runs"], batter_bowler["legal_balls"], batter_bowler["boundaries"]),
            (5, 3, 1),
        )
        self.assertEqual(
            (batter_phase["runs"], batter_phase["balls"], batter_phase["strike_rate"]),
            (5, 3, 166.67),
        )
        self.assertEqual(
            (bowler_phase["runs"], bowler_phase["legal_balls"], bowler_phase["wickets"]),
            (12, 6, 0),
        )

    def test_required_factual_questions_are_answered_from_calculated_match_data(self):
        engine = MatchStateEngine(TWO_INNINGS_FIXTURE)
        while engine.step() is not None:
            pass
        context = build_match_context(engine.snapshot())
        cases = [
            ("Who was the best India batter?", "Batter D", "India"),
            ("Who was the best West Indies batter?", "Batter B", "West Indies"),
            ("Who took the most wickets?", "Bowler B", "West Indies"),
            ("Which bowler had the best economy?", "Bowler B", "West Indies"),
            ("What is Batter D's strike rate?", "Batter D", "Strike rate"),
            ("What was the team total?", "West Indies", "12/0"),
            ("Why did West Indies lose?", "India won by 9 wickets", "Batter B runs"),
            ("What happens if the batting team loses another wicket?", "wickets remaining", "8"),
        ]
        for question, expected_answer, expected_evidence in cases:
            with self.subTest(question=question):
                result = _deterministic_answer(question, context)
                self.assertIsNotNone(result)
                self.assertIn(expected_answer.casefold(), result["answer"].casefold())
                evidence = " ".join(
                    f"{item['label']} {item['value']}" for item in result["evidence"]
                )
                self.assertIn(expected_evidence.casefold(), evidence.casefold())
                self.assertIn(result["confidence"], {"high", "medium", "low"})

    def test_momentum_explanation_uses_innings_over_comparison_and_evidence(self):
        engine = MatchStateEngine(TWO_INNINGS_FIXTURE)
        while engine.step() is not None:
            pass
        context = build_match_context(engine.snapshot())
        response = _reasoning_fallback("Why did momentum change?", context)

        self.assertIn("decreased", response["answer"])
        self.assertIn("over 2", response["answer"])
        self.assertIn("over 3", response["answer"])
        evidence = {item["label"]: item["value"] for item in response["evidence"]}
        self.assertEqual(evidence["Over 2 runs"], "5")
        self.assertEqual(evidence["Over 3 runs"], "4")
        self.assertIn("Over 2 boundaries", evidence)
        self.assertIn("Dot balls changed", response["answer"])

    def test_missing_rohit_stats_are_explicit_not_a_partial_name(self):
        result = _deterministic_answer(
            "Why did Rohit Sharma struggle?",
            build_match_context(self.state),
        )

        self.assertIn("Rohit Sharma", result["answer"])
        self.assertIn("no batting deliveries", result["answer"])
        self.assertGreaterEqual(len(result["answer"]), 40)
        self.assertTrue(result["limitations"])

    def test_chat_fallback_uses_checkpoint_facts_and_discloses_mode(self):
        with (
            patch("app.services.match_chat_service.settings.llm_provider", "groq"),
            patch("app.services.match_chat_service.settings.groq_api_key", ""),
            patch(
                "app.services.match_chat_service._provider_request",
                side_effect=ProviderFailure("groq", "unavailable"),
            ),
        ):
            answer = __import__("asyncio").run(answer_match_question(
                "Which bowler has the advantage?",
                self.state,
            ))

        self.assertIn("Bowler B", answer["answer"])
        self.assertEqual(answer["answer_mode"], "MATCHSENSE · MATCH DATA")
        self.assertTrue(answer["evidence"])

    def test_narrative_fallback_explains_innings_and_player_from_processed_deliveries(self):
        engine = MatchStateEngine(TWO_INNINGS_FIXTURE)
        while engine.step() is not None:
            pass
        state = {
            **engine.snapshot(),
            "narrative_mode": True,
            "completed_innings": ["1", "2"],
            "context_mode": "HISTORICAL REPLAY CHECKPOINT",
        }
        with patch("app.services.match_chat_service._providers_to_try", return_value=[]):
            innings_story = asyncio.run(answer_match_question(
                "Write a chronological narrative of innings 1.",
                state,
            ))
            batter_story = asyncio.run(answer_match_question(
                "Write a chronological batter narrative for Batter A "
                "(player identifier 109) in innings 1.",
                state,
            ))

        self.assertIn("West Indies reached", innings_story["answer"])
        self.assertIn("Batter A", innings_story["answer"])
        self.assertIn("Batter A first faced", batter_story["answer"])
        self.assertIn("5 from 3 balls", batter_story["answer"])
        self.assertIn("RULE-BASED", innings_story["answer_mode"])

    def test_groq_receives_only_structured_current_checkpoint_context(self):
        class FakeResponse:
            def __init__(self, content):
                self.status_code = 200
                self.content = content

            def raise_for_status(self):
                return None

            def json(self):
                return {"choices": [{"message": {"content": json.dumps(self.content)}}]}

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

            async def post(self, url, *, headers, json, params=None):
                self.url = url
                self.headers = headers
                self.body = json
                content = {
                    "answer": "The checkpoint score is 4 for 1 after three legal balls.",
                    "evidence": [{"label": "Score", "value": "4/1"}],
                    "confidence": "high",
                    "limitations": ["Checkpoint only."],
                }
                return FakeResponse(content)

        fake_client = FakeClient()
        with (
            patch("app.services.match_chat_service.settings.llm_provider", "groq"),
            patch("app.services.match_chat_service.settings.groq_api_key", "test-key"),
            patch("app.services.match_chat_service.settings.groq_model", "test-model"),
            patch("app.services.match_chat_service.httpx.AsyncClient", return_value=fake_client),
        ):
            answer = asyncio.run(answer_match_question(
                "Explain the current match situation",
                self.state,
                [{"question": "Who was the best West Indies batter?", "delivery_index": 8}],
            ))

        self.assertIn("4 for 1", answer["answer"])
        self.assertEqual(fake_client.url, "https://api.groq.com/openai/v1/chat/completions")
        self.assertTrue(fake_client.headers["Authorization"].startswith("Bearer "))
        self.assertEqual(fake_client.body["model"], "test-model")
        self.assertIn("historical replay checkpoint", fake_client.body["messages"][0]["content"])
        self.assertIn('"delivery_index":9', fake_client.body["messages"][1]["content"])
        self.assertIn("Who was the best West Indies batter?", fake_client.body["messages"][1]["content"])
        self.assertIn("GROQ", answer["answer_mode"])
        self.assertNotIn("test-key", fake_client.url)

    def test_groq_provider_errors_explain_key_model_quota_and_service_failures(self):
        class FakeResponse:
            def __init__(self, status_code):
                self.status_code = status_code

            def raise_for_status(self):
                if self.status_code >= 400:
                    request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
                    response = httpx.Response(self.status_code, request=request)
                    raise httpx.HTTPStatusError("provider error", request=request, response=response)

            def json(self):
                return {"choices": [{"message": {"content": json.dumps({
                    "answer": "A complete answer with a relevant score.",
                    "evidence": [],
                    "confidence": "low",
                    "limitations": [],
                })}}]}

        class FakeClient:
            def __init__(self, status_code):
                self.status_code = status_code

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

            async def post(self, *_args, **_kwargs):
                return FakeResponse(self.status_code)

        with (
            patch("app.services.match_chat_service.settings.llm_provider", "groq"),
            patch("app.services.match_chat_service.settings.groq_api_key", "test-key"),
            patch("app.services.match_chat_service.settings.llm_retry_attempts", 1),
            patch(
                "app.services.match_chat_service.httpx.AsyncClient",
                return_value=FakeClient(429),
            ),
        ):
            answer = asyncio.run(answer_match_question("Why did momentum change?", self.state))
        self.assertEqual(answer["answer_mode"], "RULE-BASED · PROVIDER UNAVAILABLE")
        self.assertIn("rate limit or usage quota", answer["limitations"][-1])

    def test_primary_provider_uses_only_one_configured_fallback(self):
        from app.services.match_chat_service import _providers_to_try

        with (
            patch("app.services.match_chat_service.settings.llm_provider", "ollama"),
            patch("app.services.match_chat_service.settings.cerebras_api_key", "cerebras-test"),
            patch("app.services.match_chat_service.settings.gemini_api_key", "gemini-test"),
            patch("app.services.match_chat_service.settings.groq_api_key", "groq-test"),
        ):
            self.assertEqual(_providers_to_try(), ["ollama", "cerebras"])

    def test_cloud_provider_does_not_fall_back_to_ollama_when_disabled(self):
        from app.services.match_chat_service import _providers_to_try

        with (
            patch("app.services.match_chat_service.settings.llm_provider", "cerebras"),
            patch("app.services.match_chat_service.settings.ollama_enabled", False),
            patch("app.services.match_chat_service.settings.cerebras_api_key", "cerebras-test"),
            patch("app.services.match_chat_service.settings.gemini_api_key", ""),
            patch("app.services.match_chat_service.settings.groq_api_key", ""),
        ):
            self.assertEqual(_providers_to_try(), ["cerebras"])

    def test_chat_falls_back_once_and_caches_validated_llm_response(self):
        from app.services import match_chat_service

        match_chat_service._answer_cache.clear()
        calls = []

        async def provider(provider_name, _context, _question, _history):
            calls.append(provider_name)
            if provider_name == "ollama":
                raise ProviderFailure("ollama", "local server unavailable")
            return MatchAnswer(
                answer="Recent scoring and the current rate provide the relevant context.",
                evidence=[],
                confidence="medium",
                limitations=[],
            ).model_dump()

        with (
            patch("app.services.match_chat_service.settings.llm_provider", "ollama"),
            patch("app.services.match_chat_service.settings.cerebras_api_key", "cerebras-test"),
            patch("app.services.match_chat_service.settings.gemini_api_key", "gemini-test"),
            patch("app.services.match_chat_service.settings.groq_api_key", "groq-test"),
            patch("app.services.match_chat_service._provider_request", new=AsyncMock(side_effect=provider)),
        ):
            first = asyncio.run(answer_match_question("Why did momentum change?", self.state))
            second = asyncio.run(answer_match_question("Why did momentum change?", self.state))

        self.assertEqual(calls, ["ollama", "cerebras"])
        self.assertEqual(first["answer_mode"], "CEREBRAS · CHECKPOINT GROUNDED")
        self.assertIn("CEREBRAS · CHECKPOINT GROUNDED · CACHED", second["answer_mode"])
        self.assertEqual(first["evidence"], second["evidence"])


class IntelligenceApiTests(unittest.TestCase):
    def test_removed_replay_intelligence_routes_return_not_found(self):
        self.assertEqual(client.get("/api/intelligence/missing-session").status_code, 404)


client = TestClient(app)


if __name__ == "__main__":
    unittest.main()
