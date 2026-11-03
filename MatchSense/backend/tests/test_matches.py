import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient

from app.main import app
from app.services.providers.sportmonks import SportmonksError, SportmonksProvider


client = TestClient(app)


FIXTURE = {
    "data": {
        "id": 42,
        "status": "In Progress",
        "localteam": {"id": 1, "name": "North Stars", "code": "NS"},
        "visitorteam": {"id": 2, "name": "Coastal XI", "code": "CX"},
        "runs": [{"team_id": 1, "inning": 1, "score": 4, "wicket": 0, "overs": "0.1"}],
        "batting": [{
            "player_id": 7,
            "batsman": {"fullname": "A. Player"},
            "score": 48,
            "ball": 31,
            "four_x": 5,
            "six_x": 2,
            "rate": 154.84,
            "active": 1,
        }],
        "bowling": [{
            "player_id": 8,
            "bowler": {"fullname": "B. Player"},
            "overs": "3.2",
            "runs": 24,
            "wickets": 1,
            "rate": 7.2,
            "active": 1,
        }],
        "balls": [{
            "id": 99,
            "ball": "15.2",
            "scoreboard": "1",
            "team_id": 1,
            "batsman": {"fullname": "A. Player"},
            "bowler": {"fullname": "B. Player"},
            "score": {"runs": 4, "four": 1, "six": 0, "is_wicket": 0, "ball": 1},
        }],
    }
}


