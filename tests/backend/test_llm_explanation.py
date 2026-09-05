"""Phase 12A-12C — NVIDIA NIM explanation-layer tests.

Uses a fake ``LLMProvider`` — no real NVIDIA API key required anywhere in
this file. Focuses on what this phase actually adds: provider
configuration, structured-context construction, ground-truth exclusion,
and safe-failure behavior. That the existing decision path (T-Learner
promotion, EIRV, policy) is unchanged is proven by the rest of the suite
running unmodified alongside this file.

An opt-in REAL smoke test against the live NVIDIA API is gated behind
``RUN_NVIDIA_NIM_LIVE_SMOKE_TEST=1`` (skipped by default; never required).
"""

from __future__ import annotations

import inspect
import json
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from backend.integrations import llm_provider as llm_provider_module
from backend.integrations.llm_provider import LLMProviderError, NvidiaNimProvider
from backend.schemas.audit import ActionConsideration, DecisionAuditRead, ModelVersionRef
from backend.schemas.dashboard import RecoveryCaseDetailRead
from backend.services import explanation as explanation_service

_FORBIDDEN_TOKENS = (
    "reliability", "p_by_action", "regime", "oracle", "potential",
    "true_", "p_retry", "p_message", "ground_truth",
)


class _FakeProvider:
    def __init__(self, response: str | None = None, raise_error: bool = False):
        self.response = response
        self.raise_error = raise_error
        self.calls: list[tuple[str, str]] = []

    def complete(self, *, system_prompt: str, user_prompt: str) -> str:
        self.calls.append((system_prompt, user_prompt))
        if self.raise_error:
            raise LLMProviderError("simulated provider failure")
        assert self.response is not None
        return self.response


def _sample_case_and_cycle() -> tuple[RecoveryCaseDetailRead, DecisionAuditRead]:
    now = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)
    case_id = uuid.uuid4()
    dr_id = uuid.uuid4()
    mv = ModelVersionRef(
        id=uuid.uuid4(), model_role="recovery_prediction",
        model_name="recovery-t-learner-logreg", version="seed-42",
        algorithm="logistic_regression_per_action", status="PROMOTED",
        feature_schema_id="sim-feature-schema-v1", training_dataset_snapshot_id="tds-1",
    )
    actions = [
        ActionConsideration(
            action="RETRY", recovery_probability=0.72, incremental_probability=0.34,
            eirv_value=184.0, cost_used=2.0, policy_result="ALLOWED",
            policy_reason_code="ALLOWED", is_recommended=True, is_final=True,
        ),
        ActionConsideration(
            action="MESSAGE", recovery_probability=0.61, incremental_probability=0.23,
            eirv_value=121.0, cost_used=3.0, policy_result=None,
            policy_reason_code=None, is_recommended=False, is_final=False,
        ),
        ActionConsideration(
            action="NO_ACTION", recovery_probability=0.38, incremental_probability=0.0,
            eirv_value=0.0, cost_used=0.0, policy_result=None,
            policy_reason_code=None, is_recommended=False, is_final=False,
        ),
    ]
    cycle = DecisionAuditRead(
        decision_record_id=dr_id, recovery_case_id=case_id, cycle_number=1,
        decision_timestamp=now, payment_amount_at_decision=Decimal("2500.00"),
        status="DECIDED", actions_considered=actions, recommended_action="RETRY",
        final_action="RETRY", was_blocked=False, block_reason_codes=[],
        decision_reason="RETRY had the highest EIRV and was allowed by policy",
        policy_id="POL-1", policy_version="v1", decision_engine_version="phase3-baseline-v1",
        intervention_action="RETRY", intervention_channel=None, execution_status="ACCEPTED",
        intervention_cost=Decimal("2.00"), outcome_result="RECOVERED",
        outcome_recovery_amount=Decimal("2500.00"), outcome_observed_at=now,
        model_version=mv, previous_cycles=[],
    )
    case = RecoveryCaseDetailRead(
        recovery_case_id=case_id, case_display_id="RC-00001", payment_id=uuid.uuid4(),
        merchant_id=uuid.uuid4(), status="RECOVERED", amount_at_risk=Decimal("2500.00"),
        failure_category="TEMPORARY", opened_at=now, closed_at=now, cycles=[cycle],
        payment=None, payment_events=[], experiment_assignment=None,
    )
    return case, cycle


