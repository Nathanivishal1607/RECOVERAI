#!/usr/bin/env bash
# Phase 5 — one command sequence for a clean environment.
#
#   ./scripts/run_demo.sh            # SQLite, end-to-end, no Docker needed
#   ./scripts/run_demo.sh --docker   # bring up Postgres via docker compose first
#
# Steps: generate synthetic data -> train + promote the T-learner ->
# run the 5 demo scenarios (A-E) -> run the offline evaluation -> tests.
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH="${PYTHONPATH:-.}"

DB_URL="sqlite:///phase5_demo.db"

if [[ "${1:-}" == "--docker" ]]; then
  echo "== bringing up the stack (Postgres + migrations + API) =="
  docker compose up -d --build
  # inside compose the API already ran `alembic upgrade head`; use that DB:
  DB_URL="${DATABASE_URL:-postgresql://recoverai:change-me@localhost:5432/recoverai}"
fi

echo
echo "== 1. generate synthetic data (SYNTHETIC — not Razorpay data) =="
python -m simulation.cli generate --size 1200 --seed 42 --database-url "$DB_URL" --reset

echo
echo "== 2. train + promote the recovery model (T-learner) =="
python -m ml.cli train --kind t_learner --promote --seed 42 --database-url "$DB_URL"

echo
echo "== 3. run the 5 demo scenarios (A: RETRY, B: MESSAGE, C: NO_ACTION,"
echo "      D: policy block, E: re-evaluation) =="
python scripts/demo.py --db "$DB_URL" --seed 42 --n-cases 1200 --keep

echo
echo "== 4. offline evaluation: RecoverAI vs naive baseline (oracle-scored) =="
python -m simulation.evaluation.engine_eval --seed 42

echo
echo "== 5. full test suite =="
python -m pytest tests/ -q

echo
echo "Done. To explore the API:  uvicorn backend.api.main:app --reload"
echo "  then e.g.  POST /payments  ->  POST /payments/{id}/evaluate  ->  GET /decisions/{id}"
