"""The decision engine — turns per-action model ``Prediction``s into one
final, policy-allowed action, and writes the ``DecisionRecord``.

Responsibilities: EIRV (value-calculation), ranking (action-selection),
the policy veto loop, and DecisionRecord assembly. It calls ``ml`` only
via ``ml.inference`` and calls ``backend.policies``; it never talks to a
provider and never invokes the LLM.

The ML model predicts probabilities. This engine owns EIRV, the
recommendation, and the final action; the Policy Engine keeps its
unconditional veto.
"""

from backend.decision_engine.value_engine import EIRVInputs, compute_eirv
from backend.decision_engine.optimizer import rank_actions
from backend.decision_engine.orchestrator import (
    DecisionEngine,
    DecisionEngineConfig,
    DecisionOutcome,
)

__all__ = [
    "EIRVInputs",
    "compute_eirv",
    "rank_actions",
    "DecisionEngine",
    "DecisionEngineConfig",
    "DecisionOutcome",
]
