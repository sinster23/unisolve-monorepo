from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

app = FastAPI(
    title="Developer Profile Aggregator",
    description="Self-hosted developer profile and coding statistics API.",
    version="0.1.0",
)

# In local development, Next.js runs on port 3000 and FastAPI on port 8000.
# In production, the frontend is served by this same FastAPI application.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:3000", "http://localhost:3000"],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.get("/api/health", tags=["health"])
async def health_check() -> dict[str, str]:
    return {"status": "ok"}


# apps/backend/app/main.py -> repository root/apps/frontend/out
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
FRONTEND_DIST = REPOSITORY_ROOT / "apps" / "frontend" / "out"

# Register API routes before the frontend mount so /api/* is never swallowed
# by the static frontend handler.
if FRONTEND_DIST.is_dir():
    app.mount(
        "/",
        StaticFiles(directory=str(FRONTEND_DIST), html=True),
        name="frontend",
    )
else:
    @app.get("/", include_in_schema=False)
    async def development_root() -> dict[str, str]:
        return {
            "message": "Backend is running. Start the Next.js frontend on port 3000."
        }
