"""Phase 2 — outcomes, decision cycles and Phase 1B contract compatibility."""

from __future__ import annotations

from dataclasses import replace

from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from backend.models import (
    Customer,
    DecisionRecord,
    ExperimentAssignment,
    Intervention,
    Merchant,
    Outcome,
    Payment,
    PaymentEvent,
    Prediction,
    RecoveryCase,
    TrainingExample,
)
from backend.models import enums
from backend.repositories import TrainingExampleRepository
from simulation.config import SimConfig
from simulation.features import FEATURE_SCHEMA_ID
from simulation.generator.runner import run_simulation

from .conftest import _engine

_EXEC = {enums.Action.RETRY.value, enums.Action.MESSAGE.value}


# --- outcomes ---------------------------------------------------------

def test_only_selected_action_gets_an_observed_outcome(sim_run):
    db = sim_run["db"]
    drs = list(db.scalars(select(DecisionRecord)))
    assert drs

    for dr in drs:
        rows = list(
            db.scalars(
                select(TrainingExample).where(
                    TrainingExample.decision_record_id == dr.id
                )
            )
        )
        if not rows:
            continue  # cycle whose case ended FAILED — no labels by contract
        observed = [r for r in rows if r.is_observed]
        assert len(observed) <= 1
        for r in rows:
            # label exists only on the observed action
            assert (r.outcome_label is not None) == r.is_observed
            # is_observed only ever on the action that actually happened
            assert not r.is_observed or r.action == r.observed_action
        if observed:
            assert observed[0].action == dr.final_action


def test_outcome_attaches_to_decision_record_and_result_vocab(sim_run):
    db = sim_run["db"]
    outcomes = list(db.scalars(select(Outcome)))
    assert outcomes
    for o in outcomes:
        assert o.decision_record_id is not None
        assert o.result in enums.values(enums.OutcomeResult)
        if o.result == enums.OutcomeResult.NOT_RECOVERED.value:
            assert o.recovery_amount == 0
        else:
            assert o.recovery_amount > 0


def test_not_recovered_outcomes_exist(sim_run):
    db = sim_run["db"]
    n = db.scalar(
        select(func.count()).select_from(Outcome).where(
            Outcome.result == enums.OutcomeResult.NOT_RECOVERED.value
        )
    )
    assert n >= 1


def test_delayed_outcomes_exist(sim_run):
    db = sim_run["db"]
    pairs = db.execute(
        select(Outcome.observed_at, DecisionRecord.decision_timestamp).join(
            DecisionRecord, DecisionRecord.id == Outcome.decision_record_id
        )
    ).all()
    assert pairs
    delayed = [
        1
        for observed_at, decided_at in pairs
        if (observed_at - decided_at).total_seconds() > 3600
    ]
    assert delayed, "expected at least one outcome observed >1h after the decision"


def test_recovery_amount_matches_payment_when_recovered(sim_run):
    db = sim_run["db"]
    q = (
        select(Outcome.recovery_amount, Payment.amount)
        .join(DecisionRecord, DecisionRecord.id == Outcome.decision_record_id)
        .join(RecoveryCase, RecoveryCase.id == DecisionRecord.recovery_case_id)
        .join(Payment, Payment.id == RecoveryCase.payment_id)
        .where(Outcome.result == enums.OutcomeResult.RECOVERED.value)
    )
    rows = db.execute(q).all()
    assert rows
    for recovered_amount, payment_amount in rows:
        assert recovered_amount == payment_amount


# --- decision cycles ------------------------------------------------

def test_multiple_decision_cycles_are_recorded(sim_run):
    db = sim_run["db"]
    counts = db.execute(
        select(DecisionRecord.recovery_case_id, func.count())
        .group_by(DecisionRecord.recovery_case_id)
    ).all()
    assert counts
    multi = [c for _, c in counts if c > 1]
    assert multi, "expected at least one case with more than one decision cycle"

    # cycle numbers within a case are the contiguous sequence 1..n, unique
    for case_id, n in counts:
        cycles = sorted(
            db.scalars(
                select(DecisionRecord.cycle_number).where(
                    DecisionRecord.recovery_case_id == case_id
                )
            )
        )
        assert cycles == list(range(1, n + 1))


