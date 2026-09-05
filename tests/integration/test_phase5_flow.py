"""Phase 5 — the end-to-end recovery flow service.

    ingest failed payment -> eligibility -> RecoveryCase -> DecisionEngine
      -> Predictions x3 -> EIRV -> recommendation -> PolicyEvaluation
      -> final_action -> Intervention (RETRY/MESSAGE only) -> mock execute
      -> Outcome (attached to the right cycle) -> RecoveryCase terminal
      -> TrainingExample x (cycles x 3)

Every assertion here is about the *flow* preserving the finalized Phase
1A/1B contracts, not about which action the model happens to pick.
"""

from __future__ import annotations

import ast
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from backend.models import Base, enums
from backend.models.core_entities import Customer, RecoveryCase
from backend.models.decision import DecisionRecord
from backend.repositories.core import CustomerRepository, MerchantRepository
from backend.repositories.governance import ModelVersionRepository, PolicyRepository
from backend.repositories.decision import DecisionCycleRepository
from backend.repositories.training import TrainingExampleRepository
from backend.services import recovery_flow as flow
from backend.services.model_provider import get_promoted_model
from ml.inference.recovery import clear_cache
from ml.training.uplift import MODEL_ROLE, train_uplift_model
from simulation.config import SimConfig
from simulation.generator.runner import run_simulation
from simulation.scenarios.demo_cases import run_all as run_demo_scenarios

T0 = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)


# ------------------------------------------------------------------ fixtures


@pytest.fixture(scope="module")
def _promoted_db(tmp_path_factory):
    """One heavy setup for the whole module: a 1200-case seed-42 simulator
    run (the canonical demo config, so demo scenarios A-E are deterministic
    against this exact model) + a promoted T-learner. Each test creates its
    own merchant / customer / payment so committed rows never collide."""
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False})

    @event.listens_for(eng, "connect")
    def _fk(c, _):  # noqa: ANN001
        cur = c.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(eng)
    Session = sessionmaker(bind=eng, future=True, expire_on_commit=False)
    db = Session()
    clear_cache()
    art = tmp_path_factory.mktemp("p5art")

    run_simulation(
        db, replace(SimConfig(seed=42), n_cases=1200, customers_per_merchant=250)
    )
    tr = train_uplift_model(
        db, kind="t_learner", version="p5-flow", seed=42, artifact_dir=art
    )
    repo = ModelVersionRepository(db)
    repo.transition_status(tr.model_version, enums.ModelVersionStatus.VALIDATED.value)
    repo.transition_status(tr.model_version, enums.ModelVersionStatus.PROMOTED.value)
    db.commit()
    try:
        yield Session
    finally:
        db.close()
        eng.dispose()
        clear_cache()


@pytest.fixture()
def flow_env(_promoted_db, request):
    """A fresh session + a unique merchant/customer/policy per test."""
    db = _promoted_db()
    tag = request.node.name[-8:]
    merchant = MerchantRepository(db).create(name=f"FlowCo-{tag}", industry="ecommerce")
    CustomerRepository(db).create(
        customer_id=f"C-{tag}",
        merchant_id=merchant.id,
        transaction_count=40,
        successful_transactions=24,
        failed_transactions=16,
        historical_recovery_rate=Decimal("0.3"),
    )
    policy = PolicyRepository(db).create_version(
        policy_id=f"POL-{merchant.display_id}",
        policy_version="v1",
        merchant_id=merchant.id,
        max_retry_count=3,
        max_customer_contacts=3,
        allowed_interventions=["RETRY", "MESSAGE"],
    )
    db.commit()
    try:
        yield {"db": db, "merchant": merchant, "policy": policy, "cust": f"C-{tag}"}
    finally:
        db.close()


def _failed_payment(db, merchant, *, amount, category, code, at=T0, customer_id=None):
    return flow.ingest_failed_payment(
        db,
        merchant_id=merchant.id,
        customer_id=customer_id or _cust_of(merchant),
        amount=Decimal(str(amount)),
        currency="INR",
        payment_method="CARD",
        failure_category=category,
        failure_code=code,
        created_at=at,
    )