# ------------------------------------------------------------- provider config


def test_provider_reads_config_only_from_settings(monkeypatch):
    monkeypatch.setattr("backend.core.config.settings.nvidia_nim_api_key", "test-key-123")
    monkeypatch.setattr("backend.core.config.settings.nvidia_nim_model", "nvidia/test-model")
    monkeypatch.setattr(
        "backend.core.config.settings.nvidia_nim_base_url", "https://example.invalid/v1"
    )
    provider = NvidiaNimProvider()
    assert provider.api_key == "test-key-123"
    assert provider.model == "nvidia/test-model"
    assert provider.base_url == "https://example.invalid/v1"


def test_provider_raises_clear_error_without_api_key():
    provider = NvidiaNimProvider(api_key="")
    with pytest.raises(LLMProviderError, match="NVIDIA_NIM_API_KEY"):
        provider.complete(system_prompt="s", user_prompt="u")


def test_no_secret_hardcoded_in_provider_source():
    """The provider module must contain no literal key material — the key
    only ever comes from ``backend.core.config.settings``."""
    src = inspect.getsource(llm_provider_module)
    assert "nvapi-" not in src
    assert "sk-" not in src
    assert "settings.nvidia_nim_api_key" in src  # reads from config, not a literal


def test_provider_network_failure_raises_llm_provider_error(monkeypatch):
    """A real network/timeout error from httpx is wrapped, never leaked
    as a raw exception the caller has to specifically know about."""
    import httpx

    def _boom(*args, **kwargs):
        raise httpx.ConnectTimeout("simulated timeout")

    monkeypatch.setattr(llm_provider_module.httpx, "post", _boom)
    provider = NvidiaNimProvider(api_key="test-key")
    with pytest.raises(LLMProviderError):
        provider.complete(system_prompt="s", user_prompt="u")


# --------------------------------------------------------- context construction


def test_context_contains_authoritative_decision_data():
    case, cycle = _sample_case_and_cycle()
    ctx = explanation_service.build_decision_context(case, cycle)

    assert ctx["case_display_id"] == "RC-00001"
    assert ctx["failure_category"] == "TEMPORARY"
    assert ctx["payment_amount"] == "2500.00"
    assert ctx["recommended_action"] == "RETRY"
    assert ctx["final_action"] == "RETRY"
    assert ctx["was_blocked"] is False
    assert ctx["outcome_result"] == "RECOVERED"
    assert ctx["model_name"] == "recovery-t-learner-logreg"
    assert ctx["model_status"] == "PROMOTED"

    by_action = {a["action"]: a for a in ctx["actions_considered"]}
    assert set(by_action) == {"RETRY", "MESSAGE", "NO_ACTION"}
    assert by_action["RETRY"]["recovery_probability"] == 0.72
    assert by_action["RETRY"]["eirv_value"] == 184.0
    assert by_action["NO_ACTION"]["eirv_value"] == 0.0


def test_context_excludes_ground_truth_and_hidden_fields():
    case, cycle = _sample_case_and_cycle()
    ctx = explanation_service.build_decision_context(case, cycle)
    blob = json.dumps(ctx).lower()
    for token in _FORBIDDEN_TOKENS:
        assert token not in blob, f"forbidden token {token!r} leaked into LLM context"
    # no raw feature_snapshot passed either — narrower than what's persisted
    assert "feature_snapshot" not in blob


# --------------------------------------------------------------- explain_decision


