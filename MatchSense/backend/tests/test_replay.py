import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.services.match_normalizer import normalize_fixture
from app.services.replay_engine import MatchStateEngine, ReplayService


def delivery(index, batter, *, runs=0, legal=True, wicket=False, four=False, six=False):
    return {
        "source_id": index,
        "over_ball": f"0.{index}",
        "innings": "1",
        "team_id": 43,
        "batter_id": 109 if batter == "Hope" else 110,
        "bowler_id": 58,
        "batter": batter,
        "bowler": "Yadav",
        "runs_batter": runs,
        "runs_total": runs,
        "runs_bowler": runs,
        "is_legal": legal,
        "is_wicket": wicket,
        "four": four,
        "six": six,
        "dismissal": "Bowled" if wicket else None,
    }


NORMALIZED_FIXTURE = {
    "id": 216,
    "localteam": {"id": 10, "name": "India", "short_name": "IND"},
    "visitorteam": {"id": 43, "name": "West Indies", "short_name": "WI"},
    "batting": [{"player_id": 109, "name": "Hope"}, {"player_id": 110, "name": "Hetmyer"}],
    "bowling": [{"player_id": 58, "name": "Yadav"}],
    "deliveries": [
        delivery(1, "Hope", runs=4, four=True),
        delivery(2, "Hope", wicket=True),
        delivery(3, "Hetmyer", runs=1),
        delivery(4, "Hetmyer", runs=2),
        delivery(5, "Hope"),
        delivery(6, "Hetmyer", runs=1),
    ],
}

SPORTMONKS_FIXTURE = {
    "data": {
        "id": 216,
        "status": "Finished",
        "localteam": {"id": 10, "name": "India", "code": "IND"},
        "visitorteam": {"id": 43, "name": "West Indies", "code": "WI"},
        "runs": [],
        "batting": [{"player_id": 109, "batsman": {"fullname": "Shai Hope"}, "score": 4, "ball": 2}],
        "bowling": [{"player_id": 58, "bowler": {"fullname": "Umesh Yadav"}, "overs": 1, "runs": 4}],
        "balls": [{
            "id": 6527,
            "ball": 0.1,
            "scoreboard": "S1",
            "team_id": 43,
            "batsman_id": 109,
            "bowler_id": 58,
            "batsman": {"id": 109, "fullname": "Shai Hope"},
            "bowler": {"id": 58, "fullname": "Umesh Yadav"},
            "score": {
                "runs": 4,
                "four": True,
                "six": False,
                "bye": 0,
                "leg_bye": 0,
                "noball_runs": 0,
                "is_wicket": False,
                "ball": True,
                "name": "FOUR",
            },
        }],
    }
}