class MatchApiTests(unittest.TestCase):
    def test_fixture_request_includes_scoreboards_and_player_relationships(self):
        with patch.object(SportmonksProvider, "_get", new_callable=AsyncMock) as fetch:
            fetch.return_value = {"data": {}}
            asyncio.run(SportmonksProvider().get_fixture(42))

        include = fetch.await_args.args[1]["include"]
        self.assertIn("runs", include)
        self.assertIn("scoreboards", include)
        self.assertIn("batting.batsman", include)
        self.assertIn("bowling.bowler", include)
        self.assertIn("balls.batsman", include)
        self.assertIn("balls.bowler", include)
        self.assertIn("lineup", include)
        self.assertIn("localteam", include)
        self.assertIn("visitorteam", include)

    def test_live_matches_returns_normalized_matches_and_poll_interval(self):
        with patch("app.api.matches.provider.get_livescores", new_callable=AsyncMock) as fetch:
            fetch.return_value = {"data": [FIXTURE["data"]]}
            response = client.get("/api/matches/live")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["matches"][0]["score"]["runs"], 4)
        self.assertEqual(body["matches"][0]["localteam"]["name"], "North Stars")
        self.assertEqual(body["matches"][0]["localteam_score"]["runs"], 4)
        self.assertGreaterEqual(body["poll_interval_seconds"], 5)
        self.assertIsNotNone(body["updated_at"])

    def test_match_details_include_analytics_and_chart_data(self):
        with patch("app.api.matches.provider.get_fixture", new_callable=AsyncMock) as fetch:
            fetch.return_value = FIXTURE
            response = client.get("/api/matches/42")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["innings"][0]["run_rate"], 24.0)
        self.assertEqual(body["current_batters"][0]["runs"], 4)
        self.assertEqual(body["current_batters"][0]["strike_rate"], 400.0)
        self.assertEqual(body["current_bowler"]["name"], "B. Player")
        self.assertEqual(body["deliveries"][0]["runs_total"], 4)
        self.assertEqual(body["progression"][0]["runs"], 4)
        self.assertEqual(body["momentum"][0]["runs"], 4)
        self.assertEqual(body["scorecards"][0]["runs"], 4)
        self.assertIn("measured_metrics", body["intelligence"]["situation"])

    def test_no_ball_extra_is_not_double_counted_when_raw_runs_already_include_it(self):
        fixture = {
            "data": {
                "id": 46,
                "status": "In Progress",
                "localteam": {"id": 1, "name": "North Stars"},
                "visitorteam": {"id": 2, "name": "Coastal XI"},
                "type": "T20",
                "runs": [{"team_id": 1, "inning": 1, "score": 1, "wicket": 0, "overs": "0.0"}],
                "balls": [{
                    "id": 1,
                    "ball": "0.1",
                    "scoreboard": "1",
                    "team_id": 1,
                    "batsman": {"id": 7, "fullname": "A. Batter"},
                    "bowler": {"id": 8, "fullname": "B. Bowler"},
                    "score": {
                        "name": "1 No Ball",
                        "runs": 1,
                        "noball": 1,
                        "noball_runs": 1,
                        "ball": 0,
                    },
                }],
            }
        }
        with patch("app.api.matches.provider.get_fixture", new_callable=AsyncMock) as fetch:
            fetch.return_value = fixture
            response = client.get("/api/matches/46")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["deliveries"][0]["runs_batter"], 0)
        self.assertEqual(body["deliveries"][0]["runs_total"], 1)
        self.assertEqual(body["scorecards"][0]["runs"], 1)
        self.assertEqual(body["validation"]["status"], "ok")

    def test_scoreboards_are_used_as_a_fallback_and_conflicts_are_flagged(self):
        fixture = {
            "data": {
                **FIXTURE["data"],
                "runs": [],
                "scoreboards": [
                    {
                        "type": "total",
                        "scoreboard": "S1",
                        "team_id": 1,
                        "total": 4,
                        "wickets": 0,
                        "overs": "0.1",
                    },
                ],
            }
        }
        with patch("app.api.matches.provider.get_fixture", new_callable=AsyncMock) as fetch:
            fetch.return_value = fixture
            response = client.get("/api/matches/42")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["scorecards"][0]["runs"], 4)
        self.assertEqual(body["validation"]["status"], "ok")

        fixture["data"]["runs"] = [{"team_id": 1, "inning": 1, "score": 4, "wicket": 0, "overs": "0.1"}]
        fixture["data"]["scoreboards"][0]["total"] = 5
        with patch("app.api.matches.provider.get_fixture", new_callable=AsyncMock) as fetch:
            fetch.return_value = fixture
            response = client.get("/api/matches/42")
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "scoreboard_total_mismatch",
            {issue["code"] for issue in response.json()["validation"]["issues"]},
        )

    def test_live_intelligence_uses_current_deliveries_not_scorecard_aggregates(self):
        with patch("app.api.matches.provider.get_fixture", new_callable=AsyncMock) as fetch:
            fetch.return_value = FIXTURE
            response = client.get("/api/matches/42")

        self.assertEqual(response.status_code, 200)
        intelligence = response.json()["intelligence"]
        self.assertEqual(intelligence["situation"]["score"]["runs"], 4)
        self.assertEqual(intelligence["situation"]["measured_metrics"]["legal_balls"], 1)
        self.assertIn("situation", intelligence)
        self.assertIn("win_probability", intelligence)
        self.assertNotIn("decision", intelligence)
        self.assertNotIn("field", intelligence)

    def test_removed_chat_and_standalone_intelligence_routes_return_not_found(self):
        self.assertEqual(client.post("/api/matches/42/chat", json={"question": "Who scored the most runs?"}).status_code, 404)
        self.assertEqual(client.get("/api/matches/42/intelligence").status_code, 404)

    def test_t20_powerplay_is_derived_from_current_delivery_window(self):
        fixture = {
            "data": {
                **FIXTURE["data"],
                "type": "T20",
            }
        }
        with patch("app.api.matches.provider.get_fixture", new_callable=AsyncMock) as fetch:
            fetch.return_value = fixture
            response = client.get("/api/matches/42")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["scorecards"][0]["powerplay"], {
            "label": "First 6 overs",
            "overs": "1-6",
            "boundary_legal_balls": 36,
            "runs": 4,
            "wickets": 0,
            "legal_balls": 1,
            "run_rate": 24.0,
            "complete": False,
        })

    def test_odi_powerplay_uses_first_ten_overs(self):
        fixture = {
            "data": {
                **FIXTURE["data"],
                "type": "ODI",
            }
        }
        with patch("app.api.matches.provider.get_fixture", new_callable=AsyncMock) as fetch:
            fetch.return_value = fixture
            response = client.get("/api/matches/42")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["scorecards"][0]["powerplay"], {
            "label": "First 10 overs",
            "overs": "1-10",
            "boundary_legal_balls": 60,
            "runs": 4,
            "wickets": 0,
            "legal_balls": 1,
            "run_rate": 24.0,
            "complete": False,
        })

    def test_provider_powerplay_boundaries_are_applied_per_innings(self):
        fixture = {
            "data": {
                "id": 47,
                "status": "In Progress",
                "type": "T20",
                "runs": [
                    {
                        "team_id": 1,
                        "inning": 1,
                        "score": 12,
                        "wicket": 0,
                        "overs": "2.0",
                        "powerplay_overs": 5,
                    },
                    {
                        "team_id": 2,
                        "inning": 2,
                        "score": 8,
                        "wicket": 1,
                        "overs": "1.0",
                        "powerplay_balls": 24,
                    },
                ],
            }
        }
        with patch("app.api.matches.provider.get_fixture", new_callable=AsyncMock) as fetch:
            fetch.return_value = fixture
            response = client.get("/api/matches/47")

        self.assertEqual(response.status_code, 200)
        powerplays = [card["powerplay"] for card in response.json()["scorecards"]]
        self.assertEqual([powerplay["boundary_legal_balls"] for powerplay in powerplays], [30, 24])
        self.assertEqual([powerplay["overs"] for powerplay in powerplays], ["1-5", "1-4"])

    def test_nested_player_ids_and_lineup_names_resolve_for_current_players(self):
        fixture = {
            "data": {
                "id": 43,
                "status": "In Progress",
                "localteam": {"id": 1, "name": "North Stars"},
                "visitorteam": {"id": 2, "name": "Coastal XI"},
                "runs": [{"team_id": 1, "inning": 1, "score": 8, "wicket": 1, "overs": "0.3"}],
                "lineup": [
                    {"player_id": 7, "player": {"data": {"id": 7, "fullname": "Arjun Batter"}}, "lineup": {"team_id": 1}},
                    {"player_id": 9, "player": {"data": {"id": 9, "fullname": "Nikhil Partner"}}, "lineup": {"team_id": 1}},
                    {"player_id": 10, "player": {"data": {"id": 10, "fullname": "Reserve Player"}}, "lineup": {"team_id": 1}},
                    {"player_id": 8, "player": {"data": {"id": 8, "fullname": "Bowler Example"}}, "lineup": {"team_id": 2}},
                ],
                "balls": [
                    {
                        "id": 100, "inning": 1, "team_id": 1, "ball": "0.1",
                        "batsman_id": 7, "non_striker_id": 9, "bowler_id": 8,
                        "batsman": {"data": {"id": 7}}, "non_striker": {"data": {"id": 9}},
                        "bowler": {"data": {"id": 8}},
                        "score": {"runs": 4, "four": 1, "ball": 1},
                    },
                    {
                        "id": 100, "inning": 1, "team_id": 1, "ball": "0.1",
                        "batsman_id": 7, "non_striker_id": 9, "bowler_id": 8,
                        "score": {"runs": 4, "four": 1, "ball": 1},
                    },
                    {
                        "id": 101, "inning": 1, "team_id": 1, "ball": "0.2",
                        "batsman_id": 7, "non_striker_id": 9, "bowler_id": 8,
                        "score": {"runs": 1, "ball": 1},
                    },
                    {
                        "id": 102, "inning": 1, "team_id": 1, "ball": "0.2",
                        "batsman_id": 7, "non_striker_id": 9, "bowler_id": 8,
                        "score": {"runs": 1, "name": "Wide", "ball": 0},
                    },
                    {
                        "id": 103, "inning": 1, "team_id": 1, "ball": "0.2",
                        "batsman_id": 7, "non_striker_id": 9, "bowler_id": 8,
                        "score": {"runs": 1, "noball_runs": 1, "name": "No Ball", "ball": 0},
                    },
                    {
                        "id": 104, "inning": 1, "team_id": 1, "ball": "0.3",
                        "batsman_id": 7, "non_striker_id": 9, "bowler_id": 8,
                        "batsmanout_id": 9,
                        "score": {"runs": 0, "name": "Run Out", "is_wicket": 1, "ball": 1},
                    },
                ],
            }
        }
        with patch("app.api.matches.provider.get_fixture", new_callable=AsyncMock) as fetch:
            fetch.return_value = fixture
            response = client.get("/api/matches/43")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        card = body["scorecards"][0]
        self.assertEqual(card["runs"], 8)
        self.assertEqual(card["wickets"], 1)
        self.assertEqual(card["overs"], "0.3")
        self.assertEqual(card["extras"], {"wides": 1, "no_balls": 1, "byes": 0, "leg_byes": 0, "penalty": 0})
        self.assertEqual(card["fall_of_wickets"][0]["score"], 8)
        self.assertEqual(card["partnerships"][0]["runs"], 8)
        self.assertEqual(
            {item["name"]: item["runs"] for item in card["partnerships"][0]["contributions"]},
            {"Arjun Batter": 6, "Nikhil Partner": 0},
        )
        self.assertEqual(card["did_not_bat"], [{"player_id": 10, "name": "Reserve Player"}])
        batter_rows = {row["name"]: row for row in card["batting"]}
        self.assertEqual(batter_rows["Arjun Batter"]["runs"], 6)
        self.assertEqual(batter_rows["Arjun Batter"]["balls"], 4)
        self.assertEqual(batter_rows["Nikhil Partner"]["dismissal"], "Run out (player unavailable)")
        self.assertEqual(body["current_batters"][0]["name"], "Arjun Batter")
        self.assertIsNone(body["current_batters"][0]["current_role"])
        self.assertEqual(body["current_bowler"]["name"], "Bowler Example")
        self.assertEqual(card["bowling"][0]["legal_balls"], 3)
        self.assertEqual(card["bowling"][0]["runs"], 8)
        self.assertEqual(card["bowling"][0]["wickets"], 0)
        self.assertEqual(len(body["deliveries"]), 5)

    def test_dismissal_attribution_uses_sportmonks_player_ids_and_lineup_names(self):
        fixture_data = {
            "id": 48,
            "status": "In Progress",
            "type": "T20I",
            "localteam": {"id": 1, "name": "North Stars"},
            "visitorteam": {"id": 2, "name": "Coastal XI"},
            "runs": [{"team_id": 1, "inning": 1, "score": 0, "wicket": 1, "overs": "0.1"}],
            "lineup": [
                {"id": 7, "fullname": "Dismissed Batter", "lineup": {"team_id": 1}},
                {"id": 8, "fullname": "Bowler Example", "lineup": {"team_id": 2}},
                {"id": 9, "fullname": "Catch Fielder", "lineup": {"team_id": 2}},
                {"id": 10, "fullname": "Run-out Fielder", "lineup": {"team_id": 2}},
            ],
        }
        cases = [
            (
                {"name": "Catch Out", "is_wicket": 1, "ball": 1},
                {"catchstump_id": 9},
                "c Catch Fielder b Bowler Example",
                "fielder",
                9,
            ),
            (
                {"name": "Run Out", "is_wicket": 1, "ball": 1},
                {"runout_by_id": 10},
                "Run out (Run-out Fielder)",
                "runout_by",
                10,
            ),
            (
                {"name": "Bowled", "is_wicket": 1, "ball": 1},
                {},
                "b Bowler Example",
                "fielder",
                None,
            ),
        ]
        for score, dismissal_fields, expected, player_field, expected_id in cases:
            fixture = {
                "data": {
                    **fixture_data,
                    "balls": [{
                        "id": 1,
                        "ball": "0.1",
                        "scoreboard": "S1",
                        "team_id": 1,
                        "batsman_id": 7,
                        "bowler_id": 8,
                        "batsmanout_id": 7,
                        **dismissal_fields,
                        "score": {"runs": 0, **score},
                    }],
                }
            }
            with self.subTest(dismissal=score["name"]), patch(
                "app.api.matches.provider.get_fixture",
                new_callable=AsyncMock,
            ) as fetch:
                fetch.return_value = fixture
                response = client.get("/api/matches/48")

            self.assertEqual(response.status_code, 200)
            body = response.json()
            self.assertEqual(body["deliveries"][0]["dismissal"], expected)
            self.assertEqual(body["scorecards"][0]["fall_of_wickets"][0]["dismissal"], expected)
            self.assertEqual(body["deliveries"][0][f"{player_field}_id"], expected_id)
            self.assertEqual(body["validation"]["status"], "ok")

    def test_scorecards_keep_both_innings_and_use_delivery_checkpoint_stats(self):
        fixture = {
            "data": {
                "id": 44,
                "status": "In Progress",
                "localteam": {"id": 1, "name": "North Stars"},
                "visitorteam": {"id": 2, "name": "Coastal XI"},
                "runs": [
                    {"team_id": 2, "inning": 1, "score": 3, "wicket": 0, "overs": "0.1"},
                    {"team_id": 1, "inning": 2, "score": 4, "wicket": 0, "overs": "0.1"},
                ],
                "batting": [{
                    "player_id": 17, "batsman": {"fullname": "Live Batter"},
                    "team_id": 1, "inning": 2, "score": 99, "ball": 50, "active": 1,
                }, {
                    "player_id": 18, "batsman": {"fullname": "Partner"},
                    "team_id": 1, "inning": 2, "score": 0, "ball": 0, "active": 1,
                }],
                "balls": [
                    {
                        "id": 201, "inning": 1, "team_id": 2, "ball": "0.1",
                        "batsman_id": 11, "bowler_id": 12,
                        "batsman": {"fullname": "First Innings Batter"},
                        "bowler": {"fullname": "Second Innings Bowler"},
                        "score": {"runs": 3, "ball": 1},
                    },
                    {
                        "id": 202, "inning": 2, "team_id": 1, "ball": "0.1",
                        "batsman_id": 17, "bowler_id": 19,
                        "batsman": {"fullname": "Live Batter"},
                        "bowler": {"fullname": "Live Bowler"},
                        "score": {"runs": 4, "four": 1, "ball": 1},
                    },
                ],
            }
        }
        with patch("app.api.matches.provider.get_fixture", new_callable=AsyncMock) as fetch:
            fetch.return_value = fixture
            response = client.get("/api/matches/44")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual([card["runs"] for card in body["scorecards"]], [3, 4])
        self.assertEqual([card["team_id"] for card in body["scorecards"]], [2, 1])
        self.assertEqual(body["current_batters"][0]["runs"], 4)
        self.assertNotEqual(body["current_batters"][0]["runs"], 99)
        current_roles = {batter["name"]: batter["current_role"] for batter in body["current_batters"]}
        self.assertEqual(current_roles, {"Live Batter": "striker", "Partner": "non_striker"})
        self.assertEqual(body["current_bowler"]["name"], "Live Bowler")

    def test_scorecard_recalculates_when_a_new_delivery_arrives(self):
        updated = {
            **FIXTURE,
            "data": {
                **FIXTURE["data"],
                "runs": [{"team_id": 1, "inning": 1, "score": 5, "wicket": 0, "overs": "0.2"}],
                "balls": [
                    *FIXTURE["data"]["balls"],
                    {
                        "id": 100,
                        "ball": "0.2",
                        "scoreboard": "S1",
                        "team_id": 1,
                        "batsman": {"fullname": "A. Player"},
                        "bowler": {"fullname": "B. Player"},
                        "score": {"runs": 1, "ball": 1},
                    },
                ],
            },
        }
        with patch("app.api.matches.provider.get_fixture", new_callable=AsyncMock) as fetch:
            fetch.side_effect = [FIXTURE, updated]
            first = client.get("/api/matches/42").json()
            second = client.get("/api/matches/42").json()

        self.assertEqual(first["scorecards"][0]["runs"], 4)
        self.assertEqual(second["scorecards"][0]["runs"], 5)
        self.assertEqual(second["scorecards"][0]["overs"], "0.2")
        self.assertEqual(second["current_batters"][0]["runs"], 5)
        self.assertEqual(second["current_batters"][0]["balls"], 2)
        self.assertEqual(first["intelligence"]["situation"]["measured_metrics"]["legal_balls"], 1)
        self.assertEqual(second["intelligence"]["situation"]["measured_metrics"]["legal_balls"], 2)

    def test_retired_hurt_is_not_counted_as_a_wicket(self):
        fixture = {
            "data": {
                "id": 45,
                "status": "In Progress",
                "localteam": {"id": 1, "name": "North Stars"},
                "visitorteam": {"id": 2, "name": "Coastal XI"},
                "runs": [{"team_id": 1, "inning": 1, "score": 0, "wicket": 0, "overs": "0.1"}],
                "balls": [{
                    "id": 301, "inning": 1, "team_id": 1, "ball": "0.1",
                    "batsman_id": 7, "bowler_id": 8,
                    "batsman": {"fullname": "Injured Batter"},
                    "bowler": {"fullname": "Bowler Example"},
                    "score": {"runs": 0, "name": "Retired Hurt", "is_wicket": 1, "ball": 1},
                }],
            }
        }
        with patch("app.api.matches.provider.get_fixture", new_callable=AsyncMock) as fetch:
            fetch.return_value = fixture
            response = client.get("/api/matches/45")

        self.assertEqual(response.status_code, 200)
        card = response.json()["scorecards"][0]
        self.assertEqual(card["wickets"], 0)
        self.assertEqual(card["fall_of_wickets"], [])
        self.assertEqual(card["bowling"][0]["wickets"], 0)

    def test_missing_match_returns_not_found(self):
        with patch("app.api.matches.provider.get_fixture", new_callable=AsyncMock) as fetch:
            fetch.return_value = {"data": []}
            response = client.get("/api/matches/42")

        self.assertEqual(response.status_code, 404)

    def test_finished_match_is_marked_complete(self):
        finished_fixture = {
            **FIXTURE,
            "data": {**FIXTURE["data"], "status": "Finished"},
        }
        with patch("app.api.matches.provider.get_fixture", new_callable=AsyncMock) as fetch:
            fetch.return_value = finished_fixture
            response = client.get("/api/matches/42")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["is_finished"])
        self.assertEqual(body["current_batters"], [])
        self.assertFalse(any(player["is_current"] for card in body["scorecards"] for player in card["batting"]))

    def test_provider_error_is_returned_as_service_unavailable(self):
        with patch("app.api.matches.provider.get_livescores", new_callable=AsyncMock) as fetch:
            fetch.side_effect = SportmonksError("Provider unavailable")
            response = client.get("/api/matches/live")

        self.assertEqual(response.status_code, 503)
        self.assertIn("Provider unavailable", response.json()["detail"])

    def test_sportmonks_http_errors_are_classified_without_exposing_credentials(self):
        token = "provider-test-token"

        class ErrorClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

            async def get(self, url, params):
                request = httpx.Request("GET", url, params=params)
                return httpx.Response(400, json={"message": "Bad request"}, request=request)

        with patch("app.services.providers.sportmonks.settings.sportmonks_api_token", token):
            with patch("app.services.providers.sportmonks.httpx.AsyncClient", return_value=ErrorClient()):
                with self.assertRaises(SportmonksError) as caught:
                    asyncio.run(SportmonksProvider().get_livescores())

        self.assertEqual(str(caught.exception), "Sportmonks rejected the request (HTTP 400).")
        self.assertEqual(caught.exception.status_code, 400)
        self.assertFalse(caught.exception.retryable)
        self.assertNotIn(token, str(caught.exception))

    def test_unauthorized_provider_error_is_not_retryable_and_logs_redacted_detail(self):
        token = "provider-test-token"

        class ErrorClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

            async def get(self, url, params):
                request = httpx.Request("GET", url, params=params)
                return httpx.Response(
                    401,
                    json={"message": f"Rejected token {token}"},
                    request=request,
                )

        with patch("app.services.providers.sportmonks.settings.sportmonks_api_token", token):
            with patch("app.services.providers.sportmonks.httpx.AsyncClient", return_value=ErrorClient()):
                with self.assertLogs("app.services.providers.sportmonks", level="WARNING") as logs:
                    with self.assertRaises(SportmonksError) as caught:
                        asyncio.run(SportmonksProvider().get_livescores())

        self.assertEqual(caught.exception.category, "authentication")
        self.assertEqual(caught.exception.status_code, 401)
        self.assertFalse(caught.exception.retryable)
        self.assertNotIn(token, "\n".join(logs.output))

    def test_sportmonks_too_many_attempts_response_is_treated_as_rate_limit(self):
        class ErrorClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

            async def get(self, url, params):
                request = httpx.Request("GET", url, params=params)
                return httpx.Response(400, json={"message": "Too Many Attempts."}, request=request)

        with patch("app.services.providers.sportmonks.settings.sportmonks_api_token", "provider-test-token"):
            with patch("app.services.providers.sportmonks.httpx.AsyncClient", return_value=ErrorClient()):
                with self.assertRaises(SportmonksError) as caught:
                    asyncio.run(SportmonksProvider().get_livescores())

        self.assertEqual(caught.exception.status_code, 400)
        self.assertEqual(caught.exception.category, "rate_limit")
        self.assertTrue(caught.exception.retryable)

    def test_provider_auth_error_has_meaningful_nonretryable_api_response(self):
        error = SportmonksError(
            "Sportmonks rejected the backend API token (HTTP 401). Verify SPORTMONKS_API_TOKEN in backend/.env.",
            status_code=401,
            retryable=False,
            category="authentication",
        )
        with patch("app.api.matches.provider.get_livescores", new_callable=AsyncMock) as fetch:
            fetch.side_effect = error
            response = client.get("/api/matches/live")

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["category"], "authentication")
        self.assertFalse(response.json()["retryable"])
        self.assertIn("SPORTMONKS_API_TOKEN", response.json()["detail"])

    def test_rate_limit_timeout_and_upstream_server_errors_keep_distinct_statuses(self):
        cases = [
            (
                SportmonksError("Rate limited.", status_code=400, retryable=True, retry_after_seconds=45, category="rate_limit"),
                429,
                True,
                "45",
            ),
            (
                SportmonksError("Sportmonks request timed out.", retryable=True, category="timeout"),
                504,
                True,
                None,
            ),
            (
                SportmonksError("Sportmonks is temporarily unavailable.", status_code=503, retryable=True, category="upstream_server"),
                502,
                True,
                None,
            ),
        ]
        for error, expected_status, retryable, retry_after in cases:
            with self.subTest(category=error.category):
                with patch("app.api.matches.provider.get_livescores", new_callable=AsyncMock) as fetch:
                    fetch.side_effect = error
                    response = client.get("/api/matches/live")
                self.assertEqual(response.status_code, expected_status)
                self.assertEqual(response.json()["retryable"], retryable)
                self.assertEqual(response.headers.get("Retry-After"), retry_after)

    def test_empty_live_response_is_a_consistent_empty_list(self):
        with patch("app.api.matches.provider.get_livescores", new_callable=AsyncMock) as fetch:
            fetch.return_value = {"data": []}
            response = client.get("/api/matches/live")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["matches"], [])


if __name__ == "__main__":
    unittest.main()
