"""Phase 2 — hidden ground truth: all three actions modelled, different
scenarios pick different best actions, and none of it leaks into the
observable feature snapshot or the application database."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from backend.models import Base, Prediction
from simulation.config import SimConfig
from simulation.features import (
    FEATURE_SCHEMA_ID,
    _LEAKAGE_TOKENS,
    assert_no_leakage,
    build_feature_snapshot,
)
from simulation.generator.entities import EPOCH, CustomerSpec, MerchantSpec, PaymentSpec
from simulation.ground_truth.potential_outcomes import (
    generate_potential_outcomes,
    oracle_best_action,
)
from simulation.taxonomy import CATEGORY_CODE
from simulation.taxonomy import FailureCategory as FC

_ACTIONS = ("RETRY", "MESSAGE", "NO_ACTION")


def _merchant(avg=1000.0, rec=0.4) -> MerchantSpec:
    return MerchantSpec(
        index=0, display_name="Sim M", segment="ecommerce",
        historical_recovery_rate=rec, avg_txn_amount=avg, monthly_volume=10_000,
    )


def _customer(reliability: float, prev_recovery=0.4) -> CustomerSpec:
    return CustomerSpec(
        customer_id="C-00-00001", merchant_index=0, segment="regular",
        tenure_days=300, payment_frequency_per_month=4.0,
        hist_success_rate=round(0.55 + 0.4 * reliability, 3),
        hist_failure_rate=round(1 - (0.55 + 0.4 * reliability), 3),
        prev_recovery_rate=prev_recovery, reliability=reliability,
    )


def _payment(category: str, amount: float, initial_attempts=0) -> PaymentSpec:
    return PaymentSpec(
        case_index=0, merchant_index=0, customer_id="C-00-00001", amount=amount,
        currency="INR", method="CARD", failure_category=category,
        failure_code=CATEGORY_CODE[category], created_at=EPOCH,
        failed_at=EPOCH + timedelta(minutes=5), initial_attempts=initial_attempts,
    )


def test_potential_outcomes_cover_all_three_actions():
    cfg = SimConfig(seed=42)
    po = generate_potential_outcomes(
        cfg=cfg, merchant=_merchant(), customer=_customer(0.5),
        payment=_payment(FC.TEMPORARY.value, 900.0), attempt_number=1,
    )
    assert set(po.p_by_action) == set(_ACTIONS)
    for a in _ACTIONS:
        assert 0.01 <= po.probability(a) <= 0.96


def test_different_scenarios_make_different_actions_optimal():
    cfg = SimConfig(seed=42)
    best_seen: set[str] = set()
    regimes_seen: set[str] = set()

    grid = [
        # (category, reliability, amount, attempts)
        (FC.TEMPORARY.value, 0.35, 1500.0, 1),        # steers RETRY
        (FC.TEMPORARY.value, 0.45, 2500.0, 2),
        (FC.CUSTOMER_ACTION_REQUIRED.value, 0.5, 1200.0, 1),  # steers MESSAGE
        (FC.PAYMENT_METHOD_ISSUE.value, 0.4, 1800.0, 1),
        (FC.TEMPORARY.value, 0.9, 300.0, 1),          # steers NO_ACTION
        (FC.LIMIT_EXCEEDED.value, 0.85, 250.0, 1),
    ]
    for cat, rel, amt, att in grid:
        po = generate_potential_outcomes(
            cfg=cfg, merchant=_merchant(avg=1000.0), customer=_customer(rel),
            payment=_payment(cat, amt, initial_attempts=att - 1), attempt_number=att,
        )
        best_seen.add(oracle_best_action(po, cfg=cfg))
        regimes_seen.add(po.regime)

    assert best_seen == {"RETRY", "MESSAGE", "NO_ACTION"}
    assert {"retry", "message", "no_action"} <= regimes_seen


def test_ground_truth_is_deterministic_for_same_inputs():
    cfg = SimConfig(seed=42)
    kw = dict(
        cfg=cfg, merchant=_merchant(), customer=_customer(0.6),
        payment=_payment(FC.UNKNOWN.value, 1100.0), attempt_number=2,
    )
    a = generate_potential_outcomes(**kw)
    b = generate_potential_outcomes(**kw)
    assert a.p_by_action == b.p_by_action
    assert a.regime == b.regime


def test_assert_no_leakage_rejects_hidden_keys():
    for tok in _LEAKAGE_TOKENS:
        with pytest.raises(ValueError):
            assert_no_leakage({f"x_{tok}_x": 1})
    # a clean snapshot passes
    assert_no_leakage({"amount": 10, "cust_segment": "regular"})


def test_feature_snapshot_contains_only_decision_time_info():
    snap = build_feature_snapshot(
        merchant=_merchant(), customer=_customer(0.9), payment=_payment(FC.TEMPORARY.value, 900.0),
        decision_time=EPOCH + timedelta(hours=2), attempt_number=1,
        last_attempt_time=EPOCH + timedelta(minutes=5),
    )
    assert snap["_feature_schema_id"] == FEATURE_SCHEMA_ID
    banned = {"reliability", "regime", "p_by_action", "p_retry", "p_message",
              "p_no_action", "oracle_best_action", "realised_recovered"}
    assert banned.isdisjoint(snap)
    # the latent reliability must never appear as a value-carrying feature
    assert "reliability" not in " ".join(map(str, snap)).lower()


def test_persisted_prediction_snapshots_have_no_hidden_data(sim_run):
    db = sim_run["db"]
    n = 0
    for pred in db.scalars(select(Prediction)):
        n += 1
        snap = pred.feature_snapshot
        assert snap["_feature_schema_id"] == FEATURE_SCHEMA_ID
        assert_no_leakage(snap)  # raises if a leak token sneaks in
        assert "regime" not in snap and "p_by_action" not in snap
    assert n > 0


def test_ground_truth_not_persisted_in_any_table():
    names = " ".join(Base.metadata.tables).lower()
    for tok in ("ground_truth", "groundtruth", "potential_outcome", "counterfactual",
                "oracle"):
        assert tok not in names
    # and no column anywhere is named after the hidden probabilities
    for table in Base.metadata.tables.values():
        cols = {c.name.lower() for c in table.columns}
        assert {"regime", "p_by_action", "p_retry", "reliability"}.isdisjoint(cols)


def test_ground_truth_sidecar_lives_outside_the_database(sim_run):
    gt_path = sim_run["result"].ground_truth_path
    assert gt_path.endswith(".json")
    assert "ground_truth" in gt_path.replace("\\", "/")
    store = sim_run["gt"]
    assert len(store) == sim_run["result"].cases_created
    # every case carries hidden per-action probabilities for all 3 actions
    for gt in store._by_case.values():
        assert gt.oracle_best_action in _ACTIONS
        for cyc in gt.cycles:
            assert set(cyc.p_by_action) == set(_ACTIONS)
