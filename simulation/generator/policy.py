"""Placeholder data-generating policies (NOT the real decision engine).

Phase 2 has no trained model and no EIRV optimiser yet, so the simulator
needs *some* deterministic rule to pick the observed action per cycle.
These rules use ONLY observable features (failure category, amount, cycle
number) — never the hidden potential outcomes — so the generated
training/experiment data has no leakage.

* ``control_policy``   — the baseline strategy ("retry once, then stop").
* ``heuristic_policy`` — a naive observable heuristic standing in for the
  system-under-test until Phase 5 replaces it.

Both return one of ``RETRY`` / ``MESSAGE`` / ``NO_ACTION``, or ``None`` to
mean "stop, no further cycle".
"""

from __future__ import annotations

from simulation.taxonomy import FailureCategory as FC

_EXECUTABLE = ("RETRY", "MESSAGE")


def control_policy(*, cycle_number: int, prior_actions: list[str], **_ignored) -> str | None:
    if cycle_number == 1:
        return "RETRY"
    return None  # baseline: one retry, then stop


def heuristic_policy(
    *,
    cycle_number: int,
    prior_actions: list[str],
    failure_category: str,
    amount: float,
    small_amount_threshold: float = 250.0,
    max_cycles: int = 3,
) -> str | None:
    if cycle_number > max_cycles:
        return None

    if amount < small_amount_threshold and cycle_number >= 2:
        return "NO_ACTION"

    if failure_category == FC.TEMPORARY.value:
        first_choice = "RETRY"
    elif failure_category in (
        FC.CUSTOMER_ACTION_REQUIRED.value,
        FC.PAYMENT_METHOD_ISSUE.value,
        FC.LIMIT_EXCEEDED.value,
    ):
        first_choice = "MESSAGE"
    else:  # UNKNOWN
        first_choice = "MESSAGE" if cycle_number == 1 else "NO_ACTION"

    if cycle_number == 1:
        return first_choice

    # re-evaluation: try the other executable action once, then NO_ACTION
    tried_exec = [a for a in prior_actions if a in _EXECUTABLE]
    for alt in _EXECUTABLE:
        if alt not in tried_exec:
            return alt
    return "NO_ACTION"


POLICIES = {"control": control_policy, "heuristic": heuristic_policy}
