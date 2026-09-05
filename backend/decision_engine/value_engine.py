"""EIRV — Expected Incremental Recovery Value
(``docs/decision-engine/value-calculation.md``, ADR-003).

    EIRV(a) = (P(recover|a) - P(recover|NO_ACTION)) * amount - cost(a)

``NO_ACTION`` is the reference point: EIRV(NO_ACTION) == 0 by definition
(not computed via the formula). Pure arithmetic — no ML here.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.models import enums

#: MVP placeholder cost model — simulation parameters, NOT Razorpay pricing.
#: (Mirrors ``simulation.config.SimConfig`` defaults so the baseline engine
#: and the simulator agree.)
DEFAULT_COSTS: dict[str, float] = {
    enums.Action.RETRY.value: 2.0,
    enums.Action.MESSAGE.value: 3.0,
    enums.Action.NO_ACTION.value: 0.0,
}


@dataclass(frozen=True)
class EIRVInputs:
    baseline_probability: float  # P(recover | NO_ACTION)
    action_probability: float  # P(recover | action)
    amount: float
    action_cost: float


def compute_eirv(inp: EIRVInputs) -> float:
    incremental = inp.action_probability - inp.baseline_probability
    return incremental * inp.amount - inp.action_cost


def eirv_by_action(
    probabilities: dict[str, float],
    amount: float,
    costs: dict[str, float] | None = None,
) -> dict[str, float]:
    """EIRV for every candidate action. ``NO_ACTION`` is pinned to 0.0."""
    costs = costs or DEFAULT_COSTS
    baseline = probabilities[enums.Action.NO_ACTION.value]
    out: dict[str, float] = {}
    for action, p in probabilities.items():
        if action == enums.Action.NO_ACTION.value:
            out[action] = 0.0
            continue
        out[action] = compute_eirv(
            EIRVInputs(
                baseline_probability=baseline,
                action_probability=p,
                amount=amount,
                action_cost=costs.get(action, 0.0),
            )
        )
    return out
