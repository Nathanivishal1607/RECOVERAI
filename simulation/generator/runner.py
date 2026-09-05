"""Simulation runner — persists synthetic data through the Phase 1B
repositories and writes hidden ground truth to a JSON sidecar.

No new DB models. Every domain invariant (RecoveryCase state machine,
append-only PaymentEvents, NO_ACTION creates no Intervention, immutable
decision cycles, case-level experiment assignment) goes through the
existing repository layer.
"""

from __future__ import annotations

import time
import uuid
from datetime import timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from backend.models import enums
from backend.repositories import (
    CustomerRepository,
    DecisionCycleRepository,
    ExperimentRepository,
    MerchantRepository,
    ModelVersionRepository,
    PaymentEventRepository,
    PaymentRepository,
    RecoveryCaseRepository,
    TrainingExampleRepository,
)
from simulation.config import SimConfig
from simulation.features import FEATURE_SCHEMA_ID, build_feature_snapshot
from simulation.generator.entities import (
    generate_customers,
    generate_merchants,
    generate_payment_specs,
)
from simulation.generator.naive_prior import naive_prior_probabilities
from simulation.generator.policy import control_policy, heuristic_policy
from simulation.ground_truth.potential_outcomes import (
    generate_potential_outcomes,
    oracle_best_action,
)
from simulation.ground_truth.store import CycleTruth, GroundTruthStore
from simulation.rng import stream

_ACTIONS = ("RETRY", "MESSAGE", "NO_ACTION")
_EXEC = ("RETRY", "MESSAGE")
_S = enums.RecoveryCaseStatus