def _cust_of(merchant) -> str:
    # merchant name is FlowCo-<tag>; customer is C-<tag>
    return "C-" + merchant.name.split("-", 1)[1]


# ---------------------------------------------------------------------- tests


def test_ingest_creates_payment_and_append_only_events(flow_env):
    db, m = flow_env["db"], flow_env["merchant"]
    pay = _failed_payment(db, m, amount=1200, category="TEMPORARY", code="SIM_GATEWAY_TIMEOUT")
    db.commit()
    assert pay.status == enums.PaymentStatus.FAILED.value
    from backend.repositories.core import PaymentEventRepository

    events = PaymentEventRepository(db).list_for_payment(pay.id)
    assert [e.event_type for e in events] == ["PAYMENT_CREATED", "PAYMENT_FAILED"]


def test_ineligible_payment_below_floor(flow_env):
    db, m = flow_env["db"], flow_env["merchant"]
    pay = _failed_payment(db, m, amount=5, category="TEMPORARY", code="SIM_GATEWAY_TIMEOUT")
    db.commit()
    elig = flow.check_eligibility(db, pay)
    assert not elig.eligible
    with pytest.raises(flow.RecoveryFlowError):
        flow.evaluate_recovery(db, payment=pay, policy=flow_env["policy"])


def test_happy_path_end_to_end(flow_env):
    db, m, pol = flow_env["db"], flow_env["merchant"], flow_env["policy"]
    pay = _failed_payment(db, m, amount=2500, category="TEMPORARY", code="SIM_GATEWAY_TIMEOUT")
    # a couple of prior attempts -> RETRY is clearly best
    from backend.repositories.core import PaymentEventRepository

    pe = PaymentEventRepository(db)
    for i in range(3):
        pe.append(payment_id=pay.id, event_type="RETRY_ATTEMPTED",
                  event_timestamp=T0 + timedelta(minutes=10 + i), attempt_number=2 + i)
        pe.append(payment_id=pay.id, event_type="PAYMENT_FAILED",
                  event_timestamp=T0 + timedelta(minutes=11 + i), attempt_number=2 + i,
                  metadata={"failure_code": "SIM_GATEWAY_TIMEOUT",
                            "failure_category": "TEMPORARY"})
    db.flush()

    res = flow.evaluate_recovery(
        db, payment=pay, policy=pol, decision_time=T0 + timedelta(minutes=30)
    )
    db.commit()
    dr = res.decision.decision_record
    assert dr.cycle_number == 1
    assert {p.action for p in dr.predictions} == {"RETRY", "MESSAGE", "NO_ACTION"}
    mv_ids = {p.model_version_id for p in dr.predictions}
    assert len(mv_ids) == 1  # all three Predictions share one exact ModelVersion
    assert dr.value_context[-1]["action"] == "NO_ACTION"
    assert dr.value_context[-1]["eirv_value"] == 0.0

    if res.decision.intervention_created:
        assert res.decision.final_action in enums.EXECUTABLE_ACTIONS
        assert dr.intervention.action == res.decision.final_action
        assert dr.intervention.execution_status == enums.ExecutionStatus.REQUESTED.value
        flow.execute_decision(db, decision_record_id=dr.id)
        db.commit()
        db.refresh(dr.intervention)
        assert dr.intervention.execution_status == enums.ExecutionStatus.ACCEPTED.value

        flow.record_outcome(
            db, decision_record_id=dr.id, result="RECOVERED",
            recovery_amount=Decimal("2500.00"),
            observed_at=T0 + timedelta(hours=2),
        )
        db.commit()
        case = db.get(RecoveryCase, res.case.id)
        assert case.status == enums.RecoveryCaseStatus.RECOVERED.value
        # payment marked SUCCEEDED + PAYMENT_SUCCEEDED event appended
        from backend.repositories.core import PaymentEventRepository as PER

        assert any(
            e.event_type == "PAYMENT_SUCCEEDED"
            for e in PER(db).list_for_payment(pay.id)
        )
        # TrainingExamples: 3 per cycle, label only on the observed action
        rows = TrainingExampleRepository(db).list_for_case(case.id)
        assert len(rows) == 3
        observed = [r for r in rows if r.is_observed]
        assert len(observed) == 1
        assert observed[0].action == dr.final_action
        assert observed[0].outcome_label == "RECOVERED"
        assert all(r.outcome_label is None for r in rows if not r.is_observed)


