"""initial schema — Phase 1B data layer

Realizes the finalized Phase 1A data contract (ADR-009..ADR-012). The
schema is defined once, on the ORM models; this migration creates it
verbatim from ``Base.metadata`` so model/migration parity is guaranteed.
Later migrations use ``--autogenerate`` against the same metadata.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-09-01
"""

from __future__ import annotations

from alembic import op

from backend.models import Base

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
