"""Declarative base and shared column helpers."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, MetaData, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from backend.database.types import GUID

# Predictable constraint / index names — required for clean Alembic
# migrations and for asserting constraints in tests.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utcnow() -> datetime:
    """Timezone-aware UTC now (all timestamps are tz-aware)."""
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UUIDPKMixin:
    """Opaque internal UUID primary key (never provider-derived)."""

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(), primary_key=True, default=uuid.uuid4
    )


class DisplayIdMixin:
    """Human-readable unique identifier (e.g. ``M-019``, ``RC-10281``)."""

    display_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)


class TimestampMixin:
    """System audit timestamps — distinct from business-lifecycle times."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )


class CreatedAtMixin:
    """Only a creation timestamp — for append-only / immutable rows."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
