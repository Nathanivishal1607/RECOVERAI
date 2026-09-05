"""Database layer: declarative base, portable types, lazy engine/session.

Importing this package does NOT open a DB connection or require a driver
— the engine is created lazily on first use (see ``session.get_engine``).
"""

from backend.database.base import (
    Base,
    CreatedAtMixin,
    DisplayIdMixin,
    TimestampMixin,
    UUIDPKMixin,
    utcnow,
)
from backend.database.session import (
    SessionLocal,
    get_db,
    get_engine,
    ping,
)

__all__ = [
    "Base",
    "CreatedAtMixin",
    "DisplayIdMixin",
    "TimestampMixin",
    "UUIDPKMixin",
    "utcnow",
    "SessionLocal",
    "get_db",
    "get_engine",
    "ping",
]
