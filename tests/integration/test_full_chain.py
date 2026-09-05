"""End-to-end relational chain over a clean database:

Merchant -> Payment -> PaymentEvent(PAYMENT_FAILED) -> RecoveryCase
  -> ExperimentAssignment -> DecisionRecord -> Predictions(3)
  -> PolicyEvaluations -> final action -> Intervention -> Outcome
  -> TrainingExamples

Proves every foreign key and contract rule works together.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from backend.models import enums
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

T0 = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)


def test_full_recovery_chain(db):
    # --- merchant / customer / policy / model -------------------------
    merchant = MerchantRepository(db).create(name="ABC SaaS", industry="saas")
    CustomerRepository(db).create(customer_id="C-482", merchant_id=merchant.id)
    policy = PolicyRepository(db).create_version(
        policy_id=f"POL-{merchant.display_id}",
        policy_version="v1",
        merchant_id=merchant.id,
        max_retry_count=2,
        max_customer_contacts=2,
    )
    mv_repo = ModelVersionRepository(db)
    model = mv_repo.create(
        model_role="recovery_prediction",
        model_name="s-learner",
        version="v1.3.2",
        status="VALIDATED",
        training_dataset_snapshot_id="tds-seed-0",
        feature_schema_id="fs-1",
    )
    mv_repo.transition_status(model, "PROMOTED")

    # --- payment + lifecycle events (append-only) --------------------
    payment = PaymentRepository(db).create(
        merchant_id=merchant.id,
        customer_id="C-482",
        amount=Decimal("1000.00"),
        currency="INR",
        status=enums.PaymentStatus.CREATED.value,
        external_payment_id="pay_ABC123",
        payment_method="UPI",
    )
    pe = PaymentEventRepository(db)
    pe.append(
        payment_id=payment.id,
        event_type="PAYMENT_CREATED",
        event_timestamp=T0,
    )
    pe.append(
        payment_id=payment.id,
        event_type="PAYMENT_FAILED",
        event_timestamp=T0 + timedelta(minutes=1),
        attempt_number=1,
        metadata={"error_code": "BAD_REQUEST_ERROR"},
    )
    PaymentRepository(db).set_status(payment, enums.PaymentStatus.FAILED.value)

    # --- recovery eligibility -> open a case ------------------------
    cases = RecoveryCaseRepository(db)
    case = cases.open_case(
        payment=payment,
        amount_at_risk=payment.amount,
        failure_category="AUTH_FAILURE",
        opened_at=T0 + timedelta(minutes=1),
    )
    assert case.status == "OPEN"

    # --- experiment assignment (CASE level, once) ------------------
    er = ExperimentRepository(db)
    exp = er.create(name="baseline-vs-recoverai", status="RUNNING")
    er.assign(
        experiment_id=exp.id,
        recovery_case_id=case.id,
        arm="TREATMENT",
        experimental_config_ref=model.id,
    )

    # --- decision cycle 1: recommend RETRY, allowed, execute ------
    dc = DecisionCycleRepository(db)
    cases.transition(case, "ANALYZING")
    d1 = dc.open_cycle(
        case=case,
        payment_amount_at_decision=payment.amount,
        policy_id=policy.policy_id,
        policy_version=policy.policy_version,
        decision_engine_version="de-0.1",
    )
    probs = {"RETRY": "0.72", "MESSAGE": "0.61", "NO_ACTION": "0.43"}
    for action, prob in probs.items():
        dc.add_prediction(
            decision_record=d1,
            action=action,
            recovery_probability=Decimal(prob),
            model_version_id=model.id,
            feature_snapshot={"amount": 1000, "method": "UPI", "attempt": 1,
                              "candidate_action": action},
        )
    for action in ("RETRY", "MESSAGE"):
        dc.add_policy_evaluation(
            decision_record=d1,
            action=action,
            policy_id=policy.policy_id,
            policy_version=policy.policy_version,
            result="ALLOWED",
        )
    dc.finalize(
        decision_record=d1,
        recommended_action="RETRY",
        final_action="RETRY",
        decision_reason="highest EIRV, allowed",
        value_context=[
            {"action": "RETRY", "cost_used": 2, "eirv_value": 288},
            {"action": "MESSAGE", "cost_used": 3, "eirv_value": 177},
            {"action": "NO_ACTION", "cost_used": 0, "eirv_value": 0},
        ],
    )
    cases.transition(case, "ACTION_SELECTED")
    i1 = dc.record_intervention(
        decision_record=d1, action="RETRY", channel=None, requested_at=T0 + timedelta(minutes=2)
    )
    dc.update_execution_status(i1, "ACCEPTED", resolved_at=T0 + timedelta(minutes=2))
    cases.transition(case, "ACTION_EXECUTED")
    cases.transition(case, "WAITING_FOR_OUTCOME")
    dc.record_outcome(
        decision_record=d1,
        result="NOT_RECOVERED",
        observed_at=T0 + timedelta(minutes=20),
        intervention=i1,
    )

    # --- re-evaluate: decision cycle 2 -> MESSAGE -> recovered ----
    cases.transition(case, "ANALYZING")  # re-evaluate loop
    d2 = dc.open_cycle(
        case=case,
        payment_amount_at_decision=payment.amount,
        policy_id=policy.policy_id,
        policy_version=policy.policy_version,
    )
    assert d2.cycle_number == 2
    for action, prob in {"RETRY": "0.35", "MESSAGE": "0.66", "NO_ACTION": "0.40"}.items():
        dc.add_prediction(
            decision_record=d2,
            action=action,
            recovery_probability=Decimal(prob),
            model_version_id=model.id,
            feature_snapshot={"amount": 1000, "attempt": 2, "candidate_action": action},
        )
    dc.add_policy_evaluation(
        decision_record=d2, action="MESSAGE",
        policy_id=policy.policy_id, policy_version=policy.policy_version, result="ALLOWED",
    )
    dc.finalize(decision_record=d2, recommended_action="MESSAGE", final_action="MESSAGE")
    cases.transition(case, "ACTION_SELECTED")
    i2 = dc.record_intervention(decision_record=d2, action="MESSAGE", channel="SIMULATED")
    dc.update_execution_status(i2, "ACCEPTED")
    cases.transition(case, "ACTION_EXECUTED")
    cases.transition(case, "WAITING_FOR_OUTCOME")
    dc.record_outcome(
        decision_record=d2,
        result="RECOVERED",
        recovery_amount=Decimal("1000.00"),
        observed_at=T0 + timedelta(minutes=52),
        intervention=i2,
    )
    cases.transition(case, "RECOVERED", reason="MESSAGE recovered the payment")

    # --- historical D1 is intact and immutable-shaped -------------
    db.refresh(d1)
    assert d1.final_action == "RETRY" and d1.outcome.result == "NOT_RECOVERED"
    assert d2.final_action == "MESSAGE" and d2.outcome.result == "RECOVERED"

    # --- training examples: 2 cycles x 3 actions = 6 rows --------
    ter = TrainingExampleRepository(db)
    rows_d1 = ter.generate_for_decision_record(d1)
    rows_d2 = ter.generate_for_decision_record(d2)
    assert len(rows_d1) == 3 and len(rows_d2) == 3

    by_action_d1 = {r.action: r for r in rows_d1}
    assert by_action_d1["RETRY"].is_observed is True
    assert by_action_d1["RETRY"].outcome_label == "NOT_RECOVERED"
    assert by_action_d1["MESSAGE"].outcome_label is None      # unobserved
    assert by_action_d1["NO_ACTION"].outcome_label is None

    by_action_d2 = {r.action: r for r in rows_d2}
    assert by_action_d2["MESSAGE"].is_observed is True
    assert by_action_d2["MESSAGE"].outcome_label == "RECOVERED"
    assert by_action_d2["MESSAGE"].recovery_amount == Decimal("1000.00")

    # all 6 rows carry the same case grouping key + inherited arm + exact model
    all_rows = ter.list_for_case(case.id)
    assert len(all_rows) == 6
    assert {str(r.recovery_case_id) for r in all_rows} == {str(case.id)}
    assert all(r.experiment_arm == "TREATMENT" for r in all_rows)
    assert all(r.model_version_id == model.id for r in all_rows)

    # reproducible dataset snapshot id
    snap = ter.snapshot_id(all_rows)
    assert snap.startswith("tds-6-")
    assert ter.snapshot_id(list(reversed(all_rows))) == snap
