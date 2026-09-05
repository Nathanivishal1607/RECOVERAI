# RecoverAI backend — development image.
# Build context is the repo ROOT (see docker-compose.yml) so that the
# `backend` package's absolute imports (e.g. `from backend.core.config
# import settings`) resolve identically inside and outside Docker.
# See docs/development/setup.md.

FROM python:3.11-slim

WORKDIR /app

COPY backend/requirements.txt ./backend/requirements.txt
COPY ml/requirements.txt ./ml/requirements.txt
# Phase 5: the recovery API imports ml.inference (the promoted model feeds
# the decision engine), so the ML deps are needed in the backend image too.
RUN pip install --no-cache-dir -r backend/requirements.txt -r ml/requirements.txt

COPY backend ./backend
COPY ml ./ml
COPY simulation ./simulation
COPY tests ./tests
# Phase 6: scripts/seed_demo.py runs at container start (see entrypoint) to
# populate demo data.
COPY scripts ./scripts
COPY infrastructure/docker/backend-entrypoint.sh /usr/local/bin/backend-entrypoint.sh
RUN chmod +x /usr/local/bin/backend-entrypoint.sh

EXPOSE 8000

# Migrations run at start-up, then uvicorn. Override CMD for e.g. pytest.
CMD ["/usr/local/bin/backend-entrypoint.sh"]
