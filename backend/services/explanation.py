"""Phase 12A-12C — decision explanation service (NVIDIA NIM).

Explains an ALREADY-COMPUTED decision cycle in natural language:

    DecisionAuditRead (authoritative, already persisted)
        -> build_decision_context()   (structured, privacy-filtered)
        -> NvidiaNimProvider.complete()   (default: openai/gpt-oss-20b)
        -> parsed + validated DecisionExplanation

The LLM never influences ``recovery_probability`` (T-Learner), ``eirv_value``
(decision engine), ``policy_result`` (policy engine), or ``final_action`` —
all of those are already fixed, persisted, and immutable by the time this
runs. This module writes nothing back to ``DecisionRecord`` / ``Outcome`` /
``TrainingExample`` — it only reads and returns text.

Fails soft everywhere: disabled flag, missing API key, network error,
timeout, or a malformed/unparseable LLM response all fall through to the
same safe ``available=False`` explanation. The recovery-decision API
(``/payments/{id}/evaluate`` and friends) never depends on this module and
keeps working with zero LLM availability.
"""

from __future__ import annotations

import json
import logging

from backend.core.config import settings
from backend.integrations.llm_provider import LLMProvider, LLMProviderError, NvidiaNimProvider
from backend.schemas.audit import DecisionAuditRead
from backend.schemas.dashboard import RecoveryCaseDetailRead
from backend.schemas.explanation import DecisionExplanation

logger = logging.getLogger(__name__)

_DEFAULT_DISCLAIMER = (
    "AI-generated explanation of a decision already made by the T-Learner "
    "model, the deterministic decision engine, and the policy engine. It "
    "does not itself authorize or change any action."
)

_UNAVAILABLE_TEXT = "LLM explanation unavailable."

_UNAVAILABLE = DecisionExplanation(
    summary=_UNAVAILABLE_TEXT,
    model_reasoning=_UNAVAILABLE_TEXT,
    value_reasoning=_UNAVAILABLE_TEXT,
    policy_reasoning=_UNAVAILABLE_TEXT,
    final_action_reasoning=_UNAVAILABLE_TEXT,
    disclaimer=(
        "This explanation could not be generated. The recovery decision "
        "itself (T-Learner probability, EIRV, policy check, final action) "
        "is unaffected — it was already computed and persisted before this "
        "explanation was requested."
    ),
    available=False,
)

_SYSTEM_PROMPT = (
    "You are an explanation assistant for RecoverAI, a payment-recovery "
    "system. You explain an ALREADY-DECIDED action to a human reader. You "
    "did NOT make this decision and must not claim you did. "
    "RecoverAI's T-Learner (a separate machine-learning model) predicted "
    "the recovery probability for each candidate action. RecoverAI's "
    "Decision Engine (separate, deterministic — not you) computed EIRV "
    "(expected incremental recovered value) from those probabilities and "
    "picked the economically preferred recommendation. RecoverAI's Policy "
    "Engine (separate — not you) then determined whether that "
    "recommendation was allowed, producing the final action. Execution "
    "(not you) determined whether an intervention actually ran. Outcome "
    "(not you) determined whether recovery actually happened. "
    "You must NEVER claim to have calculated a recovery probability, "
    "calculated EIRV, authorized an action, executed an intervention, or "
    "observed an outcome independently — you only narrate the authoritative "
    "values given to you below. Never invent a number. Never invent a "
    "policy rule. Never invent an outcome. Never suggest a different "
    "action than final_action. Do not include any reasoning process, "
    "chain-of-thought, or step-by-step deliberation — output only the "
    "final answer. Respond with ONLY a JSON object with exactly these "
    "keys: summary, model_reasoning, value_reasoning, policy_reasoning, "
    "final_action_reasoning, disclaimer. Each value must be 1-3 plain "
    "English sentences. No markdown, no extra keys, no text outside the "
    "JSON object."
)


def build_decision_context(case: RecoveryCaseDetailRead, cycle: DecisionAuditRead) -> dict:
    """Structured, privacy-filtered decision context for the LLM.

    Built ONLY from authoritative, already-persisted, already
    privacy-filtered fields (``RecoveryCaseDetailRead`` / ``DecisionAuditRead``
    — the same read model the frontend renders). Never includes raw
    ``feature_snapshot``, simulator ground truth, customer PII, or any
    hidden field — those are excluded by construction, since these read
    schemas never carry them in the first place (see
    tests/ml/test_leakage_and_dependency.py and
    tests/backend/test_llm_explanation.py::test_context_excludes_ground_truth).
    """
    return {
        "case_display_id": case.case_display_id,
        "failure_category": case.failure_category,
        "payment_amount": str(case.amount_at_risk),
        "cycle_number": cycle.cycle_number,
        "actions_considered": [
            {
                "action": a.action,
                "recovery_probability": a.recovery_probability,
                "eirv_value": a.eirv_value,
                "policy_result": a.policy_result,
                "policy_reason_code": a.policy_reason_code,
                "is_recommended": a.is_recommended,
                "is_final": a.is_final,
            }
            for a in cycle.actions_considered
        ],
        "recommended_action": cycle.recommended_action,
        "final_action": cycle.final_action,
        "was_blocked": cycle.was_blocked,
        "block_reason_codes": cycle.block_reason_codes,
        "execution_status": cycle.execution_status,
        "outcome_result": cycle.outcome_result,
        "outcome_recovery_amount": (
            str(cycle.outcome_recovery_amount)
            if cycle.outcome_recovery_amount is not None
            else None
        ),
        "model_name": cycle.model_version.model_name if cycle.model_version else None,
        "model_version": cycle.model_version.version if cycle.model_version else None,
        "model_status": cycle.model_version.status if cycle.model_version else None,
    }


def explain_decision(
    case: RecoveryCaseDetailRead,
    cycle: DecisionAuditRead,
    *,
    provider: LLMProvider | None = None,
) -> DecisionExplanation:
    if not settings.enable_llm_explanations:
        return _UNAVAILABLE

    context = build_decision_context(case, cycle)
    prov = provider or NvidiaNimProvider()
    try:
        raw = prov.complete(
            system_prompt=_SYSTEM_PROMPT, user_prompt=json.dumps(context, indent=2)
        )
    except LLMProviderError as exc:
        logger.warning("LLM explanation unavailable: %s", exc)
        return _UNAVAILABLE

    try:
        parsed = json.loads(raw)
        return DecisionExplanation(
            summary=str(parsed["summary"]),
            model_reasoning=str(parsed["model_reasoning"]),
            value_reasoning=str(parsed["value_reasoning"]),
            policy_reasoning=str(parsed["policy_reasoning"]),
            final_action_reasoning=str(parsed["final_action_reasoning"]),
            disclaimer=str(parsed.get("disclaimer") or _DEFAULT_DISCLAIMER),
            available=True,
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        logger.warning("LLM explanation response malformed, discarding: %s", exc)
        return _UNAVAILABLE
