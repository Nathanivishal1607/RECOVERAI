"""Action selection (``docs/decision-engine/action-selection.md``).

Rank candidate actions best-first by EIRV. ``NO_ACTION`` (EIRV = 0)
naturally outranks any negative-EIRV action — no special-casing. The
returned list always contains ``NO_ACTION`` so the policy veto loop always
has a guaranteed-passing fallback.
"""

from __future__ import annotations

from backend.models import enums

NO_ACTION = enums.Action.NO_ACTION.value


def rank_actions(
    eirv_by_action: dict[str, float],
    *,
    min_eirv_threshold: float = 0.0,
) -> list[str]:
    """Actions best-first. Anything with EIRV below the threshold is
    dropped, but ``NO_ACTION`` is always retained as the fallback."""
    ranked = sorted(eirv_by_action.items(), key=lambda kv: kv[1], reverse=True)
    kept = [
        action
        for action, value in ranked
        if value >= min_eirv_threshold or action == NO_ACTION
    ]
    if NO_ACTION not in kept:
        kept.append(NO_ACTION)
    return kept
