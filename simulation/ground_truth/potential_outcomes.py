"""Hidden potential-outcome generator.

Deterministic, interpretable simulator RULES (no ML) that turn
payment/customer/merchant characteristics into an action-specific
recovery probability for **all three** MVP actions:

    P(recovery | features, RETRY)
    P(recovery | features, MESSAGE)
    P(recovery | features, NO_ACTION)

These probabilities are SIMULATOR GROUND TRUTH. They are never given to
the model / decision / feature pipeline — only the evaluation layer may
read them. No claim is made that these match real Razorpay recovery.

Design guarantees:
  * different scenarios make different actions optimal (RETRY / MESSAGE /
    NO_ACTION each win a meaningful share) — see ``regime`` below;
  * realistic per-case noise;
  * probabilities clamped to (0.01, 0.96).
"""

from __future__ import annotations

from dataclasses import dataclass

from simulation.config import SimConfig
from simulation.generator.entities import CustomerSpec, MerchantSpec, PaymentSpec
from simulation.rng import stream
from simulation.taxonomy import FailureCategory as FC

# Per-category base recovery probability for each action.
_BASE: dict[str, dict[str, float]] = {
    FC.TEMPORARY.value: {"RETRY": 0.64, "MESSAGE": 0.40, "NO_ACTION": 0.24},
    FC.CUSTOMER_ACTION_REQUIRED.value: {"RETRY": 0.17, "MESSAGE": 0.60, "NO_ACTION": 0.11},
    FC.PAYMENT_METHOD_ISSUE.value: {"RETRY": 0.22, "MESSAGE": 0.52, "NO_ACTION": 0.09},
    FC.LIMIT_EXCEEDED.value: {"RETRY": 0.33, "MESSAGE": 0.36, "NO_ACTION": 0.21},
    FC.UNKNOWN.value: {"RETRY": 0.27, "MESSAGE": 0.30, "NO_ACTION": 0.15},
}

_ACTIONS = ("RETRY", "MESSAGE", "NO_ACTION")


@dataclass(frozen=True)
class PotentialOutcomes:
    """Hidden truth for ONE recovery opportunity (one payment)."""

    case_index: int
    p_by_action: dict[str, float]           # P(recovery | action)
    regime: str                             # which action the design steers toward
    amount: float

    def probability(self, action: str) -> float:
        return self.p_by_action[action]

    def as_public_safe_dict(self) -> dict:
        """Explicitly NOT for the model pipeline — evaluation/debug only."""
        return {"case_index": self.case_index, "regime": self.regime,
                "p_by_action": self.p_by_action}


def _clamp(x: float) -> float:
    return max(0.01, min(0.96, x))


def generate_potential_outcomes(
    *,
    cfg: SimConfig,
    merchant: MerchantSpec,
    customer: CustomerSpec,
    payment: PaymentSpec,
    attempt_number: int,
) -> PotentialOutcomes:
    r = stream(cfg.seed, "ground_truth", payment.case_index, attempt_number)
    base = dict(_BASE[payment.failure_category])

    rel = customer.reliability
    # amount friction: higher amounts are a little harder, MESSAGE least affected
    amt_norm = min(1.0, payment.amount / max(1.0, merchant.avg_txn_amount * 3))
    # each prior failed attempt erodes RETRY effectiveness the most
    attempt_penalty = 0.10 * max(0, attempt_number - 1)

    p = {
        "RETRY": base["RETRY"]
        + 0.12 * (rel - 0.5)
        - 0.14 * amt_norm
        - attempt_penalty
        + 0.15 * (merchant.historical_recovery_rate - 0.4),
        "MESSAGE": base["MESSAGE"]
        + 0.16 * (rel - 0.5)
        - 0.05 * amt_norm
        - 0.4 * attempt_penalty
        + 0.10 * (customer.prev_recovery_rate - 0.4),
        "NO_ACTION": base["NO_ACTION"]
        + 0.30 * (rel - 0.5)           # reliable customers self-recover
        - 0.10 * amt_norm,
    }

    # --- regimes that guarantee each action can strictly win on EIRV ---
    regime = "mixed"
    self_recovering = (
        payment.failure_category in (FC.TEMPORARY.value, FC.LIMIT_EXCEEDED.value)
        and rel > 0.70
        and payment.amount < merchant.avg_txn_amount * 1.5
    )
    if self_recovering:
        regime = "no_action"
        # this customer will very likely pay on their own within the window
        p["NO_ACTION"] = _clamp(0.72 + 0.18 * (rel - 0.70) + r.uniform(-0.03, 0.04))
        # intervening barely helps and adds friction -> strictly worse than NO_ACTION
        p["RETRY"] = _clamp(p["NO_ACTION"] - r.uniform(0.01, 0.09))
        p["MESSAGE"] = _clamp(p["NO_ACTION"] - r.uniform(0.02, 0.11))
    elif payment.failure_category == FC.TEMPORARY.value and rel <= 0.6:
        regime = "retry"
        p["RETRY"] += 0.10
        p["NO_ACTION"] -= 0.06
    elif payment.failure_category in (
        FC.CUSTOMER_ACTION_REQUIRED.value,
        FC.PAYMENT_METHOD_ISSUE.value,
    ):
        regime = "message"
        p["MESSAGE"] += 0.06
        p["RETRY"] -= 0.04

    # realistic per-action noise
    for a in _ACTIONS:
        p[a] = _clamp(p[a] + r.gauss(0, cfg.ground_truth_noise_sd))

    return PotentialOutcomes(
        case_index=payment.case_index,
        p_by_action={a: round(p[a], 4) for a in _ACTIONS},
        regime=regime,
        amount=payment.amount,
    )


def eirv(po: PotentialOutcomes, action: str, *, cfg: SimConfig) -> float:
    """Oracle EIRV for an action under hidden truth (evaluation only)."""
    if action == "NO_ACTION":
        return 0.0
    incremental = po.probability(action) - po.probability("NO_ACTION")
    return incremental * po.amount - cfg.cost_for(action)


def oracle_best_action(po: PotentialOutcomes, *, cfg: SimConfig) -> str:
    """The EIRV-maximising action under hidden truth (NO_ACTION = 0)."""
    scored = {a: eirv(po, a, cfg=cfg) for a in _ACTIONS}
    return max(scored, key=scored.get)
