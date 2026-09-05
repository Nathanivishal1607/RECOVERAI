"""Portable column types.

Phase 1B implementation note: PostgreSQL is the authoritative target
(``docker compose``, Alembic). To keep the model/repository unit tests
fast and dependency-free they also run on SQLite in-memory, so these
types degrade gracefully:

* :class:`GUID`  -> native ``UUID`` on PostgreSQL, ``CHAR(36)`` on SQLite.
* JSON columns   -> ``JSONB`` on PostgreSQL, ``JSON`` on SQLite
  (declared directly on the models with ``postgresql.JSONB``' variant).

Array-typed policy fields are stored as JSON for portability — a
documented Phase 1B choice; the conceptual contract is unchanged.
"""

from __future__ import annotations

import uuid

from sqlalchemy import CHAR, JSON, TypeDecorator
from sqlalchemy.dialects.postgresql import JSONB as PG_JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

#: JSON column: ``JSONB`` on PostgreSQL (indexable, typed), plain ``JSON``
#: elsewhere. Used for immutable snapshots / structured context only —
#: never for data that must be independently queried as columns.
JSONColumn = JSON().with_variant(PG_JSONB(), "postgresql")


class GUID(TypeDecorator):
    """Platform-independent UUID.

    Stores a canonical 36-char string on non-PostgreSQL backends and a
    native ``uuid`` on PostgreSQL. Always returns :class:`uuid.UUID` in
    Python.
    """

    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect):  # noqa: D102
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PG_UUID(as_uuid=True))
        return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value, dialect):  # noqa: D102
        if value is None:
            return None
        if not isinstance(value, uuid.UUID):
            value = uuid.UUID(str(value))
        if dialect.name == "postgresql":
            return value
        return str(value)

    def process_result_value(self, value, dialect):  # noqa: D102
        if value is None:
            return None
        if isinstance(value, uuid.UUID):
            return value
        return uuid.UUID(str(value))
