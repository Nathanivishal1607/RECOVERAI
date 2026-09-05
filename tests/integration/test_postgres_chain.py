"""Optional: run the full relational chain against a real PostgreSQL.

Skipped unless ``RECOVERAI_TEST_PG_URL`` is set (CI / ``docker compose``).
Proves the schema + repositories work on the authoritative target DB,
including the DB-level partial-unique constraints.
"""

from __future__ import annotations

import os
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

PG_URL = os.getenv("RECOVERAI_TEST_PG_URL")

pytestmark = pytest.mark.skipif(
    not PG_URL, reason="set RECOVERAI_TEST_PG_URL to run the PostgreSQL chain test"
)


@pytest.fixture()
def pg_db():
    from backend.models import Base

    engine = create_engine(PG_URL, future=True)
    # isolated schema so we never touch dev data
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS recoverai_test CASCADE"))
        conn.execute(text("CREATE SCHEMA recoverai_test"))
        conn.execute(text("SET search_path TO recoverai_test"))
    engine = create_engine(
        PG_URL, future=True,
        connect_args={"options": "-csearch_path=recoverai_test"},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    s = Session()
    try:
        yield s
    finally:
        s.close()
        with engine.begin() as conn:
            conn.execute(text("DROP SCHEMA IF EXISTS recoverai_test CASCADE"))
        engine.dispose()


def test_full_chain_on_postgres(pg_db):
    from tests.integration.test_full_chain import test_full_recovery_chain

    test_full_recovery_chain(pg_db)


def test_partial_unique_active_case_enforced_by_db(pg_db):
    """The DB itself (not just the repo guard) rejects a 2nd active case."""
    from sqlalchemy.exc import IntegrityError

    from backend.models import enums
    from backend.models.core_entities import RecoveryCase
    from backend.repositories import (
        CustomerRepository,
        MerchantRepository,
        PaymentRepository,
    )
    from backend.database.base import utcnow
    from datetime import timedelta

    m = MerchantRepository(pg_db).create(name="pg")
    CustomerRepository(pg_db).create(customer_id="C-pg", merchant_id=m.id)
    p = PaymentRepository(pg_db).create(
        merchant_id=m.id, customer_id="C-pg", amount=Decimal("100"),
        currency="INR", status=enums.PaymentStatus.FAILED.value,
    )
    now = utcnow()
    pg_db.add(
        RecoveryCase(
            display_id="RC-a", payment_id=p.id, merchant_id=m.id, customer_id="C-pg",
            status="OPEN", opened_at=now, expires_at=now + timedelta(days=14),
            amount_at_risk=p.amount,
        )
    )
    pg_db.flush()
    pg_db.add(
        RecoveryCase(
            display_id="RC-b", payment_id=p.id, merchant_id=m.id, customer_id="C-pg",
            status="OPEN", opened_at=now, expires_at=now + timedelta(days=14),
            amount_at_risk=p.amount,
        )
    )
    with pytest.raises(IntegrityError):
        pg_db.flush()
