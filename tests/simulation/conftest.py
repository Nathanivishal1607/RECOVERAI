"""Fixtures for Phase 2 simulator tests — a small in-memory run."""

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
def sim_run():
    """One deterministic ~80-case run, shared across a module's tests."""
    eng = _engine()
    Session = sessionmaker(bind=eng, future=True, expire_on_commit=False)
    db = Session()
    cfg = replace(SimConfig(seed=7), n_cases=80, customers_per_merchant=60)
    try:
        res = run_simulation(db, cfg)
        gt = GroundTruthStore.load(res.run_id)
        yield {"db": db, "result": res, "cfg": cfg, "gt": gt, "Session": Session}
    finally:
        db.close()
        try:
            gt.path.unlink(missing_ok=True)
        except Exception:
            pass
        eng.dispose()


@pytest.fixture()
def fresh_db():
    eng = _engine()
    Session = sessionmaker(bind=eng, future=True, expire_on_commit=False)
    db = Session()
    try:
        yield db
    finally:
        db.close()
        eng.dispose()
