#!/bin/sh
# RecoverAI backend container entrypoint.
# 1) wait for the database, 2) run migrations, 3) start the API.
set -e

echo "[entrypoint] waiting for database ..."
python - <<'PY'
import time
from sqlalchemy import create_engine, text
from backend.core.config import settings

for attempt in range(60):
    try:
        create_engine(settings.database_url).connect().execute(text("SELECT 1"))
        print("[entrypoint] database is up")
        break
    except Exception as exc:  # noqa: BLE001
        print(f"[entrypoint]   not ready ({type(exc).__name__}); retrying...")
        time.sleep(2)
else:
    raise SystemExit("[entrypoint] database never became reachable")
PY

echo "[entrypoint] running migrations ..."
alembic -c backend/alembic.ini upgrade head

echo "[entrypoint] seeding demo data (idempotent; skips if already seeded) ..."
PYTHONPATH=. python scripts/seed_demo.py || echo "[entrypoint] seed step failed — continuing without demo data"

echo "[entrypoint] starting API ..."
exec uvicorn backend.api.main:app --host 0.0.0.0 --port 8000 --reload