def test_no_action_first_cycle_stops_case_without_intervention(flow_env):
    db, m, pol = flow_env["db"], flow_env["merchant"], flow_env["policy"]
    # very reliable customer + tiny amount -> NO_ACTION
    c = db.get(Customer, flow_env["cust"])
    c.transaction_count, c.successful_transactions, c.failed_transactions = 60, 58, 2
    c.historical_recovery_rate = Decimal("0.92")
    db.flush()
    pay = _failed_payment(db, m, amount=90, category="TEMPORARY", code="SIM_GATEWAY_TIMEOUT")
    res = flow.evaluate_recovery(
        db, payment=pay, policy=pol, decision_time=T0 + timedelta(minutes=5)
    )
    db.commit()
    assert res.decision.final_action == "NO_ACTION"
    assert res.decision.intervention_created is False
    assert res.decision.decision_record.intervention is None
    case = db.get(RecoveryCase, res.case.id)
    assert case.status == enums.RecoveryCaseStatus.STOPPED.value
    assert res.stopped_early is True


def test_policy_veto_forces_final_action_to_differ(flow_env):
    """A policy that blocks BOTH executable actions must force
    ``final_action`` to NO_ACTION regardless of the recommendation, with a
    BLOCKED PolicyEvaluation on the recommended action and no Intervention."""
    db, m = flow_env["db"], flow_env["merchant"]
    blocked_pol = PolicyRepository(db).create_version(
        policy_id=f"BLK-{m.display_id}", policy_version="v1", merchant_id=m.id,
        max_retry_count=0, max_customer_contacts=0,
        allowed_interventions=[], make_active=False,
    )
    pay = _failed_payment(db, m, amount=2500, category="TEMPORARY", code="SIM_GATEWAY_TIMEOUT")
    from backend.repositories.core import PaymentEventRepository

    pe = PaymentEventRepository(db)
    for i in range(3):
        pe.append(payment_id=pay.id, event_type="RETRY_ATTEMPTED",
                  event_timestamp=T0 + timedelta(minutes=10 + i), attempt_number=2 + i)
        pe.append(payment_id=pay.id, event_type="PAYMENT_FAILED",
                  event_timestamp=T0 + timedelta(minutes=11 + i), attempt_number=2 + i,
                  metadata={"failure_code": "SIM_GATEWAY_TIMEOUT",
                            "failure_category": "TEMPORARY"})
    db.flush()
    res = flow.evaluate_recovery(
        db, payment=pay, policy=blocked_pol, decision_time=T0 + timedelta(minutes=30)
    )
    db.commit()
    dr = res.decision.decision_record
    if dr.recommended_action == "NO_ACTION":
        pytest.skip("model recommended NO_ACTION for this profile — no veto to test")
    assert dr.recommended_action in enums.EXECUTABLE_ACTIONS
    assert dr.final_action == "NO_ACTION"
    assert dr.final_action != dr.recommended_action
    blocked = [pe for pe in dr.policy_evaluations if pe.result == "BLOCKED"]
    assert any(pe.action == dr.recommended_action for pe in blocked)
    assert dr.intervention is None


