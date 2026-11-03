# MatchSense AI
Your AI companion during a live cricket match.

## Project layout
- `backend/`: FastAPI API and Sportmonks provider client
- `frontend/`: React + Vite + TypeScript starter UI

## Run backend (Windows PowerShell)
```powershell
cd backend
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
# Add SPORTMONKS_API_TOKEN to backend/.env
uvicorn app.main:app --reload
```

## Run frontend (new terminal)
```powershell
cd frontend
npm install
npm run dev
```

Backend docs: http://localhost:8000/docs

## Live scorecard data
Fixture requests include Sportmonks `runs`, `scoreboards`, `batting`, `bowling`,
`lineup`, `balls`, and both fixture teams. Ball-by-ball data drives delivery,
over, and scorecard calculations; `runs` and `scoreboards` are retained as
independent reconciliation sources. The Scorecard and Intelligence views display
warnings when source totals, team associations, player figures, or derived rates
disagree. No secondary scorecard feed is merged into the live response.

The API response timestamp describes the provider snapshot load. If a required
field is absent from that snapshot, the UI leaves the related statistic
unavailable rather than substituting data from another source.
Frontend: http://localhost:5173

Never commit API keys. The frontend must call the backend; provider credentials stay server-side.

## Render deployment

The existing Render services use the following configuration; keep these services and roots:

| Service | Root directory | Build command | Publish / start |
|---|---|---|---|
| Static Site | `MatchSense/frontend` | `npm install && npm run build` | Publish `dist` |
| Web Service | `MatchSense/backend` | `pip install -r requirements.txt` | `uvicorn app.main:app --host 0.0.0.0 --port $PORT` |

Set `VITE_API_BASE_URL=https://matchsense-ai.onrender.com` in the Static Site's Render environment. The frontend appends `/api` to this backend origin; local development can leave the variable unset to use Vite's `/api` proxy, or set it to `http://localhost:8000`.

Set these backend variables in the Web Service environment:

```text
FRONTEND_ORIGIN=https://matchsense-ai-1.onrender.com
SPORTMONKS_API_TOKEN=<Render secret>
SPORTMONKS_BASE_URL=https://cricket.sportmonks.com/api/v2.0
LLM_PROVIDER=cerebras
OLLAMA_ENABLED=false
CEREBRAS_API_KEY=<Render secret>
CEREBRAS_MODEL=qwen-3.8-27b
LLM_TIMEOUT_SECONDS=25
LLM_RETRY_ATTEMPTS=2
LLM_MAX_OUTPUT_TOKENS=1200
LLM_CACHE_TTL_SECONDS=120
POLL_INTERVAL_SECONDS=30
REPLAY_BASE_INTERVAL_SECONDS=1.0
```

Do not set Ollama as the production provider or copy local credentials into Render. For local development, `backend/.env.example` retains Ollama defaults. Set `FRONTEND_ORIGIN` to the exact deployed frontend origin; the backend also allows `http://localhost:5173` for local development. Render serves the API under `/api`, with health at `/health`.
