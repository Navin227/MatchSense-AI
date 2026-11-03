# MatchSense AI Backend

## Setup (Windows PowerShell)
```powershell
cd backend
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
# Add your Sportmonks token to .env
uvicorn app.main:app --reload
```

Open http://localhost:8000/docs and http://localhost:8000/health.

Endpoints:
- `GET /api/matches/live` returns `matches`, `poll_interval_seconds`, and `updated_at`. Scores are grouped by team when Sportmonks supplies team IDs.
- `GET /api/matches/{fixture_id}` returns normalized innings scorecards, current batter/bowler stats, all available deliveries, run progression, over scoring data, and deterministic live intelligence calculated from the same provider snapshot. Scorecard figures are recalculated from unique provider deliveries when available; provider batting/bowling figures are used only when delivery detail is absent. Current player names resolve from nested player resources and lineup IDs. Innings scorecards include extras, fall of wickets, did-not-bat players, current partnership contributions, and T10/T20/ODI powerplay figures when the format is supplied by Sportmonks.
- `GET /api/matches/{fixture_id}/intelligence` returns the current fixture's situation, matchups, strategic debate, executive decision, field suggestion, and disclosed win-probability estimate.
- `POST /api/matches/{fixture_id}/chat` accepts `{"question":"Why did momentum change?"}` and answers from the selected fixture's current provider snapshot. It uses the same validated deterministic/LLM response pipeline as replay chat, but never substitutes replay data or final scorecard aggregates for unprocessed live deliveries.

The dashboard polls live match data using `POLL_INTERVAL_SECONDS` (default: 30; allowed range: 5–3600 seconds). Empty live-score responses return an empty `matches` list. Missing fixtures return `404`; Sportmonks 401/403 responses are reported as non-retryable configuration/subscription errors, 429 and the provider's 400 `Too Many Attempts` response are treated as rate limits, timeouts return `504`, and upstream 5xx errors return `502`. Rate limits use the upstream retry delay when present, otherwise the configured poll interval. Safe logs include the provider status and bounded error detail, with token values redacted. Finished matches remain available through the fixture endpoint with `is_finished: true`.

The fixture request includes player relationships on the batting, bowling, and ball-by-ball resources in addition to the lineup. Unresolved player IDs are logged by the backend; the frontend displays unavailable statistics as `—` rather than substituting another player.

## Historical replay

The replay mode fetches the real Sportmonks Fixture 216 delivery feed (India vs West Indies, 1st T20I) through the existing provider. It does not add the fixture to live-match results.

- `POST /api/replay` creates a paused replay and returns its session ID and initial state.
- `GET /api/replay/{session_id}` retrieves the current replay state.
- `WS /api/replay/{session_id}/ws` streams `replay_state`, `delivery_update`, `match_event`, and `replay_complete` events. Send JSON commands `{"action":"play"}`, `{"action":"pause"}`, `{"action":"resume"}`, `{"action":"reset"}`, or `{"action":"set_speed","speed":2}`. Supported speeds are 1x, 2x, and 5x.

`REPLAY_BASE_INTERVAL_SECONDS` controls the 1x delivery interval (default: 1 second); 2x and 5x scale that interval. Replay score, overs, run rate, and player figures are recomputed from each processed delivery. The interface labels replay as historical/simulated throughout; replay state is never returned by the live-match endpoint.

## Replay intelligence

The intelligence endpoints are bound to a replay session and use only deliveries processed at its current checkpoint:

- `GET /api/intelligence/{session_id}` returns situation analysis, evidence-backed tactical debate and recommendation, observed batter/bowler matchups, an explicitly illustrative field setup, and a disclosed deterministic win estimate when a chase is underway.
- `POST /api/intelligence/{session_id}/scenario` accepts `{"kind":"additional_runs|wicket_not_fallen|bowler_runs_saved|earlier_acceleration|alternative_bowler","runs":15}`. Scenario math holds completed deliveries fixed and does not forecast changed future deliveries. Alternative-bowler scenarios return `422` when the feed lacks complete over-by-over allocation.
- `POST /api/intelligence/{session_id}/chat` accepts `{"question":"Why did momentum change?"}` and returns a validated answer, labeled evidence, confidence, limitations, provider mode, and replay delivery checkpoint.

