"""Phase 2 — generation: determinism, size, valid entities, event vocab."""

from __future__ import annotations

from dataclasses import replace

from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from backend.models import (
    Customer,
    DecisionRecord,
    Merchant,
    Payment,
    PaymentEvent,
    RecoveryCase,
)
from backend.models import enums
from simulation.config import DATASET_SIZES, SimConfig
from simulation.generator.runner import run_simulation

from .conftest import _engine


def _fingerprint(db) -> list[tuple]:
    rows = db.execute(
        select(
            RecoveryCase.display_id,
            RecoveryCase.status,
            DecisionRecord.cycle_number,
            DecisionRecord.final_action,
        )
        .join(DecisionRecord, DecisionRecord.recovery_case_id == RecoveryCase.id)
        .order_by(RecoveryCase.display_id, DecisionRecord.cycle_number)
    ).all()
    return [tuple(r) for r in rows]


def _run(cfg):
    eng = _engine()
    db = sessionmaker(bind=eng, future=True, expire_on_commit=False)()
    try:
        res = run_simulation(db, cfg)
        return res, _fingerprint(db)
    finally:
        db.close()
        eng.dispose()


def test_same_seed_same_data():
    cfg = replace(SimConfig(seed=42), n_cases=40, customers_per_merchant=40)
    res_a, fp_a = _run(cfg)
    res_b, fp_b = _run(cfg)

    volatile = {"run_id", "ground_truth_path", "seconds"}
    a = {k: v for k, v in res_a.as_dict().items() if k not in volatile}
    b = {k: v for k, v in res_b.as_dict().items() if k not in volatile}
    assert a == b
    assert fp_a == fp_b
    assert len(fp_a) > 0
    # clean up the two ground-truth sidecars
    for res in (res_a, res_b):
        from pathlib import Path

        Path(res.ground_truth_path).unlink(missing_ok=True)


def test_different_seed_different_data():
    fp1 = _run(replace(SimConfig(seed=1), n_cases=40, customers_per_merchant=40))
    fp2 = _run(replace(SimConfig(seed=2), n_cases=40, customers_per_merchant=40))
    assert fp1[1] != fp2[1]
    from pathlib import Path

    for res in (fp1[0], fp2[0]):
        Path(res.ground_truth_path).unlink(missing_ok=True)


def test_dataset_size_honoured(sim_run):
    res, cfg = sim_run["result"], sim_run["cfg"]
    assert cfg.n_cases == 80
    assert res.cases_created + res.cases_ineligible == cfg.n_cases
    assert res.cases_created >= 1

    db = sim_run["db"]
    assert db.scalar(select(func.count()).select_from(RecoveryCase)) == res.cases_created


def test_default_development_size_is_1000():
    assert DATASET_SIZES["development"] == 1_000
    assert SimConfig().n_cases == 1_000


def test_entities_are_valid(sim_run):
    db = sim_run["db"]

    assert db.scalar(select(func.count()).select_from(Merchant)) == sim_run["cfg"].n_merchants
    assert db.scalar(select(func.count()).select_from(Customer)) > 0

    for pay in db.scalars(select(Payment)):
        assert pay.amount > 0
        assert pay.currency == "INR"
        assert pay.status in enums.values(enums.PaymentStatus)
        assert pay.display_id.startswith("P-")
        # every simulated payment fails before a recovery case is considered
        assert pay.status in (
            enums.PaymentStatus.FAILED.value,
            enums.PaymentStatus.SUCCEEDED.value,
        )

    for case in db.scalars(select(RecoveryCase)):
        assert case.status in enums.values(enums.RecoveryCaseStatus)
        assert case.amount_at_risk > 0
        assert case.failure_category in {
            "TEMPORARY",
            "CUSTOMER_ACTION_REQUIRED",
            "PAYMENT_METHOD_ISSUE",
            "LIMIT_EXCEEDED",
            "UNKNOWN",
        }


def test_payment_event_vocabulary_and_attempt_numbers(sim_run):
    db = sim_run["db"]
    allowed = set(enums.values(enums.PaymentEventType))

    seen_created = seen_failed = seen_retry = 0
    by_payment: dict = {}
    for ev in db.scalars(select(PaymentEvent).order_by(PaymentEvent.event_timestamp)):
        assert ev.event_type in allowed
        by_payment.setdefault(ev.payment_id, []).append(ev)
        if ev.event_type == enums.PaymentEventType.PAYMENT_CREATED.value:
            seen_created += 1
            assert ev.attempt_number is None
        elif ev.event_type == enums.PaymentEventType.PAYMENT_FAILED.value:
            seen_failed += 1
            assert ev.attempt_number >= 1
        elif ev.event_type == enums.PaymentEventType.RETRY_ATTEMPTED.value:
            seen_retry += 1
            assert ev.attempt_number >= 2

    assert seen_created > 0 and seen_failed > 0
    # attempt_number is monotonic non-decreasing within a payment
    for events in by_payment.values():
        nums = [e.attempt_number for e in events if e.attempt_number is not None]
        assert nums == sorted(nums)


def test_no_predictions_flag_skips_predictions_and_training():
    cfg = replace(
        SimConfig(seed=5), n_cases=25, customers_per_merchant=25, with_predictions=False
    )
    eng = _engine()
    db = sessionmaker(bind=eng, future=True, expire_on_commit=False)()
    try:
        res = run_simulation(db, cfg)
        from backend.models import Prediction, TrainingExample

        assert db.scalar(select(func.count()).select_from(Prediction)) == 0
        assert db.scalar(select(func.count()).select_from(TrainingExample)) == 0
        assert res.decision_records > 0
    finally:
        from pathlib import Path

        Path(res.ground_truth_path).unlink(missing_ok=True)
        db.close()
        eng.dispose()
