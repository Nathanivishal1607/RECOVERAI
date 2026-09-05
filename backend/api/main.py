"""FastAPI application entrypoint.

Phase 1B: health check (+ optional DB connectivity probe).
Phase 5: the end-to-end recovery flow routes (``backend/api/routes/``) —
create a failed payment, evaluate recovery, retrieve the decision audit
chain, mock-execute the selected action, record an outcome, re-evaluate.
Phase 6: read-only ``/api/*`` routes for the frontend (dashboard, recovery
case list/detail).
See docs/README.md.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.routes import dashboard_router, recovery_router
from backend.core.config import settings

app = FastAPI(
    title="RecoverAI",
    description="AI Revenue Recovery Decision Engine — see /docs in the repo for the full specification.",
    version="0.6.0",
)

# Hackathon-scoped: the frontend is a separate origin (localhost:3000 ->
# localhost:8000). No auth exists yet, so this is intentionally permissive.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

app.include_router(recovery_router)
app.include_router(dashboard_router)


@app.get("/health")
def health() -> dict:
    """Liveness + DB connectivity. Never raises — reports ``db`` status so
    the endpoint is usable before/without a database."""
    db_status = "unknown"
    try:
        from backend.database import ping

        ping()
        db_status = "ok"
    except Exception as exc:  # noqa: BLE001 - health must not raise
        db_status = f"error: {type(exc).__name__}"
    return {"status": "ok", "env": settings.app_env, "db": db_status}