class MatchStateEngineTests(unittest.TestCase):
    def test_delivery_steps_recalculate_score_and_trigger_events(self):
        engine = MatchStateEngine(NORMALIZED_FIXTURE)
        first = engine.step()
        second = engine.step()
        for _ in range(4):
            final = engine.step()

        self.assertEqual(first["state"]["current_score"]["runs"], 4)
        self.assertEqual(first["detected_events"][0]["event"], "boundary")
        self.assertEqual(second["detected_events"][0]["event"], "wicket")
        self.assertEqual(final["detected_events"][0]["event"], "over_complete")
        self.assertEqual(final["state"]["current_score"]["runs"], 8)
        self.assertEqual(final["state"]["current_score"]["wickets"], 1)
        self.assertEqual(final["state"]["current_score"]["overs"], "1.0")
        self.assertEqual(final["state"]["current_score"]["run_rate"], 8.0)
        self.assertEqual(final["state"]["batting"][0]["runs"], 4)
        self.assertEqual(final["state"]["bowling"][0]["wickets"], 1)

    def test_reset_clears_the_pipeline_without_touching_source_deliveries(self):
        engine = MatchStateEngine(NORMALIZED_FIXTURE)
        engine.step()
        engine.reset()

        self.assertEqual(engine.snapshot()["delivery_index"], 0)
        self.assertEqual(engine.snapshot()["current_score"], None)
        self.assertEqual(len(engine.deliveries), 6)

    def test_wide_extras_do_not_increment_batter_runs(self):
        fixture = normalize_fixture({
            "data": {
                "id": 216,
                "balls": [{
                    "id": 1,
                    "scoreboard": "S1",
                    "batsman_id": 109,
                    "bowler_id": 58,
                    "batsman": {"id": 109, "fullname": "Shai Hope"},
                    "bowler": {"id": 58, "fullname": "Umesh Yadav"},
                    "score": {
                        "name": "1 Wide",
                        "runs": 1,
                        "ball": False,
                        "is_wicket": False,
                    },
                }],
            },
        })
        engine = MatchStateEngine(fixture)
        update = engine.step()

        self.assertEqual(update["state"]["current_score"]["runs"], 1)
        self.assertEqual(update["state"]["batting"][0]["runs"], 0)
        self.assertEqual(update["state"]["current_score"]["legal_balls"], 0)

    def test_penalty_runs_count_in_innings_and_extras_not_bowler_concession(self):
        fixture = normalize_fixture({
            "data": {
                "id": 216,
                "balls": [{
                    "id": 1,
                    "scoreboard": "S1",
                    "batsman_id": 109,
                    "bowler_id": 58,
                    "batsman": {"id": 109, "fullname": "Shai Hope"},
                    "bowler": {"id": 58, "fullname": "Umesh Yadav"},
                    "score": {
                        "runs": 2,
                        "penalty_runs": 5,
                        "ball": True,
                        "is_wicket": False,
                    },
                }],
            },
        })
        delivery = fixture["deliveries"][0]
        update = MatchStateEngine(fixture).step()

        self.assertEqual(delivery["runs_total"], 7)
        self.assertEqual(delivery["runs_extras"], 5)
        self.assertEqual(delivery["extras"]["penalty"], 5)
        self.assertEqual(delivery["runs_bowler"], 2)
        self.assertEqual(update["state"]["current_score"]["runs"], 7)
        self.assertEqual(update["state"]["bowling"][0]["runs"], 2)

    def test_no_ball_counts_as_batter_ball_but_not_legal_delivery(self):
        fixture = {
            **NORMALIZED_FIXTURE,
            "deliveries": [{
                **delivery(1, "Hope", runs=2, legal=False),
                "is_wide": False,
                "is_noball": True,
                "runs_batter": 2,
                "runs_total": 3,
                "runs_bowler": 3,
            }],
        }
        update = MatchStateEngine(fixture).step()

        self.assertEqual(update["state"]["current_score"]["runs"], 3)
        self.assertEqual(update["state"]["current_score"]["legal_balls"], 0)
        self.assertEqual(update["state"]["batting"][0]["runs"], 2)
        self.assertEqual(update["state"]["batting"][0]["balls"], 1)
        self.assertEqual(update["state"]["bowling"][0]["runs"], 3)

    def test_sportmonks_no_ball_boundary_total_is_not_counted_twice(self):
        fixture = normalize_fixture({
            "data": {
                "id": 216,
                "balls": [{
                    "id": 1,
                    "scoreboard": "S1",
                    "batsman_id": 109,
                    "bowler_id": 58,
                    "batsman": {"id": 109, "fullname": "Shai Hope"},
                    "bowler": {"id": 58, "fullname": "Umesh Yadav"},
                    "score": {
                        "name": "1 No Ball + Four",
                        "runs": 5,
                        "noball_runs": 1,
                        "ball": True,
                        "is_wicket": False,
                    },
                }],
            },
        })

        delivery = fixture["deliveries"][0]
        self.assertEqual(delivery["runs_batter"], 4)
        self.assertEqual(delivery["runs_extras"], 1)
        self.assertEqual(delivery["runs_total"], 5)
        self.assertEqual(delivery["runs_bowler"], 5)
        self.assertFalse(delivery["is_legal"])

    def test_seek_rebuilds_exact_delivery_checkpoint_without_future_leakage(self):
        engine = MatchStateEngine(NORMALIZED_FIXTURE)
        first = engine.step()["state"]
        engine.seek(1)

        self.assertEqual(engine.snapshot(), first)
        engine.seek(len(NORMALIZED_FIXTURE["deliveries"]))
        final = engine.snapshot()
        self.assertEqual(final["current_score"]["runs"], 8)
        self.assertEqual(final["delivery_index"], 6)


class ReplayServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_supported_speeds_and_playback_controls(self):
        service = ReplayService(base_interval_seconds=0.1)
        service.create(NORMALIZED_FIXTURE)
        run = next(iter(service.sessions.values()))
        queue = await service.subscribe(run)
        await queue.get()

        for speed in (1, 2, 5):
            await service.command(run, {"action": "set_speed", "speed": speed})
            self.assertEqual(service.snapshot(run)["speed"], speed)
            await queue.get()

        await service.command(run, {"action": "play"})
        self.assertEqual(service.snapshot(run)["status"], "playing")
        await queue.get()
        await service.command(run, {"action": "pause"})
        self.assertEqual(service.snapshot(run)["status"], "paused")
        await queue.get()
        await service.command(run, {"action": "resume"})
        self.assertEqual(service.snapshot(run)["status"], "playing")
        await queue.get()
        await service.command(run, {"action": "reset"})
        self.assertEqual(service.snapshot(run)["status"], "ready")
        self.assertEqual(service.snapshot(run)["state"]["delivery_index"], 0)
        await queue.get()

        if run.worker is not None:
            run.worker.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await run.worker


