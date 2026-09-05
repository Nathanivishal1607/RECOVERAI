"""Observable feature snapshot builder.

This is the ONLY feature representation the decision/model pipeline is
allowed to see. It contains information available **at decision time**
and **never** hidden potential outcomes, recovery labels, future events,
or the customer's latent ``reliability``.

~16 features across customer / payment / merchant.
"""

from __future__ import annotations

from datetime import datetime

from simulation.generator.entities import CustomerSpec, MerchantSpec, PaymentSpec

FEATURE_SCHEMA_ID = "sim-feature-schema-v1"

FEATURE_NAMES: list[str] = [
    # customer
    "cust_tenure_days",
    "cust_hist_success_rate",
    "cust_hist_failure_rate",
    "cust_prev_recovery_rate",
    "cust_payment_freq_per_month",
    "cust_segment",
    # payment
    "amount",
    "currency",
    "payment_method",
    "failure_category",
    "failure_code",
    "attempt_number",
    "minutes_since_last_attempt",
    "hour_of_day",
    "day_of_week",
    # merchant
    "merchant_segment",
    "merchant_hist_recovery_rate",
    "merchant_avg_txn_amount",
]

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
)


def build_feature_snapshot(
    *,
    merchant: MerchantSpec,
    customer: CustomerSpec,
    payment: PaymentSpec,
    decision_time: datetime,
    attempt_number: int,
    last_attempt_time: datetime,
) -> dict:
    minutes_since = max(0.0, (decision_time - last_attempt_time).total_seconds() / 60.0)
    snap = {
        "cust_tenure_days": customer.tenure_days,
        "cust_hist_success_rate": customer.hist_success_rate,
        "cust_hist_failure_rate": customer.hist_failure_rate,
        "cust_prev_recovery_rate": customer.prev_recovery_rate,
        "cust_payment_freq_per_month": customer.payment_frequency_per_month,
        "cust_segment": customer.segment,
        "amount": payment.amount,
        "currency": payment.currency,
        "payment_method": payment.method,
        "failure_category": payment.failure_category,
        "failure_code": payment.failure_code,
        "attempt_number": attempt_number,
        "minutes_since_last_attempt": round(minutes_since, 1),
        "hour_of_day": decision_time.hour,
        "day_of_week": decision_time.weekday(),
        "merchant_segment": merchant.segment,
        "merchant_hist_recovery_rate": merchant.historical_recovery_rate,
        "merchant_avg_txn_amount": merchant.avg_txn_amount,
        "_feature_schema_id": FEATURE_SCHEMA_ID,
    }
    assert_no_leakage(snap)
    return snap


def assert_no_leakage(snapshot: dict) -> None:
    """Raise if any key looks like hidden ground truth / a future label."""
    for key in snapshot:
        low = str(key).lower()
        if any(tok in low for tok in _LEAKAGE_TOKENS):
            raise ValueError(f"feature snapshot leaks hidden data: {key!r}")
