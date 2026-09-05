"""The single decision-engine entry point for one evaluate->decide cycle.

    feature_snapshot
        -> model.predict_all_actions()      (ml.inference — probabilities only)
        -> Prediction(RETRY), Prediction(MESSAGE), Prediction(NO_ACTION)
        -> EIRV per action                  (value_engine)
        -> recommended_action = argmax EIRV  (optimizer)
        -> policy veto loop                  (backend.policies) -> final_action
        -> DecisionRecord (recommended + final stored separately, value_context,
                           one PolicyEvaluation per candidate checked)
        -> Intervention   ONLY if final_action in {RETRY, MESSAGE}

Everything is persisted through the existing Phase 1B repositories. The
engine does not move money, does not call a provider, does not call the
LLM. The Policy Engine has an unconditional veto; the loop always
terminates at NO_ACTION (which always passes policy).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from backend.database.base import utcnow
from backend.models import enums
from backend.models.core_entities import RecoveryCase
from backend.models.decision import DecisionRecord
from backend.models.governance import ModelVersion, Policy
from backend.repositories.decision import DecisionCycleRepository
from backend.decision_engine.optimizer import rank_actions
from backend.decision_engine.value_engine import DEFAULT_COSTS, eirv_by_action
from backend.policies.engine import PolicyContext, check_policy
from ml.inference.recovery import RecoveryInference

_ACTIONS = (
    enums.Action.RETRY.value,
    enums.Action.MESSAGE.value,
    enums.Action.NO_ACTION.value,
)
_EXECUTABLE = frozenset(enums.EXECUTABLE_ACTIONS)


@dataclass
class DecisionEngineConfig:
    costs: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_COSTS))
    min_eirv_threshold: float = 0.0
    decision_engine_version: str = "phase3-baseline-v1"


@dataclass
class DecisionOutcome:
    decision_record: DecisionRecord
    recommended_action: str
    final_action: str
    probabilities: dict[str, float]
    eirv: dict[str, float]
    ranked_actions: list[str]
    policy_results: dict[str, str]  # action -> ALLOWED / BLOCKED
    intervention_created: bool
    decision_reason: str


class DecisionEngine:
    """Deterministic baseline decision engine.

    ``predictor`` is an ``ml.inference.RecoveryInference`` bound to one
    exact immutable ``ModelVersion``. All three ``Prediction`` rows in a
    cycle reference that same ``ModelVersion``.
    """

    def __init__(
        self,
        db: Session,
        *,
        predictor: RecoveryInference,
        model_version: ModelVersion,
        config: DecisionEngineConfig | None = None,
    ) -> None:
        self.db = db
        self.predictor = predictor
        self.model_version = model_version
        self.cfg = config or DecisionEngineConfig()
        self._dc = DecisionCycleRepository(db)
        if str(model_version.id) != predictor.model_version_id:
            raise ValueError(
                "predictor is not bound to the supplied ModelVersion "
                f"({predictor.model_version_id} != {model_version.id})"
            )

    # ---------------------------------------------------------------- public
    def run_cycle(
        self,
        *,
        case: RecoveryCase,
        feature_snapshot: dict,
        policy: Policy,
        policy_context: PolicyContext,
        decision_timestamp: datetime | None = None,
    ) -> DecisionOutcome:
        now = decision_timestamp or utcnow()
        amount = float(case.amount_at_risk)

        # 1) model inference — probabilities only, one per candidate action
        probs = self.predictor.predict_all_actions(feature_snapshot)
        for a in _ACTIONS:
            probs.setdefault(a, 0.0)

        # 2) open the DecisionRecord and persist per-action Predictions
        dr = self._dc.open_cycle(
            case=case,
            payment_amount_at_decision=case.amount_at_risk,
            decision_timestamp=now,
            policy_id=policy.policy_id,
            policy_version=policy.policy_version,
            decision_engine_version=self.cfg.decision_engine_version,
        )
        for action in _ACTIONS:
            self._dc.add_prediction(
                decision_record=dr,
                action=action,
                recovery_probability=_as_prob(probs[action]),
                model_version_id=self.model_version.id,
                feature_snapshot=feature_snapshot,
            )

        # 3) EIRV + 4) recommendation (pre-policy)
        eirv = eirv_by_action(probs, amount, self.cfg.costs)
        ranked = rank_actions(eirv, min_eirv_threshold=self.cfg.min_eirv_threshold)
        recommended_action = ranked[0]

        # 5) policy veto loop -> final_action (always terminates at NO_ACTION)
        policy_results: dict[str, str] = {}
        final_action = enums.Action.NO_ACTION.value
        for candidate in ranked:
            decision = check_policy(candidate, policy, policy_context)
            self._dc.add_policy_evaluation(
                decision_record=dr,
                action=candidate,
                policy_id=policy.policy_id,
                policy_version=policy.policy_version,
                result=decision.result,
                reason_code=decision.reason_code,
                reason=decision.reason,
                evaluated_at=now,
            )
            policy_results[candidate] = decision.result
            if decision.allowed:
                final_action = candidate
                break

        # 6) finalize the DecisionRecord (recommended & final stored separately)
        reason = _reason(recommended_action, final_action, policy_results)
        value_context = [
            {
                "action": a,
                "cost_used": float(self.cfg.costs.get(a, 0.0)),
                "eirv_value": round(float(eirv[a]), 4),
                "recovery_probability": round(float(probs[a]), 6),
            }
            for a in _ACTIONS
        ]
        self._dc.finalize(
            decision_record=dr,
            recommended_action=recommended_action,
            final_action=final_action,
            decision_reason=reason,
            value_context=value_context,
        )

        # 7) Intervention ONLY for RETRY / MESSAGE (never for NO_ACTION)
        intervention_created = False
        if final_action in _EXECUTABLE:
            self._dc.record_intervention(
                decision_record=dr,
                action=final_action,
                channel="SIMULATED" if final_action == enums.Action.MESSAGE.value else None,
                execution_status=enums.ExecutionStatus.REQUESTED.value,
                cost_incurred=Decimal(str(self.cfg.costs.get(final_action, 0.0))),
                requested_at=now,
            )
            intervention_created = True

        self.db.flush()
        return DecisionOutcome(
            decision_record=dr,
            recommended_action=recommended_action,
            final_action=final_action,
            probabilities=probs,
            eirv=eirv,
            ranked_actions=ranked,
            policy_results=policy_results,
            intervention_created=intervention_created,
            decision_reason=reason,
        )


def _as_prob(value: float) -> Decimal:
    """Clamp to [0, 1] and quantize to the Prediction column scale (9,8)."""
    v = max(0.0, min(1.0, float(value)))
    return Decimal(f"{v:.8f}")


def _reason(recommended: str, final: str, policy_results: dict[str, str]) -> str:
    if recommended == final:
        return f"{recommended} had the highest EIRV and was allowed by policy"
    blocked = [
        a for a, r in policy_results.items()
        if r == enums.PolicyResult.BLOCKED.value
    ]
    return (
        f"recommended {recommended} (highest EIRV) but policy blocked "
        f"{', '.join(blocked) or recommended}; final action {final}"
    )
