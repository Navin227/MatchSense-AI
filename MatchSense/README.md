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
