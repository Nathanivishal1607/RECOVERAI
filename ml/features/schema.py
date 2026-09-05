"""Deterministic vectorization of a decision-time feature snapshot.

Input: the immutable ``feature_snapshot`` dict persisted on every
``Prediction`` / ``TrainingExample`` (schema ``sim-feature-schema-v1``,
built by ``simulation/features.py``), plus one candidate ``action``.

Output: a fixed-length numeric row. The column order is frozen here and
recorded on the ``ModelVersion`` (``feature_schema_id``) so a historical
model reproduces the exact same inputs.

No hidden simulator data is referenced. Unknown categorical values map to
an all-zero one-hot block (safe for inference on unseen categories); a
missing numeric key falls back to a documented neutral default.
"""

from __future__ import annotations

from typing import Iterable, Sequence

import numpy as np

#: Must match ``simulation/features.py::FEATURE_SCHEMA_ID``. The model is
#: only valid for snapshots carrying this id.
FEATURE_SCHEMA_ID = "sim-feature-schema-v1"

#: The three MVP candidate actions — the S-learner treatment feature.
ACTIONS: tuple[str, ...] = ("RETRY", "MESSAGE", "NO_ACTION")

#: Numeric features taken straight from the snapshot, with neutral fallbacks
#: for a missing key (kept explicit so the vector length never changes).
NUMERIC_FEATURES: dict[str, float] = {
    "cust_tenure_days": 180.0,
    "cust_hist_success_rate": 0.7,
    "cust_hist_failure_rate": 0.3,
    "cust_prev_recovery_rate": 0.4,
    "cust_payment_freq_per_month": 2.0,
    "amount": 1000.0,
    "attempt_number": 1.0,
    "minutes_since_last_attempt": 0.0,
    "hour_of_day": 12.0,
    "day_of_week": 3.0,
    "merchant_hist_recovery_rate": 0.4,
    "merchant_avg_txn_amount": 1500.0,
}

#: Categorical features -> the closed value set they are one-hot encoded
#: against. Values mirror the Phase 2 taxonomy / entity generators.
CATEGORICAL_FEATURES: dict[str, tuple[str, ...]] = {
    "cust_segment": ("new", "casual", "regular", "loyal"),
    "currency": ("INR",),
    "payment_method": ("UPI", "CARD", "NETBANKING", "WALLET"),
    "failure_category": (
        "TEMPORARY",
        "CUSTOMER_ACTION_REQUIRED",
        "PAYMENT_METHOD_ISSUE",
        "LIMIT_EXCEEDED",
        "UNKNOWN",
    ),
    "failure_code": (
        "SIM_GATEWAY_TIMEOUT",
        "SIM_AUTH_REQUIRED",
        "SIM_INSTRUMENT_DECLINED",
        "SIM_LIMIT_EXCEEDED",
        "SIM_UNKNOWN",
    ),
    "merchant_segment": ("saas_subscription", "ecommerce", "utility_bills", "edtech"),
}

#: Tokens that must never appear as a snapshot key — a defence-in-depth
#: copy of the simulator's leakage guard, enforced at vectorization time
#: so a leaked snapshot can never be trained/inferred on.
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


def feature_column_names() -> list[str]:
    """Frozen order of the *features only* block (numeric, then one-hot
    categorical blocks) — no action columns. Used by the T-learner /
    per-action models where ``action`` is not an input feature."""
    cols = list(NUMERIC_FEATURES)
    for feat, values in CATEGORICAL_FEATURES.items():
        cols += [f"{feat}={v}" for v in values]
    return cols


def column_names() -> list[str]:
    """The frozen output column order (numeric, then one-hot blocks, then
    the action one-hot). Recorded conceptually via ``feature_schema_id``."""
    return feature_column_names() + [f"action={a}" for a in ACTIONS]


def assert_snapshot_clean(snapshot: dict) -> None:
    """Raise if a snapshot key looks like hidden ground truth / a future
    label. Defence in depth over ``simulation.features.assert_no_leakage``."""
    for key in snapshot:
        low = str(key).lower()
        if any(tok in low for tok in _LEAKAGE_TOKENS):
            raise ValueError(f"feature snapshot leaks hidden data: {key!r}")


def _one_hot(value: object, values: Sequence[str]) -> list[float]:
    v = str(value)
    return [1.0 if v == opt else 0.0 for opt in values]


def vectorize_features(snapshot: dict) -> np.ndarray:
    """The *features only* row (no action) — for T-learner / per-action models."""
    assert_snapshot_clean(snapshot)
    row: list[float] = []
    for key, default in NUMERIC_FEATURES.items():
        raw = snapshot.get(key, default)
        try:
            row.append(float(raw))
        except (TypeError, ValueError):
            row.append(float(default))
    for feat, values in CATEGORICAL_FEATURES.items():
        row.extend(_one_hot(snapshot.get(feat), values))
    return np.asarray(row, dtype=np.float64)


def vectorize(snapshot: dict, action: str) -> np.ndarray:
    """One (features + candidate action) row, as a float64 vector."""
    if action not in ACTIONS:
        raise ValueError(f"unknown action {action!r}; expected one of {ACTIONS}")
    feats = vectorize_features(snapshot)
    return np.concatenate([feats, np.asarray(_one_hot(action, ACTIONS))])


def feature_matrix(
    snapshots: Iterable[dict], actions: Iterable[str]
) -> np.ndarray:
    """Stack ``vectorize`` over parallel snapshot/action iterables."""
    rows = [vectorize(s, a) for s, a in zip(snapshots, actions)]
    if not rows:
        return np.empty((0, len(column_names())), dtype=np.float64)
    return np.vstack(rows)


def features_only_matrix(snapshots: Iterable[dict]) -> np.ndarray:
    """Stack ``vectorize_features`` over a snapshot iterable (no action)."""
    rows = [vectorize_features(s) for s in snapshots]
    if not rows:
        return np.empty((0, len(feature_column_names())), dtype=np.float64)
    return np.vstack(rows)