def test_reevaluation_creates_new_record_and_leaves_cycle_one_untouched(flow_env):
    db, m, pol = flow_env["db"], flow_env["merchant"], flow_env["policy"]
    pay = _failed_payment(db, m, amount=2500, category="TEMPORARY", code="SIM_GATEWAY_TIMEOUT")
    from backend.repositories.core import PaymentEventRepository

    pe = PaymentEventRepository(db)
    for i in range(3):
        pe.append(payment_id=pay.id, event_type="RETRY_ATTEMPTED",
                  event_timestamp=T0 + timedelta(minutes=10 + i), attempt_number=2 + i)
        pe.append(payment_id=pay.id, event_type="PAYMENT_FAILED",
                  event_timestamp=T0 + timedelta(minutes=11 + i), attempt_number=2 + i,
                  metadata={"failure_code": "SIM_GATEWAY_TIMEOUT",
                            "failure_category": "TEMPORARY"})
    db.flush()

    res1 = flow.evaluate_recovery(
        db, payment=pay, policy=pol, decision_time=T0 + timedelta(minutes=30)
    )
    db.commit()
    dr1 = res1.decision.decision_record
    dr1_id = dr1.id
    assert res1.decision.intervention_created is True

    snap = _dr_snapshot(dr1)

    flow.execute_decision(db, decision_record_id=dr1_id)
    flow.record_outcome(
        db, decision_record_id=dr1_id, result="NOT_RECOVERED",
        observed_at=T0 + timedelta(hours=2),
    )
    db.commit()

    case = db.get(RecoveryCase, res1.case.id)
    res2 = flow.reevaluate(
        db, case=case, policy=pol, decision_time=T0 + timedelta(hours=4)
    )
    db.commit()
    dr2 = res2.decision.decision_record
    assert dr2.id != dr1_id
    assert dr2.cycle_number == 2

    db.refresh(dr1)
    assert _dr_snapshot(dr1) == snap, "cycle-1 DecisionRecord changed after re-evaluation"
    # cycle-1 outcome is still its own
    assert dr1.outcome is not None and dr1.outcome.result == "NOT_RECOVERED"
    assert dr2.outcome is None


def test_outcome_attaches_to_the_correct_cycle(flow_env):
    db, m, pol = flow_env["db"], flow_env["merchant"], flow_env["policy"]
    pay = _failed_payment(db, m, amount=2500, category="TEMPORARY", code="SIM_GATEWAY_TIMEOUT")
    from backend.repositories.core import PaymentEventRepository

    pe = PaymentEventRepository(db)
    for i in range(3):
        pe.append(payment_id=pay.id, event_type="RETRY_ATTEMPTED",
                  event_timestamp=T0 + timedelta(minutes=10 + i), attempt_number=2 + i)
        pe.append(payment_id=pay.id, event_type="PAYMENT_FAILED",
                  event_timestamp=T0 + timedelta(minutes=11 + i), attempt_number=2 + i,
                  metadata={"failure_code": "SIM_GATEWAY_TIMEOUT",
                            "failure_category": "TEMPORARY"})
    db.flush()

    res1 = flow.evaluate_recovery(
        db, payment=pay, policy=pol, decision_time=T0 + timedelta(minutes=30)
    )
    db.commit()
    dr1 = res1.decision.decision_record
    flow.execute_decision(db, decision_record_id=dr1.id)
    flow.record_outcome(
        db, decision_record_id=dr1.id, result="NOT_RECOVERED",
        observed_at=T0 + timedelta(hours=2),
    )
    db.commit()

    case = db.get(RecoveryCase, res1.case.id)
    res2 = flow.reevaluate(db, case=case, policy=pol, decision_time=T0 + timedelta(hours=4))
    db.commit()
    dr2 = res2.decision.decision_record
    if res2.decision.intervention_created:
        flow.execute_decision(db, decision_record_id=dr2.id)
    flow.record_outcome(
        db, decision_record_id=dr2.id, result="RECOVERED",
        recovery_amount=Decimal("2500.00"), observed_at=T0 + timedelta(hours=5),
    )
    db.commit()

    db.refresh(dr1)
    db.refresh(dr2)
    assert dr1.outcome.result == "NOT_RECOVERED"
    assert dr2.outcome.result == "RECOVERED"
    assert dr1.outcome.decision_record_id == dr1.id
    assert dr2.outcome.decision_record_id == dr2.id


