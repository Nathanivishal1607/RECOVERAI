"""Engine / session management (lazy).

The engine is created on first use, not at import time, so that importing
the ORM models (for Alembic, tests, or tooling) never needs a live DB
driver or a reachable database.

The DB URL comes from ``settings.database_url``. SQLAlchemy accepts the
plain ``postgresql://`` URL and picks the default driver (psycopg2).
"""

from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from backend.core.config import settings


def _make_engine(url: str) -> Engine:
    connect_args: dict = {}
    kwargs: dict = {"future": True, "pool_pre_ping": True}
    if url.startswith("sqlite"):
        from sqlalchemy.pool import StaticPool

        connect_args["check_same_thread"] = False
        kwargs["poolclass"] = StaticPool
    eng = create_engine(url, connect_args=connect_args, **kwargs)

    if eng.dialect.name == "sqlite":

        @event.listens_for(eng, "connect")
        def _fk_pragma(dbapi_connection, _record):  # pragma: no cover - trivial
            cur = dbapi_connection.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    return eng


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    return _make_engine(settings.database_url)


@lru_cache(maxsize=1)
def _sessionmaker() -> sessionmaker:
    return sessionmaker(
        bind=get_engine(),
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        future=True,
    )


def SessionLocal() -> Session:  # noqa: N802 - keep familiar call-site name
    return _sessionmaker()()


def get_db() -> Iterator[Session]:
    """FastAPI dependency: yield a session, always close it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ping() -> bool:
    """Cheap connectivity check for /health."""
    with get_engine().connect() as conn:
        conn.execute(text("SELECT 1"))
    return True
