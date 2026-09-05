"""Human-readable ``display_id`` generation.

Format: ``<PREFIX>-<zero-padded counter>``. The UUID ``id`` is the real
key; ``display_id`` is a convenience label for humans/dashboards/demos.
"""

from __future__ import annotations

from sqlalchemy import update
from sqlalchemy.orm import Session

from backend.models.core_entities import DisplayIdSequence

PREFIXES = {
    "merchant": "M",
    "payment": "P",
    "recovery_case": "RC",
}


def next_display_id(db: Session, entity: str) -> str:
    prefix = PREFIXES[entity]
    row = db.get(DisplayIdSequence, prefix)
    if row is None:
        row = DisplayIdSequence(prefix=prefix, next_value=1)
        db.add(row)
        db.flush()
    n = row.next_value
    row.next_value = n + 1
    db.flush()
    return f"{prefix}-{n:05d}"