def test_training_examples_contract_valid_across_cycles(flow_env):
    db, m, pol = flow_env["db"], flow_env["merchant"], flow_env["policy"]
    pay = _failed_payment(db, m, amount=2500, category="TEMPORARY", code="SIM_GATEWAY_TIMEOUT")
    from backend.repositories.core import PaymentEventRepository

    pe = PaymentEventRepository(db)
    for i in range(3):
        pe.append(payment_id=pay.id, event_type="RETRY_ATTEMPTED",
                  event_timestamp=T0 + timedelta(minutes=10 + i), attempt_number=2 + i)
        pe.append(payment_id=pay.id, event_type="PAYMENT_FAILED",
                  event_timestamp=T0 + timedelta(minutes=11 + i), attempt_number=2 + i,
                  metadata={"failure_code": "SIM_GATEWAY_TIMEOUT",
                            "failure_category": "TEMPORARY"})
    db.flush()

    res1 = flow.evaluate_recovery(
        db, payment=pay, policy=pol, decision_time=T0 + timedelta(minutes=30)
    )
    db.commit()
    dr1 = res1.decision.decision_record
    flow.execute_decision(db, decision_record_id=dr1.id)
    flow.record_outcome(
        db, decision_record_id=dr1.id, result="NOT_RECOVERED",
        observed_at=T0 + timedelta(hours=2),
    )
    db.commit()
    case = db.get(RecoveryCase, res1.case.id)
    res2 = flow.reevaluate(db, case=case, policy=pol, decision_time=T0 + timedelta(hours=4))
    db.commit()
    dr2 = res2.decision.decision_record
    if res2.decision.intervention_created:
        flow.execute_decision(db, decision_record_id=dr2.id)
    flow.record_outcome(
        db, decision_record_id=dr2.id, result="RECOVERED",
        recovery_amount=Decimal("2500.00"), observed_at=T0 + timedelta(hours=5),
    )
    db.commit()

    rows = TrainingExampleRepository(db).list_for_case(case.id)
    assert len(rows) == 6  # 2 cycles x 3 candidate actions
    assert {str(r.recovery_case_id) for r in rows} == {str(case.id)}
    by_dr = {}
    for r in rows:
        by_dr.setdefault(r.decision_record_id, []).append(r)
    for dr_rows in by_dr.values():
        assert len(dr_rows) == 3
        assert sum(1 for r in dr_rows if r.is_observed) == 1
        for r in dr_rows:
            if r.is_observed:
                assert r.outcome_label in ("RECOVERED", "NOT_RECOVERED")
            else:
                assert r.outcome_label is None  # no counterfactual labels


def test_demo_scenarios_all_pass(flow_env):
    """The five demo scenarios (A-E) run end-to-end and meet their
    expectations on a freshly promoted model."""
    db = flow_env["db"]
    results = run_demo_scenarios(db, get_promoted_model(db))
    failed = {r.key: r.notes for r in results if not r.ok}
    assert not failed, f"demo scenarios failed: {failed}"
    keys = {r.key: r for r in results}
    assert keys["A"].recommended_action == "RETRY"
    assert keys["B"].recommended_action == "MESSAGE"
    assert keys["C"].recommended_action == "NO_ACTION"
    assert keys["D"].was_blocked is True
    assert keys["D"].final_action != keys["D"].recommended_action


# --------------------------------------------------------- leakage guard


def test_services_do_not_import_simulator_ground_truth():
    """``backend/services/`` and ``backend/api/`` must never import the
    simulator's hidden ground truth or the evaluation package."""
    root = Path(__file__).resolve().parents[2]
    forbidden = ("simulation.ground_truth", "simulation.evaluation")
    offenders: list[str] = []
    for pkg in ("backend/services", "backend/api"):
        for path in (root / pkg).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                else:
                    continue
                for name in names:
                    if any(name == f or name.startswith(f + ".") for f in forbidden):
                        offenders.append(f"{path.relative_to(root)} -> {name}")
    assert not offenders, f"forbidden imports: {offenders}"


# ----------------------------------------------------------------- helpers


def _dr_snapshot(dr: DecisionRecord) -> tuple:
    preds = tuple(
        (p.action, str(p.recovery_probability), str(p.model_version_id))
        for p in sorted(dr.predictions, key=lambda x: x.action)
    )
    pol = tuple(
        (pe.action, pe.result, pe.reason_code)
        for pe in sorted(dr.policy_evaluations, key=lambda x: x.action)
    )
    return (dr.cycle_number, dr.recommended_action, dr.final_action,
            dr.decision_reason, preds, pol)
