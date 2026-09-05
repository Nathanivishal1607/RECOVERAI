"""The deterministic Policy Engine — HOW merchant policy rules are
evaluated. Policy DATA (WHAT is allowed) lives in the ``policy`` table.

This package has an unconditional veto over every candidate action and
must never import from ``backend.decision_engine`` or ``ml`` (see
``docs/architecture/component-architecture.md``).
"""

from backend.policies.engine import PolicyContext, PolicyDecision, check_policy

__all__ = ["PolicyContext", "PolicyDecision", "check_policy"]
