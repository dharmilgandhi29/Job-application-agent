from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from api.routes import scoring, ui, onboarding

app = FastAPI(
    title="Job Search Agent",
    version="0.1.0",
    description="AI-powered job scoring, resume gap analysis, and application material generation",
)

# Allow cross-origin requests (n8n, and later a frontend, run on different ports).
# Wide open for local dev — tighten allow_origins to specific hosts before any real deployment.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve the shared static assets (motion.css / motion.js and any future static files).
# Every page links these, so the motion + polish layer is defined once and shared.
_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
_STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

# Plug in the routes. Each future feature (cover letters, outreach)
# gets its own router and one include_router line here — main.py stays thin.
app.include_router(scoring.router)
app.include_router(ui.router)
app.include_router(onboarding.router)


@app.get("/health")
async def health():
    """Liveness check. Ping this to confirm the service is up without making a real call."""
    return {"status": "ok", "version": "0.1.0"}
