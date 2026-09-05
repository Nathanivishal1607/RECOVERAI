"""Deterministic policy evaluation (``docs/decision-engine/policy-engine.md``).

Pure function of (candidate action, context, ``Policy`` row). No ML, no
randomness, no imports from ``backend.decision_engine`` / ``ml``. Hard
binary checks only — a high predicted value can never buy past a rule.

``NO_ACTION`` is unconditionally ALLOWED — this is what guarantees the
decision engine's veto loop always terminates.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.models import enums
from backend.models.governance import Policy

# machine-readable reason codes (docs/decision-engine/policy-engine.md)
RC_CHANNEL_DISABLED = "CHANNEL_DISABLED"
RC_MAX_RETRY_LIMIT = "MAX_RETRY_LIMIT"
RC_MAX_CONTACTS = "MAX_CONTACTS"
RC_AMOUNT_LIMIT = "AMOUNT_LIMIT"
RC_MIN_AMOUNT = "MIN_AMOUNT"
RC_RISK_FLAG = "RISK_FLAG"
RC_ALLOWED = "ALLOWED"


@dataclass(frozen=True)
class PolicyContext:
    """Everything the engine needs about the case, gathered by the caller."""

    retry_attempts_so_far: int = 0
    contacts_in_window: int = 0
    amount_at_risk: float = 0.0
    has_risk_flag: bool = False


@dataclass(frozen=True)
class PolicyDecision:
    action: str
    result: str  # enums.PolicyResult
    reason_code: str
    reason: str

    @property
    def allowed(self) -> bool:
        return self.result == enums.PolicyResult.ALLOWED.value


def _blocked(action: str, code: str, reason: str) -> PolicyDecision:
    return PolicyDecision(action, enums.PolicyResult.BLOCKED.value, code, reason)


def _allowed(action: str, reason: str = "all checks passed") -> PolicyDecision:
    return PolicyDecision(action, enums.PolicyResult.ALLOWED.value, RC_ALLOWED, reason)


def check_policy(
    action: str, policy: Policy, ctx: PolicyContext
) -> PolicyDecision:
    """Evaluate one candidate action against one merchant ``Policy`` version."""
    if action == enums.Action.NO_ACTION.value:
        return _allowed(action, "NO_ACTION is always allowed")

    allowed_actions = set(policy.allowed_interventions or [])
    if action not in allowed_actions:
        return _blocked(
            action, RC_CHANNEL_DISABLED,
            f"{action} is not enabled for this merchant",
        )

    if action == enums.Action.RETRY.value:
        # attempt about to be made would be (so_far + 1); cap at max_retry_count
        if ctx.retry_attempts_so_far >= (policy.max_retry_count or 0):
            return _blocked(
                action, RC_MAX_RETRY_LIMIT,
                f"max retry count reached ({policy.max_retry_count})",
            )

    # a MESSAGE (or any non-RETRY intervention) is a customer contact
    if action == enums.Action.MESSAGE.value:
        if ctx.contacts_in_window >= (policy.max_customer_contacts or 0):
            return _blocked(
                action, RC_MAX_CONTACTS,
                f"max customer contacts reached "
                f"({policy.max_customer_contacts} / {policy.contact_window_days}d)",
            )

    if policy.minimum_amount is not None and ctx.amount_at_risk < float(
        policy.minimum_amount
    ):
        return _blocked(
            action, RC_MIN_AMOUNT,
            f"amount {ctx.amount_at_risk} below policy minimum "
            f"{policy.minimum_amount}",
        )

    if policy.max_autonomous_amount is not None and ctx.amount_at_risk > float(
        policy.max_autonomous_amount
    ):
        return _blocked(
            action, RC_AMOUNT_LIMIT,
            f"amount {ctx.amount_at_risk} exceeds autonomous-action limit "
            f"{policy.max_autonomous_amount} — escalate",
        )

    if ctx.has_risk_flag:
        return _blocked(
            action, RC_RISK_FLAG,
            "risk flag present — escalate, no autonomous action",
        )

    return _allowed(action)
