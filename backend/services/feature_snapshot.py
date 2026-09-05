"""Build the decision-time ``feature_snapshot`` for a live RecoveryCase.

Phase 5. The snapshot is the ONLY representation the model / decision
pipeline is allowed to see. It carries information available **at decision
time** and never a hidden potential outcome, a future label, or the
customer's latent reliability.

Schema id is ``sim-feature-schema-v1`` — identical field set to
``simulation/features.py`` so a model trained on simulator data scores a
live case unchanged. The ~10-line leakage guard is copied here (not
imported) so ``backend/`` keeps zero dependency on ``simulation/``.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models.core_entities import (
    Customer,
    Merchant,
    Payment,
    PaymentEvent,
    RecoveryCase,
)

#: Must match ``simulation/features.py::FEATURE_SCHEMA_ID`` and
#: ``ml/features/schema.py::FEATURE_SCHEMA_ID``.
FEATURE_SCHEMA_ID = "sim-feature-schema-v1"

_LEAKAGE_TOKENS = (
    "potential",
    "ground_truth",
    "reliability",
    "outcome",
    "recovered",
    "recovery_amount",
    "p_retry",
    "p_message",
    "p_no_action",
    "true_",
    "oracle",
    "regime",
)

_ATTEMPTING_EVENTS = frozenset({"PAYMENT_FAILED", "RETRY_ATTEMPTED"})


def _aware(dt: datetime | None) -> datetime | None:
    """Normalise a possibly-naive datetime (SQLite round-trips tz-aware
    values as naive UTC) to tz-aware UTC so arithmetic is safe."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt

# Coarse observable defaults when a field is genuinely unknown for a live
# case (mirrors the neutral fallbacks in ``ml/features/schema.py``).
_DEFAULT_SEGMENT = "casual"
_DEFAULT_MERCHANT_SEGMENT = "ecommerce"


def assert_no_leakage(snapshot: dict) -> None:
    """Raise if any key looks like hidden ground truth / a future label."""
    for key in snapshot:
        low = str(key).lower()
        if any(tok in low for tok in _LEAKAGE_TOKENS):
            raise ValueError(f"feature snapshot leaks hidden data: {key!r}")


def _events(db: Session, payment_id) -> list[PaymentEvent]:
    return list(
        db.scalars(
            select(PaymentEvent)
            .where(PaymentEvent.payment_id == payment_id)
            .order_by(PaymentEvent.event_timestamp, PaymentEvent.created_at)
        )
    )


def _segment_from_recovery_rate(rate: float | None) -> str:
    if rate is None:
        return _DEFAULT_SEGMENT
    r = float(rate)
    if r >= 0.6:
        return "loyal"
    if r >= 0.4:
        return "regular"
    if r >= 0.2:
        return "casual"
    return "new"


def build_case_feature_snapshot(
    db: Session,
    *,
    case: RecoveryCase,
    decision_time: datetime,
) -> dict:
    """Assemble the ``sim-feature-schema-v1`` snapshot from the case's
    Payment / Customer / Merchant rows and the append-only PaymentEvent
    stream. Deterministic; no randomness; leakage-guarded before return."""
    payment = db.get(Payment, case.payment_id)
    merchant = db.get(Merchant, case.merchant_id)
    customer = db.get(Customer, case.customer_id)

    events = _events(db, case.payment_id)
    attempt_events = [e for e in events if e.event_type in _ATTEMPTING_EVENTS]
    # attempt about to be evaluated = failed + retried attempts already seen
    attempt_number = max(1, len(attempt_events))
    last_attempt_at = _aware(
        attempt_events[-1].event_timestamp if attempt_events else None
    )
    dt = _aware(decision_time)
    minutes_since_last = (
        max(0.0, (dt - last_attempt_at).total_seconds() / 60.0)
        if last_attempt_at is not None
        else 0.0
    )

    txn_count = getattr(customer, "transaction_count", 0) or 0
    succ = getattr(customer, "successful_transactions", 0) or 0
    failed = getattr(customer, "failed_transactions", 0) or 0
    hist_success_rate = round(succ / txn_count, 4) if txn_count else 0.7
    hist_failure_rate = round(failed / txn_count, 4) if txn_count else 0.3
    prev_recovery_rate = (
        float(customer.historical_recovery_rate)
        if customer is not None and customer.historical_recovery_rate is not None
        else 0.4
    )
    freq = round(txn_count / 6.0, 3) if txn_count else 2.0

    cust_segment = _segment_from_recovery_rate(prev_recovery_rate)
    merchant_segment = getattr(merchant, "industry", None) or _DEFAULT_MERCHANT_SEGMENT

    snap = {
        # customer
        "cust_tenure_days": _tenure_days(customer, decision_time),
        "cust_hist_success_rate": hist_success_rate,
        "cust_hist_failure_rate": hist_failure_rate,
        "cust_prev_recovery_rate": prev_recovery_rate,
        "cust_payment_freq_per_month": freq,
        "cust_segment": cust_segment,
        # payment
        "amount": float(case.amount_at_risk),
        "currency": payment.currency if payment is not None else "INR",
        "payment_method": (payment.payment_method if payment is not None else None)
        or "CARD",
        "failure_category": case.failure_category or "UNKNOWN",
        "failure_code": case.failure_code or "SIM_UNKNOWN",
        "attempt_number": attempt_number,
        "minutes_since_last_attempt": round(minutes_since_last, 1),
        "hour_of_day": decision_time.hour,
        "day_of_week": decision_time.weekday(),
        # merchant
        "merchant_segment": merchant_segment,
        "merchant_hist_recovery_rate": round(float(prev_recovery_rate), 4),
        "merchant_avg_txn_amount": _merchant_avg_amount(payment, case),
        "_feature_schema_id": FEATURE_SCHEMA_ID,
    }
    assert_no_leakage(snap)
    return snap


def _tenure_days(customer, decision_time: datetime) -> int:
    created = _aware(getattr(customer, "created_at", None))
    if created is None:
        return 180
    days = int((_aware(decision_time) - created).total_seconds() // 86400)
    # backdated / demo data can have created_at after the decision time —
    # fall back to the neutral default rather than a misleading 0.
    return days if days > 0 else 180


def _merchant_avg_amount(payment, case: RecoveryCase) -> float:
    if payment is not None and payment.amount is not None:
        return float(payment.amount)
    return float(case.amount_at_risk)