class ReplayApiTests(unittest.TestCase):
    def test_library_lists_provider_competitions_and_seasons(self):
        competitions = [{
            "id": 3,
            "name": "Twenty20 International",
            "seasons": [{"id": 1715, "name": "2026"}],
        }]
        with patch("app.api.replay.provider.get_replay_competitions", new_callable=AsyncMock) as fetch:
            fetch.return_value = competitions
            response = client.get("/api/replay/library")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["competitions"], competitions)

    def test_library_marks_replayability_only_for_finished_fixtures_with_deliveries(self):
        competitions = [{
            "id": 3,
            "name": "Twenty20 International",
            "seasons": [{"id": 1715, "name": "2026"}],
        }]
        fixtures = [
            {
                "id": 1,
                "status": "Finished",
                "starting_at": "2026-01-01",
                "localteam": {"id": 10, "name": "Home"},
                "visitorteam": {"id": 20, "name": "Away"},
                "note": "Home won by 4 runs",
                "runs": [{"team_id": 10, "score": 100, "wickets": 5, "overs": 20}],
                "balls": [{"id": 101}],
            },
            {"id": 2, "status": "Finished", "balls": []},
            {"id": 3, "status": "Live", "balls": [{"id": 301}]},
        ]
        with (
            patch("app.api.replay.provider.get_replay_competitions", new_callable=AsyncMock) as list_competitions,
            patch("app.api.replay.provider.get_replay_fixtures", new_callable=AsyncMock) as list_fixtures,
        ):
            list_competitions.return_value = competitions
            list_fixtures.return_value = fixtures
            response = client.get("/api/replay/library/matches?league_id=3&season_id=1715")

        self.assertEqual(response.status_code, 200)
        matches = response.json()["matches"]
        self.assertEqual([match["fixture_id"] for match in matches], [1, 2])
        self.assertTrue(matches[0]["replay_available"])
        self.assertEqual(matches[0]["delivery_count"], 1)
        self.assertEqual(matches[0]["result"], "Home won by 4 runs")
        self.assertEqual(matches[0]["data_quality_status"], "warning")
        self.assertTrue(matches[0]["data_quality_issues"])
        self.assertFalse(matches[1]["replay_available"])

    def test_library_rejects_a_season_outside_the_selected_competition(self):
        competitions = [{
            "id": 3,
            "name": "Twenty20 International",
            "seasons": [{"id": 1715, "name": "2026"}],
        }]
        with (
            patch("app.api.replay.provider.get_replay_competitions", new_callable=AsyncMock) as list_competitions,
            patch("app.api.replay.provider.get_replay_fixtures", new_callable=AsyncMock) as list_fixtures,
        ):
            list_competitions.return_value = competitions
            response = client.get("/api/replay/library/matches?league_id=3&season_id=999")

        self.assertEqual(response.status_code, 404)
        list_fixtures.assert_not_awaited()

    def test_fixture_endpoint_returns_explicit_historical_replay_state(self):
        with patch("app.api.replay.provider.get_fixture", new_callable=AsyncMock) as fetch:
            fetch.return_value = SPORTMONKS_FIXTURE
            response = client.post("/api/replay", json={"fixture_id": 216})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["mode"], "HISTORICAL REPLAY / SIMULATED LIVE")
        self.assertEqual(body["state"]["localteam"]["name"], "India")
        self.assertEqual(body["state"]["current_score"], None)
        self.assertEqual(body["state"]["total_deliveries"], 1)
        self.assertEqual(body["available_speeds"], [1, 2, 5])

    def test_websocket_streams_delivery_and_event_updates(self):
        with patch("app.api.replay.provider.get_fixture", new_callable=AsyncMock) as fetch:
            fetch.return_value = SPORTMONKS_FIXTURE
            replay = client.post("/api/replay", json={"fixture_id": 216}).json()

        with client.websocket_connect(f"/api/replay/{replay['session_id']}/ws") as websocket:
            initial = websocket.receive_json()
            self.assertEqual(initial["mode"], "HISTORICAL REPLAY / SIMULATED LIVE")
            websocket.send_json({"action": "set_speed", "speed": 5})
            self.assertEqual(websocket.receive_json()["speed"], 5)
            websocket.send_json({"action": "play"})
            websocket.receive_json()
            delivery_update = websocket.receive_json()
            self.assertEqual(delivery_update["type"], "delivery_update")
            self.assertEqual(delivery_update["state"]["current_score"]["runs"], 4)
            self.assertEqual(websocket.receive_json()["type"], "match_event")
            self.assertEqual(websocket.receive_json()["type"], "replay_state")
            self.assertEqual(websocket.receive_json()["type"], "replay_complete")


client = TestClient(app)


if __name__ == "__main__":
    unittest.main()