def test_no_action_creates_no_intervention(sim_run):
    db = sim_run["db"]
    no_action_drs = set(
        db.scalars(
            select(DecisionRecord.id).where(
                DecisionRecord.final_action == enums.Action.NO_ACTION.value
            )
        )
    )
    assert no_action_drs  # the sim does choose NO_ACTION sometimes
    intervened = set(
        db.scalars(
            select(Intervention.decision_record_id).where(
                Intervention.decision_record_id.in_(no_action_drs)
            )
        )
    )
    assert not intervened


def test_interventions_are_retry_or_message_only(sim_run):
    db = sim_run["db"]
    actions = set(db.scalars(select(Intervention.action)))
    assert actions
    assert actions <= _EXEC
    statuses = set(db.scalars(select(Intervention.execution_status)))
    assert statuses <= set(enums.values(enums.ExecutionStatus))
    assert "SUCCEEDED" not in statuses


def test_rejected_or_failed_execution_is_not_a_clean_label():
    """A non-ACCEPTED execution must not produce an observed training label."""
    cfg = replace(
        SimConfig(seed=11),
        n_cases=120,
        customers_per_merchant=60,
        exec_reject_rate=0.4,
        exec_fail_rate=0.3,
    )
    eng = _engine()
    db = sessionmaker(bind=eng, future=True, expire_on_commit=False)()
    try:
        res = run_simulation(db, cfg)
        dirty = list(
            db.scalars(
                select(Intervention).where(
                    Intervention.execution_status.in_(
                        [
                            enums.ExecutionStatus.REJECTED.value,
                            enums.ExecutionStatus.FAILED.value,
                        ]
                    )
                )
            )
        )
        assert dirty, "high reject/fail rates should produce non-clean executions"
        for iv in dirty:
            rows = list(
                db.scalars(
                    select(TrainingExample).where(
                        TrainingExample.decision_record_id == iv.decision_record_id
                    )
                )
            )
            # if the cycle produced training rows at all, none is observed and
            # none carries a label (failed execution != clean treatment)
            for r in rows:
                assert not r.is_observed
                assert r.outcome_label is None
    finally:
        from pathlib import Path

        Path(res.ground_truth_path).unlink(missing_ok=True)
        db.close()
        eng.dispose()


def test_historical_decision_records_are_immutable(sim_run):
    """Each cycle is a fresh DecisionRecord; (case, cycle_number) is unique and
    re-deriving training rows is idempotent (no rewrite of history)."""
    db = sim_run["db"]
    seen = set()
    for case_id, cyc in db.execute(
        select(DecisionRecord.recovery_case_id, DecisionRecord.cycle_number)
    ):
        assert (case_id, cyc) not in seen
        seen.add((case_id, cyc))

    te_repo = TrainingExampleRepository(db)
    before = db.scalar(select(func.count()).select_from(TrainingExample))
    for dr in db.scalars(select(DecisionRecord)):
        te_repo.generate_for_decision_record(dr)
    after = db.scalar(select(func.count()).select_from(TrainingExample))
    assert before == after


# --- Phase 1B contract compatibility ------------------------------

def test_generated_data_populates_the_phase1b_tables(sim_run):
    db = sim_run["db"]
    for model in (
        Merchant, Customer, Payment, PaymentEvent, RecoveryCase,
        DecisionRecord, Prediction, Intervention, Outcome,
        ExperimentAssignment, TrainingExample,
    ):
        assert db.scalar(select(func.count()).select_from(model)) > 0, model.__name__


def test_training_examples_are_contract_valid(sim_run):
    db = sim_run["db"]
    rows = list(db.scalars(select(TrainingExample)))
    assert rows
    per_dr: dict = {}
    for r in rows:
        per_dr.setdefault(r.decision_record_id, []).append(r)
        assert r.action in enums.values(enums.Action)
        assert r.feature_snapshot["_feature_schema_id"] == FEATURE_SCHEMA_ID
        assert (r.outcome_label is None) or r.is_observed
        assert (not r.is_observed) or (r.action == r.observed_action)
    # one row per candidate action per decision record
    for dr_rows in per_dr.values():
        assert sorted(r.action for r in dr_rows) == sorted(enums.values(enums.Action))


def test_experiment_assignment_is_case_level_and_single_arm(sim_run):
    db = sim_run["db"]
    assignments = list(db.scalars(select(ExperimentAssignment)))
    assert assignments
    per_case: dict = {}
    for a in assignments:
        per_case.setdefault(a.recovery_case_id, set()).add(a.arm)
    for arms in per_case.values():
        assert len(arms) == 1
        assert arms <= {
            enums.ExperimentArm.CONTROL.value,
            enums.ExperimentArm.TREATMENT.value,
        }