class SimulationResult:
    def __init__(self) -> None:
        self.run_id = ""
        self.cases_created = 0
        self.cases_ineligible = 0
        self.decision_records = 0
        self.interventions = 0
        self.outcomes = 0
        self.training_examples = 0
        self.recovered = 0
        self.oracle_best_action_counts = {"RETRY": 0, "MESSAGE": 0, "NO_ACTION": 0}
        self.arm_counts = {"CONTROL": 0, "TREATMENT": 0, "NONE": 0}
        self.ground_truth_path = ""
        self.seconds = 0.0

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def run_simulation(db: Session, cfg: SimConfig) -> SimulationResult:
    t0 = time.perf_counter()
    res = SimulationResult()
    res.run_id = f"sim-{cfg.seed}-{cfg.n_cases}-{uuid.uuid4().hex[:8]}"
    gt = GroundTruthStore(res.run_id)

    mv_repo = ModelVersionRepository(db)
    model = mv_repo.create(
        model_role="recovery_prediction",
        model_name="sim-naive-prior",
        version=f"sim-{cfg.seed}",
        status=enums.ModelVersionStatus.VALIDATED.value,
        training_dataset_snapshot_id=f"{res.run_id}-none",
        feature_schema_id=FEATURE_SCHEMA_ID,
    )
    exp_repo = ExperimentRepository(db)
    experiment = exp_repo.create(
        name=f"sim-baseline-vs-heuristic-{res.run_id}",
        description="CONTROL = retry-once baseline; TREATMENT = observable heuristic",
        status=enums.ExperimentStatus.RUNNING.value,
    )

    merchants = generate_merchants(cfg)
    customers = generate_customers(cfg, merchants)
    payments = generate_payment_specs(cfg, merchants, customers)
    merch_by_ix = {m.index: m for m in merchants}
    cust_by_id = {c.customer_id: c for c in customers}

    m_repo, c_repo, p_repo = (
        MerchantRepository(db), CustomerRepository(db), PaymentRepository(db)
    )
    pe_repo = PaymentEventRepository(db)
    case_repo = RecoveryCaseRepository(db)
    dc_repo = DecisionCycleRepository(db)
    te_repo = TrainingExampleRepository(db)

    merchant_rows = {m.index: m_repo.create(name=m.display_name, industry=m.segment)
                     for m in merchants}
    for cu in customers:
        c_repo.create(
            customer_id=cu.customer_id,
            merchant_id=merchant_rows[cu.merchant_index].id,
            transaction_count=int(cu.payment_frequency_per_month * 6),
            successful_transactions=int(cu.payment_frequency_per_month * 6 * cu.hist_success_rate),
            failed_transactions=int(cu.payment_frequency_per_month * 6 * cu.hist_failure_rate),
            historical_recovery_rate=Decimal(str(cu.prev_recovery_rate)),
        )
    db.flush()

    for k, spec in enumerate(payments):
        m_spec, cu_spec = merch_by_ix[spec.merchant_index], cust_by_id[spec.customer_id]
        m_row = merchant_rows[spec.merchant_index]

        payment = p_repo.create(
            merchant_id=m_row.id, customer_id=spec.customer_id,
            amount=Decimal(str(spec.amount)), currency=spec.currency,
            status=enums.PaymentStatus.CREATED.value,
            external_payment_id=f"sim_pay_{k:07d}", payment_method=spec.method,
        )
        pe_repo.append(payment_id=payment.id,
                       event_type=enums.PaymentEventType.PAYMENT_CREATED.value,
                       event_timestamp=spec.created_at)
        payment_attempt = 1 + spec.initial_attempts
        pe_repo.append(payment_id=payment.id,
                       event_type=enums.PaymentEventType.PAYMENT_FAILED.value,
                       event_timestamp=spec.failed_at, attempt_number=payment_attempt,
                       metadata={"failure_code": spec.failure_code, "method": spec.method})
        p_repo.set_status(payment, enums.PaymentStatus.FAILED.value)

        if spec.amount < 20.0:  # recovery-eligibility gate (inputs present)
            res.cases_ineligible += 1
            if (k + 1) % 500 == 0:
                db.commit()
            continue

        case = case_repo.open_case(
            payment=payment, amount_at_risk=payment.amount,
            failure_category=spec.failure_category, failure_code=spec.failure_code,
            recovery_window_days=cfg.recovery_window_days,
            opened_at=spec.failed_at + timedelta(minutes=1),
        )
        res.cases_created += 1

        r_arm = stream(cfg.seed, "arm", k)
        arm = None
        if r_arm.random() < cfg.experiment_fraction:
            arm = (enums.ExperimentArm.CONTROL.value
                   if r_arm.random() < cfg.control_fraction_within_experiment
                   else enums.ExperimentArm.TREATMENT.value)
            exp_repo.assign(experiment_id=experiment.id, recovery_case_id=case.id,
                            arm=arm, assigned_at=case.opened_at)
        res.arm_counts[arm or "NONE"] += 1

        po_at_open = generate_potential_outcomes(
            cfg=cfg, merchant=m_spec, customer=cu_spec, payment=spec,
            attempt_number=1 + spec.initial_attempts,
        )
        best = oracle_best_action(po_at_open, cfg=cfg)
        res.oracle_best_action_counts[best] += 1
        case_gt = gt.setdefault_case(
            recovery_case_id=str(case.id), case_display_id=case.display_id,
            payment_amount=float(payment.amount),
            failure_category=spec.failure_category, experiment_arm=arm,
            oracle_best_action=best,
        )

        policy = (control_policy
                  if arm == enums.ExperimentArm.CONTROL.value else heuristic_policy)

        prior_actions: list[str] = []
        last_attempt_time = spec.failed_at
        decision_time = case.opened_at
        cycle_number = 0
        recovered = False
        terminalised = False

        while cycle_number < cfg.max_cycles and not recovered:
            cycle_number += 1
            case_repo.transition(case, _S.ANALYZING.value, occurred_at=decision_time)

            po = generate_potential_outcomes(
                cfg=cfg, merchant=m_spec, customer=cu_spec, payment=spec,
                attempt_number=payment_attempt,
            )
            snap = build_feature_snapshot(
                merchant=m_spec, customer=cu_spec, payment=spec,
                decision_time=decision_time, attempt_number=payment_attempt,
                last_attempt_time=last_attempt_time,
            )
            dr = dc_repo.open_cycle(
                case=case, payment_amount_at_decision=payment.amount,
                decision_timestamp=decision_time,
                decision_engine_version="sim-heuristic-v0",
            )
            res.decision_records += 1

            prior = naive_prior_probabilities(snap)
            if cfg.with_predictions:
                for a in _ACTIONS:
                    dc_repo.add_prediction(
                        decision_record=dr, action=a,
                        recovery_probability=Decimal(str(prior[a])),
                        model_version_id=model.id, feature_snapshot=snap,
                    )

            action = policy(
                cycle_number=cycle_number, prior_actions=prior_actions,
                failure_category=spec.failure_category, amount=spec.amount,
                max_cycles=cfg.max_cycles,
            )
            if action is None:
                dc_repo.finalize(decision_record=dr, recommended_action="NO_ACTION",
                                 final_action="NO_ACTION",
                                 decision_reason="policy stop (no cycle)")
                case_repo.transition(case, _S.STOPPED.value,
                                     reason="policy: no further action",
                                     occurred_at=decision_time)
                terminalised = True
                break

            vc = [{"action": a, "cost_used": cfg.cost_for(a),
                   "eirv_value": (round((prior[a] - prior["NO_ACTION"]) * spec.amount
                                        - cfg.cost_for(a), 2) if a != "NO_ACTION" else 0.0)}
                  for a in _ACTIONS]
            dc_repo.finalize(decision_record=dr, recommended_action=action,
                             final_action=action, decision_reason="sim heuristic",
                             value_context=vc)
            case_repo.transition(case, _S.ACTION_SELECTED.value, occurred_at=decision_time)

            clean_exposure, intervention = True, None
            if action in _EXEC:
                payment_attempt += 1
                pe_repo.append(payment_id=payment.id,
                               event_type=enums.PaymentEventType.RETRY_ATTEMPTED.value,
                               event_timestamp=decision_time, attempt_number=payment_attempt,
                               metadata={"triggered_by": action})
                r_exec = stream(cfg.seed, "exec", k, cycle_number)
                roll = r_exec.random()
                if roll < cfg.exec_reject_rate:
                    exec_status, clean_exposure = enums.ExecutionStatus.REJECTED.value, False
                elif roll < cfg.exec_reject_rate + cfg.exec_fail_rate:
                    exec_status, clean_exposure = enums.ExecutionStatus.FAILED.value, False
                else:
                    exec_status = enums.ExecutionStatus.ACCEPTED.value
                intervention = dc_repo.record_intervention(
                    decision_record=dr, action=action,
                    channel="SIMULATED" if action == "MESSAGE" else None,
                    execution_status=enums.ExecutionStatus.REQUESTED.value,
                    cost_incurred=Decimal(str(cfg.cost_for(action))),
                    requested_at=decision_time,
                )
                dc_repo.update_execution_status(
                    intervention, exec_status,
                    resolved_at=decision_time + timedelta(seconds=30),
                )
                res.interventions += 1
                last_attempt_time = decision_time

            case_repo.transition(case, _S.ACTION_EXECUTED.value, occurred_at=decision_time)
            case_repo.transition(case, _S.WAITING_FOR_OUTCOME.value, occurred_at=decision_time)

            r_out = stream(cfg.seed, "outcome", k, cycle_number)
            # Outcome-sampling ONLY. final_action stays `action` and the
            # TrainingExample still records observed_action=`action`; a
            # non-clean execution (REJECTED/FAILED) just means the world
            # evolved as if untreated, so we sample from P(recovery|NO_ACTION)
            # and that cycle yields no clean observed treatment label.
            effective_action = action if clean_exposure else "NO_ACTION"
            recovered = r_out.random() < po.probability(effective_action)

            window_end = case.expires_at
            if recovered:
                if r_out.random() < cfg.delayed_outcome_fraction:
                    span = max(3600.0, (window_end - decision_time).total_seconds())
                    observed_at = decision_time + timedelta(seconds=r_out.uniform(3600, span))
                else:
                    observed_at = decision_time + timedelta(minutes=r_out.randint(2, 55))
                amount_recovered = payment.amount
            else:
                remaining = max(1, cfg.max_cycles - cycle_number + 1)
                observed_at = decision_time + (window_end - decision_time) / remaining
                amount_recovered = Decimal("0")

            dc_repo.record_outcome(
                decision_record=dr,
                result=(enums.OutcomeResult.RECOVERED.value if recovered
                        else enums.OutcomeResult.NOT_RECOVERED.value),
                recovery_amount=amount_recovered, observed_at=observed_at,
                intervention=intervention,
            )
            res.outcomes += 1
            case_gt.cycles.append(CycleTruth(
                cycle_number=cycle_number, attempt_number=payment_attempt,
                observed_action=action, p_by_action=po.p_by_action, regime=po.regime,
                realised_recovered=recovered, realised_amount=float(amount_recovered),
                clean_exposure=clean_exposure,
            ))
            prior_actions.append(action)

            if recovered:
                pe_repo.append(payment_id=payment.id,
                               event_type=enums.PaymentEventType.PAYMENT_SUCCEEDED.value,
                               event_timestamp=observed_at, attempt_number=payment_attempt)
                p_repo.set_status(payment, enums.PaymentStatus.SUCCEEDED.value)
                case_repo.transition(case, _S.RECOVERED.value,
                                     reason=f"recovered via {action}", occurred_at=observed_at)
                res.recovered += 1
                terminalised = True
                break

            if cycle_number >= cfg.max_cycles:
                # exhausted the recovery window without recovering
                case_repo.transition(case, _S.EXPIRED.value,
                                     reason="recovery window elapsed",
                                     occurred_at=observed_at)
                terminalised = True
                break
            next_action = policy(
                cycle_number=cycle_number + 1, prior_actions=prior_actions,
                failure_category=spec.failure_category, amount=spec.amount,
                max_cycles=cfg.max_cycles,
            )
            if next_action is None:
                case_repo.transition(case, _S.STOPPED.value, reason="policy stop",
                                     occurred_at=observed_at)
                terminalised = True
                break

            # re-evaluate: bump the clock and loop. The loop head does the
            # WAITING_FOR_OUTCOME -> ANALYZING transition (a valid edge).
            decision_time = observed_at + timedelta(minutes=r_out.randint(30, 240))
        # end while

        if not terminalised:
            # safety net: exhausted cycles without an explicit terminal
            case_repo.transition(case, _S.EXPIRED.value, reason="cycles exhausted",
                                 occurred_at=decision_time)

        if cfg.with_predictions:
            for drow in dc_repo.cycles_for_case(case.id):
                res.training_examples += len(te_repo.generate_for_decision_record(drow))

        if (k + 1) % 250 == 0:
            db.commit()

    db.commit()
    gt.save()
    res.ground_truth_path = str(gt.path)
    res.seconds = round(time.perf_counter() - t0, 3)
    return res
