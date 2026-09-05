"""Phase 12A — LLM decision-explanation contract.

A read-only, after-the-fact natural-language explanation of an ALREADY
COMPUTED decision cycle. See backend/services/explanation.py for how this
is assembled and why the LLM cannot influence the decision itself.
"""

from __future__ import annotations

from pydantic import BaseModel


class DecisionExplanation(BaseModel):
    summary: str
    model_reasoning: str
    value_reasoning: str
    policy_reasoning: str
    final_action_reasoning: str
    disclaimer: str
    #: False when the LLM was unavailable/disabled/malformed — the fields
    #: above then hold a safe fallback message, never invented content.
    available: bool = True
