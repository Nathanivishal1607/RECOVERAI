"""Shared test fixtures.

Model/repository tests run on an in-memory SQLite database (fast, no
external dependency). PostgreSQL remains the authoritative target for
``docker compose`` + Alembic; the portable column types (GUID, JSON,
partial indexes) make the schema valid on both.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.models import Base


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
    )
    from sqlalchemy import event

    @event.listens_for(eng, "connect")
    def _fk(dbapi_conn, _rec):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(eng)
    try:
        yield eng
    finally:
        Base.metadata.drop_all(eng)
        eng.dispose()


@pytest.fixture()
def db(engine) -> Iterator[Session]:
    SessionLocal = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    session = SessionLocal()
    try:
        yield session
        session.rollback()
    finally:
        session.close()