def test_explain_decision_success_path():
    case, cycle = _sample_case_and_cycle()
    fake_response = json.dumps({
        "summary": "RETRY was recommended and recovered the payment.",
        "model_reasoning": "The T-Learner predicted a 72% recovery probability for RETRY.",
        "value_reasoning": "RETRY had the highest EIRV at +184, so it was economically preferred.",
        "policy_reasoning": "Policy allowed RETRY — no limits were exceeded.",
        "final_action_reasoning": "RETRY was executed and the payment recovered.",
        "disclaimer": "AI-generated explanation, not a decision.",
    })
    provider = _FakeProvider(response=fake_response)
    result = explanation_service.explain_decision(case, cycle, provider=provider)

    assert result.available is True
    assert "RETRY" in result.summary
    assert len(provider.calls) == 1
    system_prompt, user_prompt = provider.calls[0]
    assert "does not" in system_prompt.lower() or "not you" in system_prompt.lower()
    sent = json.loads(user_prompt)
    assert sent["final_action"] == "RETRY"
    for token in _FORBIDDEN_TOKENS:
        assert token not in user_prompt.lower()


def test_explain_decision_provider_failure_returns_safe_fallback():
    case, cycle = _sample_case_and_cycle()
    provider = _FakeProvider(raise_error=True)
    result = explanation_service.explain_decision(case, cycle, provider=provider)

    assert result.available is False
    assert "unavailable" in result.summary.lower()
    # the decision-relevant fields were never touched — this object carries
    # no financial data at all when unavailable
    assert result.disclaimer  # non-empty, explains what happened


def test_explain_decision_malformed_response_returns_safe_fallback():
    case, cycle = _sample_case_and_cycle()
    provider = _FakeProvider(response="not valid json at all")
    result = explanation_service.explain_decision(case, cycle, provider=provider)
    assert result.available is False


def test_explain_decision_missing_key_returns_safe_fallback():
    case, cycle = _sample_case_and_cycle()
    # valid JSON but missing a required key
    provider = _FakeProvider(response=json.dumps({"summary": "ok"}))
    result = explanation_service.explain_decision(case, cycle, provider=provider)
    assert result.available is False


def test_explain_decision_disabled_flag_skips_provider_entirely(monkeypatch):
    monkeypatch.setattr(
        "backend.services.explanation.settings.enable_llm_explanations", False
    )
    case, cycle = _sample_case_and_cycle()
    provider = _FakeProvider(response="{}")
    result = explanation_service.explain_decision(case, cycle, provider=provider)
    assert result.available is False
    assert provider.calls == []  # never even called


def test_explanation_never_claims_to_compute_eirv_or_probability():
    """Static guard: the system prompt must instruct the LLM it is not
    the decision maker, matching the architecture boundary — including
    the Phase 12B-added boundaries (execution, outcome, chain-of-thought)."""
    prompt = explanation_service._SYSTEM_PROMPT.lower()
    assert "did not make this decision" in prompt
    assert "calculated a recovery probability" in prompt
    assert "calculated eirv" in prompt
    assert "authorized an action" in prompt
    assert "executed an intervention" in prompt
    assert "observed an outcome independently" in prompt
    assert "chain-of-thought" in prompt


# --------------------------------------------------- opt-in real API smoke test


@pytest.mark.skipif(
    os.environ.get("RUN_NVIDIA_NIM_LIVE_SMOKE_TEST") != "1",
    reason="opt-in only — set RUN_NVIDIA_NIM_LIVE_SMOKE_TEST=1 and a real NVIDIA_NIM_API_KEY",
)
def test_live_nvidia_nim_smoke():  # pragma: no cover - opt-in, network
    """Real request against the configured NVIDIA_NIM_MODEL (default
    openai/gpt-oss-20b as of Phase 12C — see docs/architecture/
    decision-flow.md for why it was selected over Kimi K3)."""
    case, cycle = _sample_case_and_cycle()
    result = explanation_service.explain_decision(case, cycle)
    assert result.available is True
    assert result.summary and result.summary != "LLM explanation unavailable."