The chat context is rebuilt from deliveries processed in the selected replay session. It includes both innings' batting and bowling scorecards, player/team attribution, dismissals, over scoring, and ball-by-ball history through that checkpoint. Factual questions (top scorers by team, wickets, economy, strike rate, totals, over runs, comparisons, and wicket scenarios) are calculated in Python and do not call an LLM. Explanatory questions use a strict JSON response schema; invalid or incomplete model output is discarded. The UI keeps conversation history and separates answer, evidence, and limitations.

## Match story

`POST /api/narrative` builds innings and player stories from the delivery records processed at the current live or replay checkpoint. One canonical innings timeline supplies every narrative fact: it orders labeled deliveries by over and ball (preserving feed order for repeated labels), rebuilds score, wicket, and legal-ball checkpoints, and reconciles them against the innings aggregates. Over totals, batter progressions, bowler spells and matchups, partnerships, wickets, phase totals, turning points, and chase-rate changes all derive from that timeline. The response uses chronological passages to connect the opening, scoring shifts, partnerships, wickets, and finish, followed by player, bowler, and innings assessments. First and second innings are rendered separately; a chase story includes its target and result when those facts are available.

If no configured provider returns a valid narrative, the endpoint uses a context-aware deterministic story built from those same delivery facts. Provider diagnostics are logged on the backend; the interface only labels the fallback “AI narrative unavailable — showing data-derived analysis.” Live and replay narratives are checkpoint-scoped, and replay refreshes only when a delivery changes a meaningful event (a boundary, wicket, or over completion).

Configure the LLM in `backend/.env`:

- `LLM_PROVIDER=ollama` (default), `OLLAMA_BASE_URL=http://localhost:11434`, and `OLLAMA_MODEL=qwen2.5:3b` use a local Ollama server first. Install/start Ollama and pull the selected model (for example `ollama pull qwen2.5:3b`).
- If the primary provider fails or is rate-limited, each other configured provider is tried once in order: Cerebras, Gemini, Groq, then Ollama. Only cloud providers with API keys are eligible. Set `CEREBRAS_API_KEY`, `GEMINI_API_KEY`, or `GROQ_API_KEY` to enable them; set `LLM_PROVIDER` to choose the primary and use the provider-specific model settings to select an available model. Cerebras defaults to `qwen-3.8-27b`; the narrative request sends the canonical delivery timeline and reserves enough output tokens for the validated report.
- `LLM_TIMEOUT_SECONDS`, `LLM_RETRY_ATTEMPTS`, `LLM_MAX_OUTPUT_TOKENS`, and `LLM_CACHE_TTL_SECONDS` tune request timeout, transient retry count, output limit, and in-memory response caching.

Provider keys remain on the backend. No provider response body or prompt/context is logged. When all LLM attempts fail, the service returns a deterministic checkpoint-based response and explains the limitation rather than returning raw provider output.

The win estimate is a deterministic, uncalibrated heuristic—not official odds. Matchups are computed only from observed replay deliveries; the fixture does not provide external head-to-head data, bowler type, or wagon-wheel coordinates. Field placements are conventional suggestions and are never represented as actual placements.

Live intelligence uses only deliveries returned for the selected fixture at the time of the request. Matchups are current-innings measurements; no external historical head-to-head data is claimed. Powerplay figures are shown only for recognized T10/T20/ODI formats. Win probability is available only for 20-over chases and is a disclosed, uncalibrated estimate; unknown or unsupported formats do not receive an assumed T20 rate or win estimate. The live dashboard stops automatic polling after non-retryable credential/subscription errors; use manual refresh after correcting configuration.

Never commit `.env` or expose your API token in frontend code.
