"""Fixtures for Phase 3 ML tests — a small deterministic simulator run
persisted through the Phase 1B repositories, plus a per-test artifact dir.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from backend.models import Base
from simulation.config import SimConfig
from simulation.generator.runner import run_simulation
from simulation.ground_truth.store import GroundTruthStore


def _engine():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False})

    @event.listens_for(eng, "connect")
    def _fk(dbapi_conn, _rec):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(eng)
    return eng


@pytest.fixture(scope="module")
def ml_run(tmp_path_factory):
    """~450-case run — enough labelled TrainingExamples for a 3-way split."""
    eng = _engine()
    Session = sessionmaker(bind=eng, future=True, expire_on_commit=False)
    db = Session()
    cfg = replace(SimConfig(seed=3), n_cases=450, customers_per_merchant=120)
    artifacts = tmp_path_factory.mktemp("artifacts")
    try:
        res = run_simulation(db, cfg)
        gt = GroundTruthStore.load(res.run_id)
        yield {
            "db": db,
            "Session": Session,
            "result": res,
            "cfg": cfg,
            "gt": gt,
            "artifacts": artifacts,
        }
    finally:
        db.close()
        try:
            gt.path.unlink(missing_ok=True)
        except Exception:
            pass
        eng.dispose()


@pytest.fixture()
def artifact_dir(tmp_path):
    return tmp_path / "artifacts"
