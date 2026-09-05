"""Phase 1B data-layer contract tests.

Each test maps to a rule from the finalized Phase 1A contract
(docs/decisions/architecture-decisions.md, ADR-009..ADR-012).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from backend.core.errors import (
    ActiveCaseExistsError,
    DataContractError,
    ExperimentAlreadyAssignedError,
    InvalidTransitionError,
    PromotedModelExistsError,
)
from backend.models import enums
from backend.models.core_entities import Merchant, Payment, PaymentEvent, RecoveryCase
from backend.repositories import (
    CustomerRepository,
    DecisionCycleRepository,
    ExperimentRepository,
    MerchantRepository,
    ModelVersionRepository,
    PaymentEventRepository,
    PaymentRepository,
    PolicyRepository,
    RecoveryCaseRepository,
    TrainingExampleRepository,
)

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _merchant(db) -> Merchant:
    return MerchantRepository(db).create(name="ACME SaaS")


def _customer(db, merchant) -> str:
    CustomerRepository(db).create(customer_id="C-1", merchant_id=merchant.id)
    return "C-1"


def _payment(db, merchant, cust, status=enums.PaymentStatus.FAILED.value) -> Payment:
    return PaymentRepository(db).create(
        merchant_id=merchant.id,
        customer_id=cust,
        amount=Decimal("1000.00"),
        currency="INR",
        status=status,
        external_payment_id="pay_ext_1",
    )


def _model_version(db, *, status=enums.ModelVersionStatus.PROMOTED.value):
    return ModelVersionRepository(db).create(
        model_role="recovery_prediction",
        model_name="s-learner",
        version="v1.0.0",
        status=status,
        training_dataset_snapshot_id="tds-seed",
        feature_schema_id="fs-1",
    )


def _open_case(db, payment) -> RecoveryCase:
    return RecoveryCaseRepository(db).open_case(
        payment=payment, amount_at_risk=payment.amount, opened_at=NOW
    )


# --------------------------------------------------------------------------
# identity
# --------------------------------------------------------------------------
class TestIdentity:
    def test_uuid_primary_keys(self, db):
        m = _merchant(db)
        assert isinstance(m.id, uuid.UUID)
        cust = _customer(db, m)
        p = _payment(db, m, cust)
        assert isinstance(p.id, uuid.UUID) and p.id != m.id

    def test_display_ids_unique_and_prefixed(self, db):
        m1 = MerchantRepository(db).create(name="A")
        m2 = MerchantRepository(db).create(name="B")
        assert m1.display_id.startswith("M-") and m2.display_id.startswith("M-")
        assert m1.display_id != m2.display_id

    def test_provider_id_is_not_the_pk(self, db):
        m = _merchant(db)
        cust = _customer(db, m)
        p = _payment(db, m, cust)
        assert p.external_payment_id == "pay_ext_1"
        assert str(p.id) != p.external_payment_id  # UUID PK, not provider id

    def test_payment_event_has_uuid_id_no_display_id(self, db):
        m = _merchant(db)
        cust = _customer(db, m)
        p = _payment(db, m, cust)
        e = PaymentEventRepository(db).append(
            payment_id=p.id,
            event_type=enums.PaymentEventType.PAYMENT_CREATED.value,
            event_timestamp=NOW,
        )
        assert isinstance(e.id, uuid.UUID)
        assert not hasattr(e, "display_id")


# --------------------------------------------------------------------------
# PaymentEvent — authoritative, append-only
# --------------------------------------------------------------------------
class TestPaymentEvent:
    def test_append_only_no_update_or_delete_api(self):
        repo_api = set(dir(PaymentEventRepository))
        assert "update" not in repo_api and "delete" not in repo_api

    def test_events_persist_unchanged_and_ordered(self, db):
        m = _merchant(db)
        cust = _customer(db, m)
        p = _payment(db, m, cust)
        r = PaymentEventRepository(db)
        r.append(payment_id=p.id, event_type="PAYMENT_CREATED", event_timestamp=NOW)
        r.append(
            payment_id=p.id,
            event_type="PAYMENT_FAILED",
            event_timestamp=NOW + timedelta(minutes=1),
            attempt_number=1,
        )
        r.append(
            payment_id=p.id,
            event_type="RETRY_ATTEMPTED",
            event_timestamp=NOW + timedelta(minutes=5),
            attempt_number=2,
        )
        evts = r.list_for_payment(p.id)
        assert [e.event_type for e in evts] == [
            "PAYMENT_CREATED",
            "PAYMENT_FAILED",
            "RETRY_ATTEMPTED",
        ]

    def test_invalid_event_type_rejected(self, db):
        m = _merchant(db)
        cust = _customer(db, m)
        p = _payment(db, m, cust)
        with pytest.raises(IntegrityError):
            PaymentEventRepository(db).append(
                payment_id=p.id, event_type="PAYMENT_WEIRD", event_timestamp=NOW
            )

    def test_attempt_number_nullable_and_separate_timestamps(self, db):
        m = _merchant(db)
        cust = _customer(db, m)
        p = _payment(db, m, cust)
        e = PaymentEventRepository(db).append(
            payment_id=p.id,
            event_type="PAYMENT_CREATED",
            event_timestamp=NOW,  # occurred
        )
        assert e.attempt_number is None
        assert e.event_timestamp == NOW
        assert e.created_at >= NOW - timedelta(days=1)  # ingested, distinct field
        assert e.created_at is not e.event_timestamp


# --------------------------------------------------------------------------
# RecoveryCase
# --------------------------------------------------------------------------
class TestRecoveryCase:
    def test_at_most_one_active_case_per_payment(self, db):
        m = _merchant(db)
        cust = _customer(db, m)
        p = _payment(db, m, cust)
        _open_case(db, p)
        with pytest.raises(ActiveCaseExistsError):
            _open_case(db, p)

    def test_reopen_allowed_after_terminal(self, db):
        m = _merchant(db)
        cust = _customer(db, m)
        p = _payment(db, m, cust)
        repo = RecoveryCaseRepository(db)
        c1 = _open_case(db, p)
        repo.transition(c1, "ANALYZING")
        repo.transition(c1, "STOPPED", reason="limits reached")
        c2 = repo.open_case(payment=p, amount_at_risk=p.amount, opened_at=NOW)
        assert c2.id != c1.id

    def test_multiple_decision_records_per_case(self, db):
        m = _merchant(db)
        cust = _customer(db, m)
        p = _payment(db, m, cust)
        case = _open_case(db, p)
        dc = DecisionCycleRepository(db)
        d1 = dc.open_cycle(case=case, payment_amount_at_decision=p.amount)
        d2 = dc.open_cycle(case=case, payment_amount_at_decision=p.amount)
        d3 = dc.open_cycle(case=case, payment_amount_at_decision=p.amount)
        assert [d.cycle_number for d in (d1, d2, d3)] == [1, 2, 3]

    def test_invalid_transition_rejected(self, db):
        m = _merchant(db)
        cust = _customer(db, m)
        p = _payment(db, m, cust)
        case = _open_case(db, p)
        RecoveryCaseRepository(db).transition(case, "ANALYZING")
        RecoveryCaseRepository(db).transition(case, "ACTION_SELECTED")
        RecoveryCaseRepository(db).transition(case, "ACTION_EXECUTED")
        RecoveryCaseRepository(db).transition(case, "WAITING_FOR_OUTCOME")
        RecoveryCaseRepository(db).transition(case, "RECOVERED")
        with pytest.raises(InvalidTransitionError):
            RecoveryCaseRepository(db).transition(case, "ACTION_EXECUTED")

    def test_status_history_appended(self, db):
        m = _merchant(db)
        cust = _customer(db, m)
        p = _payment(db, m, cust)
        case = _open_case(db, p)
        RecoveryCaseRepository(db).transition(case, "ANALYZING", reason="scoring")
        db.refresh(case)
        assert [h.to_status for h in case.status_history] == ["OPEN", "ANALYZING"]


# --------------------------------------------------------------------------
# DecisionRecord / Prediction / PolicyEvaluation
# --------------------------------------------------------------------------
class TestDecisionCycle:
    def _cycle(self, db):
        m = _merchant(db)
        cust = _customer(db, m)
        p = _payment(db, m, cust)
        case = _open_case(db, p)
        mv = _model_version(db)
        dc = DecisionCycleRepository(db)
        dr = dc.open_cycle(case=case, payment_amount_at_decision=p.amount)
        for act, prob in (("RETRY", 0.72), ("MESSAGE", 0.51), ("NO_ACTION", 0.19)):
            dc.add_prediction(
                decision_record=dr,
                action=act,
                recovery_probability=Decimal(str(prob)),
                model_version_id=mv.id,
                feature_snapshot={"amount": 1000, "method": "UPI", "as_of": "decision"},
            )
        return db, dc, dr, case, mv

    def test_predictions_are_action_specific_and_one_per_action(self, db):
        db, dc, dr, case, mv = self._cycle(db)
        db.refresh(dr)
        assert sorted(pr.action for pr in dr.predictions) == [
            "MESSAGE",
            "NO_ACTION",
            "RETRY",
        ]
        with pytest.raises(IntegrityError):
            dc.add_prediction(
                decision_record=dr,
                action="RETRY",
                recovery_probability=Decimal("0.5"),
                model_version_id=mv.id,
                feature_snapshot={},
            )

    def test_prediction_references_exact_model_version(self, db):
        db, dc, dr, case, mv = self._cycle(db)
        db.refresh(dr)
        assert all(pr.model_version_id == mv.id for pr in dr.predictions)

    def test_decision_record_has_no_model_version_column(self):
        from backend.models.decision import DecisionRecord

        cols = set(DecisionRecord.__table__.columns.keys())
        assert "model_version_id" not in cols and "model_version" not in cols

    def test_decision_record_has_no_experiment_column(self):
        from backend.models.decision import DecisionRecord

        cols = set(DecisionRecord.__table__.columns.keys())
        assert not any("experiment" in c for c in cols)

    def test_recommended_and_final_action_stored_separately(self, db):
        db, dc, dr, case, mv = self._cycle(db)
        dc.add_policy_evaluation(
            decision_record=dr,
            action="RETRY",
            policy_id="POL-1",
            policy_version="v1",
            result="BLOCKED",
            reason_code="MAX_RETRY_LIMIT",
        )
        dc.finalize(
            decision_record=dr,
            recommended_action="RETRY",
            final_action="NO_ACTION",
            decision_reason="RETRY blocked by policy",
        )
        assert dr.recommended_action == "RETRY"
        assert dr.final_action == "NO_ACTION"

    def test_policy_evaluation_candidate_specific_with_version(self, db):
        db, dc, dr, case, mv = self._cycle(db)
        pe = dc.add_policy_evaluation(
            decision_record=dr,
            action="MESSAGE",
            policy_id="POL-1",
            policy_version="v3",
            result="ALLOWED",
        )
        assert pe.action == "MESSAGE" and pe.policy_version == "v3"


# --------------------------------------------------------------------------
# Intervention
# --------------------------------------------------------------------------
class TestIntervention:
    def _ready(self, db, final_action):
        m = _merchant(db)
        cust = _customer(db, m)
        p = _payment(db, m, cust)
        case = _open_case(db, p)
        mv = _model_version(db)
        dc = DecisionCycleRepository(db)
        dr = dc.open_cycle(case=case, payment_amount_at_decision=p.amount)
        for act in ("RETRY", "MESSAGE", "NO_ACTION"):
            dc.add_prediction(
                decision_record=dr,
                action=act,
                recovery_probability=Decimal("0.4"),
                model_version_id=mv.id,
                feature_snapshot={},
            )
        dc.finalize(
            decision_record=dr,
            recommended_action=final_action,
            final_action=final_action,
        )
        return dc, dr

    def test_retry_message_only(self, db):
        dc, dr = self._ready(db, "RETRY")
        intv = dc.record_intervention(decision_record=dr, action="RETRY")
        assert intv.action == "RETRY"
        assert intv.execution_status == "REQUESTED"

    def test_no_action_creates_no_intervention(self, db):
        dc, dr = self._ready(db, "NO_ACTION")
        with pytest.raises(DataContractError):
            dc.record_intervention(decision_record=dr, action="NO_ACTION")

    def test_execution_status_vocab_no_succeeded(self, db):
        assert "SUCCEEDED" not in enums.values(enums.ExecutionStatus)
        dc, dr = self._ready(db, "MESSAGE")
        intv = dc.record_intervention(decision_record=dr, action="MESSAGE")
        with pytest.raises(IntegrityError):
            intv.execution_status = "SUCCEEDED"
            db.flush()

    def test_one_intervention_per_decision_record(self, db):
        dc, dr = self._ready(db, "RETRY")
        dc.record_intervention(decision_record=dr, action="RETRY")
        with pytest.raises(IntegrityError):
            dc.record_intervention(decision_record=dr, action="RETRY")


# --------------------------------------------------------------------------
# Outcome
# --------------------------------------------------------------------------
class TestOutcome:
    def _cycle_final(self, db, final_action):
        m = _merchant(db)
        cust = _customer(db, m)
        p = _payment(db, m, cust)
        case = _open_case(db, p)
        mv = _model_version(db)
        dc = DecisionCycleRepository(db)
        dr = dc.open_cycle(case=case, payment_amount_at_decision=p.amount)
        for act in ("RETRY", "MESSAGE", "NO_ACTION"):
            dc.add_prediction(
                decision_record=dr,
                action=act,
                recovery_probability=Decimal("0.4"),
                model_version_id=mv.id,
                feature_snapshot={},
            )
        dc.finalize(
            decision_record=dr,
            recommended_action=final_action,
            final_action=final_action,
        )
        return dc, dr

    def test_result_vocab(self, db):
        assert enums.values(enums.OutcomeResult) == ["RECOVERED", "NOT_RECOVERED"]

    def test_delayed_outcome(self, db):
        dc, dr = self._cycle_final(db, "RETRY")
        intv = dc.record_intervention(
            decision_record=dr, action="RETRY", requested_at=NOW
        )
        dc.update_execution_status(intv, "ACCEPTED", resolved_at=NOW)
        later = NOW + timedelta(minutes=52)
        oc = dc.record_outcome(
            decision_record=dr,
            result="RECOVERED",
            recovery_amount=Decimal("1000.00"),
            observed_at=later,
            intervention=intv,
        )
        assert oc.observed_at == later
        assert oc.observed_at > intv.resolved_at
        assert oc.intervention_id == intv.id

    def test_outcome_on_no_action_cycle_without_intervention(self, db):
        dc, dr = self._cycle_final(db, "NO_ACTION")
        oc = dc.record_outcome(
            decision_record=dr, result="NOT_RECOVERED", observed_at=NOW
        )
        assert oc.intervention_id is None
        assert oc.decision_record_id == dr.id

    def test_execution_status_is_not_the_recovery_label(self, db):
        dc, dr = self._cycle_final(db, "RETRY")
        intv = dc.record_intervention(decision_record=dr, action="RETRY")
        dc.update_execution_status(intv, "ACCEPTED")
        oc = dc.record_outcome(
            decision_record=dr, result="NOT_RECOVERED", observed_at=NOW, intervention=intv
        )
        assert intv.execution_status == "ACCEPTED"
        assert oc.result == "NOT_RECOVERED"  # accepted != recovered


# --------------------------------------------------------------------------
# Experiment
# --------------------------------------------------------------------------
class TestExperiment:
    def test_one_assignment_per_case_immutable(self, db):
        m = _merchant(db)
        cust = _customer(db, m)
        p = _payment(db, m, cust)
        case = _open_case(db, p)
        er = ExperimentRepository(db)
        exp = er.create(name="uplift-v2-trial")
        er.assign(experiment_id=exp.id, recovery_case_id=case.id, arm="TREATMENT")
        with pytest.raises(ExperimentAlreadyAssignedError):
            er.assign(experiment_id=exp.id, recovery_case_id=case.id, arm="CONTROL")

    def test_arm_vocab(self, db):
        assert enums.values(enums.ExperimentArm) == ["CONTROL", "TREATMENT"]

    def test_arm_inherited_by_case_not_decision_record(self, db):
        m = _merchant(db)
        cust = _customer(db, m)
        p = _payment(db, m, cust)
        case = _open_case(db, p)
        er = ExperimentRepository(db)
        exp = er.create(name="e1")
        er.assign(experiment_id=exp.id, recovery_case_id=case.id, arm="CONTROL")
        assert er.arm_for_case(case.id) == "CONTROL"
        from backend.models.governance import ExperimentAssignment

        assert "decision_record_id" not in ExperimentAssignment.__table__.columns


# --------------------------------------------------------------------------
# ModelVersion
# --------------------------------------------------------------------------
class TestModelVersion:
    def test_lifecycle_transitions(self, db):
        r = ModelVersionRepository(db)
        mv = r.create(
            model_role="recovery_prediction", model_name="s", version="v2",
            status="DRAFT",
        )
        r.transition_status(mv, "VALIDATED")
        r.transition_status(mv, "PROMOTED")
        r.transition_status(mv, "RETIRED")
        assert mv.status == "RETIRED"

    def test_rejected_cannot_become_promoted(self, db):
        r = ModelVersionRepository(db)
        mv = r.create(
            model_role="recovery_prediction", model_name="s", version="v3",
            status="DRAFT",
        )
        r.transition_status(mv, "REJECTED")
        with pytest.raises(InvalidTransitionError):
            r.transition_status(mv, "PROMOTED")

    def test_draft_cannot_skip_to_promoted(self, db):
        r = ModelVersionRepository(db)
        mv = r.create(
            model_role="recovery_prediction", model_name="s", version="v4",
            status="DRAFT",
        )
        with pytest.raises(InvalidTransitionError):
            r.transition_status(mv, "PROMOTED")

    def test_one_promoted_per_role(self, db):
        r = ModelVersionRepository(db)
        a = r.create(
            model_role="recovery_prediction", model_name="s", version="v5",
            status="VALIDATED",
        )
        r.transition_status(a, "PROMOTED")
        b = r.create(
            model_role="recovery_prediction", model_name="s", version="v6",
            status="VALIDATED",
        )
        with pytest.raises(PromotedModelExistsError):
            r.transition_status(b, "PROMOTED")

    def test_two_promoted_different_roles_ok(self, db):
        r = ModelVersionRepository(db)
        a = r.create(model_role="role_a", model_name="s", version="v1", status="VALIDATED")
        b = r.create(model_role="role_b", model_name="s", version="v1", status="VALIDATED")
        r.transition_status(a, "PROMOTED")
        r.transition_status(b, "PROMOTED")
        assert a.status == b.status == "PROMOTED"


# --------------------------------------------------------------------------
# Policy
# --------------------------------------------------------------------------
class TestPolicy:
    def test_versions_immutable_new_row_per_change(self, db):
        m = _merchant(db)
        r = PolicyRepository(db)
        v1 = r.create_version(
            policy_id="POL-M1", policy_version="v1", merchant_id=m.id, max_retry_count=2
        )
        v2 = r.create_version(
            policy_id="POL-M1", policy_version="v2", merchant_id=m.id, max_retry_count=1
        )
        assert v1.max_retry_count == 2 and v2.max_retry_count == 1
        assert v1.is_active is False and v2.is_active is True

    def test_one_active_per_merchant(self, db):
        m = _merchant(db)
        r = PolicyRepository(db)
        r.create_version(policy_id="POL-M1", policy_version="v1", merchant_id=m.id)
        r.create_version(policy_id="POL-M1", policy_version="v2", merchant_id=m.id)
        active = r.active_for_merchant(m.id)
        assert active.policy_version == "v2"

    def test_immutable_rule_guard(self, db):
        from backend.core.errors import ImmutableRecordError

        m = _merchant(db)
        r = PolicyRepository(db)
        v1 = r.create_version(
            policy_id="POL-M1", policy_version="v1", merchant_id=m.id, max_retry_count=2
        )
        with pytest.raises(ImmutableRecordError):
            r.assert_rules_unchanged(v1, max_retry_count=5)


# --------------------------------------------------------------------------
# TrainingExample — Phase 1A.4
# --------------------------------------------------------------------------
class TestTrainingExample:
    _seq = 0

    def _resolved_cycle(self, db, *, final_action, exec_status="ACCEPTED",
                        result="RECOVERED", arm=None):
        TestTrainingExample._seq += 1
        n = TestTrainingExample._seq
        m = _merchant(db)
        cid = f"C-{n}"
        CustomerRepository(db).create(customer_id=cid, merchant_id=m.id)
        cust = cid
        p = _payment(db, m, cust)
        case = _open_case(db, p)
        mv = ModelVersionRepository(db).create(
            model_role=f"recovery_prediction_{n}",
            model_name="s-learner",
            version="v1",
            status=enums.ModelVersionStatus.VALIDATED.value,
        )
        dc = DecisionCycleRepository(db)
        dr = dc.open_cycle(case=case, payment_amount_at_decision=p.amount)
        for act, prob in (("RETRY", 0.72), ("MESSAGE", 0.51), ("NO_ACTION", 0.19)):
            dc.add_prediction(
                decision_record=dr,
                action=act,
                recovery_probability=Decimal(str(prob)),
                model_version_id=mv.id,
                feature_snapshot={"action_slot": act, "amount": 1000},
            )
        dc.finalize(
            decision_record=dr,
            recommended_action=final_action,
            final_action=final_action,
        )
        intv = None
        if final_action in ("RETRY", "MESSAGE"):
            intv = dc.record_intervention(decision_record=dr, action=final_action)
            dc.update_execution_status(intv, exec_status)
        dc.record_outcome(
            decision_record=dr,
            result=result,
            recovery_amount=Decimal("1000.00") if result == "RECOVERED" else 0,
            observed_at=NOW + timedelta(minutes=30),
            intervention=intv,
        )
        # drive the case terminal so it's labellable
        for to in ("ANALYZING", "ACTION_SELECTED", "ACTION_EXECUTED",
                   "WAITING_FOR_OUTCOME",
                   "RECOVERED" if result == "RECOVERED" else "EXPIRED"):
            RecoveryCaseRepository(db).transition(case, to)
        if arm:
            er = ExperimentRepository(db)
            exp = er.create(name=f"exp-{case.display_id}")
            er.assign(experiment_id=exp.id, recovery_case_id=case.id, arm=arm)
        return db, dr, case

    def test_one_row_per_decision_record_x_action(self, db):
        db, dr, case = self._resolved_cycle(db, final_action="RETRY")
        rows = TrainingExampleRepository(db).generate_for_decision_record(dr)
        assert sorted(r.action for r in rows) == ["MESSAGE", "NO_ACTION", "RETRY"]

    def test_only_observed_action_gets_label(self, db):
        db, dr, case = self._resolved_cycle(db, final_action="RETRY", result="RECOVERED")
        rows = {r.action: r for r in
                TrainingExampleRepository(db).generate_for_decision_record(dr)}
        assert rows["RETRY"].is_observed is True
        assert rows["RETRY"].outcome_label == "RECOVERED"
        assert rows["MESSAGE"].is_observed is False
        assert rows["MESSAGE"].outcome_label is None
        assert rows["NO_ACTION"].is_observed is False
        assert rows["NO_ACTION"].outcome_label is None

    def test_no_action_observed_without_intervention(self, db):
        db, dr, case = self._resolved_cycle(
            db, final_action="NO_ACTION", result="RECOVERED"
        )
        rows = {r.action: r for r in
                TrainingExampleRepository(db).generate_for_decision_record(dr)}
        assert rows["NO_ACTION"].is_observed is True
        assert rows["NO_ACTION"].outcome_label == "RECOVERED"
        assert dr.intervention is None

    def test_failed_execution_not_clean_observation(self, db):
        db, dr, case = self._resolved_cycle(
            db, final_action="RETRY", exec_status="FAILED", result="NOT_RECOVERED"
        )
        rows = {r.action: r for r in
                TrainingExampleRepository(db).generate_for_decision_record(dr)}
        assert rows["RETRY"].observed_action == "RETRY"
        assert rows["RETRY"].is_observed is False  # decision != clean exposure
        assert rows["RETRY"].outcome_label is None

    def test_observed_action_is_final_not_recommendation(self, db):
        # recommend RETRY, policy blocks -> final NO_ACTION
        m = _merchant(db)
        cust = _customer(db, m)
        p = _payment(db, m, cust)
        case = _open_case(db, p)
        mv = _model_version(db)
        dc = DecisionCycleRepository(db)
        dr = dc.open_cycle(case=case, payment_amount_at_decision=p.amount)
        for act in ("RETRY", "MESSAGE", "NO_ACTION"):
            dc.add_prediction(
                decision_record=dr, action=act,
                recovery_probability=Decimal("0.4"),
                model_version_id=mv.id, feature_snapshot={"a": act},
            )
        dc.add_policy_evaluation(
            decision_record=dr, action="RETRY", policy_id="P", policy_version="v1",
            result="BLOCKED", reason_code="MAX_RETRY_LIMIT",
        )
        dc.finalize(
            decision_record=dr, recommended_action="RETRY", final_action="NO_ACTION"
        )
        dc.record_outcome(
            decision_record=dr, result="NOT_RECOVERED", observed_at=NOW
        )
        for to in ("ANALYZING", "ACTION_SELECTED", "ACTION_EXECUTED",
                   "WAITING_FOR_OUTCOME", "EXPIRED"):
            RecoveryCaseRepository(db).transition(case, to)
        rows = {r.action: r for r in
                TrainingExampleRepository(db).generate_for_decision_record(dr)}
        assert rows["NO_ACTION"].observed_action == "NO_ACTION"
        assert rows["NO_ACTION"].is_observed is True
        assert rows["RETRY"].is_observed is False

    def test_feature_snapshot_has_no_future_outcome(self, db):
        db, dr, case = self._resolved_cycle(db, final_action="RETRY")
        for r in TrainingExampleRepository(db).generate_for_decision_record(dr):
            snap = r.feature_snapshot
            assert "outcome" not in snap and "recovery_amount" not in snap
            assert "observed_at" not in snap

    def test_case_level_grouping_preserved_in_split(self, db):
        from sqlalchemy import select

        from backend.models.decision import DecisionRecord

        for _ in range(6):
            self._resolved_cycle(db, final_action="RETRY")
        ter = TrainingExampleRepository(db)
        for dr in db.scalars(select(DecisionRecord)).all():
            ter.generate_for_decision_record(dr)
        split = ter.split_by_case(seed=1)
        # every case's rows are entirely within one split
        seen: dict[str, str] = {}
        for name, rows in split.items():
            for row in rows:
                cid = str(row.recovery_case_id)
                assert seen.setdefault(cid, name) == name

    def test_snapshot_id_is_deterministic(self, db):
        db, dr, case = self._resolved_cycle(db, final_action="RETRY")
        ter = TrainingExampleRepository(db)
        rows = ter.generate_for_decision_record(dr)
        assert ter.snapshot_id(rows) == ter.snapshot_id(list(reversed(rows)))

    def test_experiment_arm_inherited(self, db):
        db, dr, case = self._resolved_cycle(db, final_action="RETRY", arm="TREATMENT")
        rows = TrainingExampleRepository(db).generate_for_decision_record(dr)
        assert all(r.experiment_arm == "TREATMENT" for r in rows)

    def test_not_generated_until_outcome_and_terminal(self, db):
        m = _merchant(db)
        cust = _customer(db, m)
        p = _payment(db, m, cust)
        case = _open_case(db, p)
        mv = _model_version(db)
        dc = DecisionCycleRepository(db)
        dr = dc.open_cycle(case=case, payment_amount_at_decision=p.amount)
        for act in ("RETRY", "MESSAGE", "NO_ACTION"):
            dc.add_prediction(
                decision_record=dr, action=act,
                recovery_probability=Decimal("0.4"),
                model_version_id=mv.id, feature_snapshot={},
            )
        dc.finalize(decision_record=dr, recommended_action="RETRY", final_action="RETRY")
        assert TrainingExampleRepository(db).generate_for_decision_record(dr) == []
