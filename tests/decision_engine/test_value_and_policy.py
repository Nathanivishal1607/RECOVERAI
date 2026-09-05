"""Phase 3 — EIRV arithmetic, action ranking, policy veto (unit level)."""

from __future__ import annotations

from backend.decision_engine.optimizer import rank_actions
from backend.decision_engine.value_engine import (
    DEFAULT_COSTS,
    EIRVInputs,
    compute_eirv,
    eirv_by_action,
)
from backend.models import enums
from backend.models.governance import Policy
from backend.policies.engine import PolicyContext, check_policy


def test_eirv_formula_matches_doc_example():
    # docs/decision-engine/value-calculation.md §6
    v = compute_eirv(EIRVInputs(baseline_probability=0.28, action_probability=0.67,
                                amount=5000, action_cost=2))
    assert round(v, 2) == 1948.00


def test_no_action_eirv_is_zero_by_definition():
    probs = {"RETRY": 0.9, "MESSAGE": 0.8, "NO_ACTION": 0.5}
    e = eirv_by_action(probs, amount=1000.0)
    assert e["NO_ACTION"] == 0.0


def test_negative_eirv_action_ranks_below_no_action():
    # RETRY hurts recovery odds -> negative EIRV -> ranked below NO_ACTION
    probs = {"RETRY": 0.20, "MESSAGE": 0.19, "NO_ACTION": 0.30}
    e = eirv_by_action(probs, amount=1000.0, costs=DEFAULT_COSTS)
    ranked = rank_actions(e)
    assert ranked[0] == "NO_ACTION"
    assert set(ranked) >= {"NO_ACTION"}


def test_rank_always_contains_no_action_even_above_threshold():
    e = {"RETRY": 5000.0, "MESSAGE": 4000.0, "NO_ACTION": 0.0}
    ranked = rank_actions(e, min_eirv_threshold=100.0)
    assert ranked[0] == "RETRY"
    assert "NO_ACTION" in ranked  # fallback preserved


def _policy(**over) -> Policy:
    base = dict(
        policy_id="POL-1", policy_version="v1", merchant_id=None,
        max_retry_count=2, max_customer_contacts=2, contact_window_days=7,
        allowed_interventions=["RETRY", "MESSAGE"],
    )
    base.update(over)
    return Policy(**base)


def test_no_action_is_always_allowed():
    d = check_policy("NO_ACTION", _policy(), PolicyContext())
    assert d.allowed and d.reason_code == "ALLOWED"


def test_retry_blocked_at_max_retry_count():
    d = check_policy("RETRY", _policy(max_retry_count=2),
                     PolicyContext(retry_attempts_so_far=2))
    assert not d.allowed and d.reason_code == "MAX_RETRY_LIMIT"


def test_message_blocked_at_max_contacts():
    d = check_policy("MESSAGE", _policy(max_customer_contacts=1),
                     PolicyContext(contacts_in_window=1))
    assert not d.allowed and d.reason_code == "MAX_CONTACTS"


def test_channel_disabled():
    d = check_policy("MESSAGE", _policy(allowed_interventions=["RETRY"]),
                     PolicyContext())
    assert not d.allowed and d.reason_code == "CHANNEL_DISABLED"


def test_amount_limit_and_risk_flag():
    from decimal import Decimal
    d = check_policy("RETRY", _policy(max_autonomous_amount=Decimal("1000")),
                     PolicyContext(amount_at_risk=5000.0))
    assert not d.allowed and d.reason_code == "AMOUNT_LIMIT"

    d2 = check_policy("RETRY", _policy(), PolicyContext(has_risk_flag=True))
    assert not d2.allowed and d2.reason_code == "RISK_FLAG"
