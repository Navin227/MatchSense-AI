from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.matches import router as matches_router
from app.api.narrative import router as narrative_router
from app.api.replay import router as replay_router
from app.core.config import settings

app = FastAPI(title="MatchSense AI API", version="0.1.0")
allowed_origins = {
    origin.strip().rstrip("/")
    for origin in (settings.frontend_origin, "http://localhost:5173")
    if origin.strip()
}
if "*" in allowed_origins:
    raise ValueError("FRONTEND_ORIGIN must be an explicit origin, not a wildcard.")

app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted(allowed_origins),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Accept", "Authorization", "Content-Type"],
)
app.include_router(matches_router)
app.include_router(replay_router)
app.include_router(narrative_router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "matchsense-api"}
