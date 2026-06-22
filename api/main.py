from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api.routes import scoring

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

# Plug in the scoring routes. Each future feature (cover letters, outreach)
# gets its own router and one include_router line here — main.py stays thin.
app.include_router(scoring.router)


@app.get("/health")
async def health():
    """Liveness check. Ping this to confirm the service is up without making a real call."""
    return {"status": "ok", "version": "0.1.0"}