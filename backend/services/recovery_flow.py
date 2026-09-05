"""Phase 5 — the single end-to-end recovery flow.

    PaymentEvent(PAYMENT_FAILED)
      -> recovery eligibility
      -> RecoveryCase (OPEN -> ANALYZING)
      -> DecisionEngine.run_cycle       (Predictions x3 -> EIRV -> recommendation
                                         -> PolicyEvaluation veto loop -> final_action)
      -> Intervention                   (RETRY/MESSAGE only)
      -> mock execution                 (execution_status; no real provider)
      -> Outcome                        (attached to THIS cycle)
      -> RecoveryCase terminal state    (RECOVERED / STOPPED / EXPIRED)
      -> TrainingExample x (cycles x 3) (label only on the observed action)

Every write goes through the existing Phase 1B repositories and the
Phase 3 ``DecisionEngine``. This service adds NO new persistence entity
and changes NO Phase 1A/1B contract. It only *orchestrates*: case state
machine, PolicyContext assembly, stopping rules, and TrainingExample
derivation on terminal cases.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.database.base import utcnow
from backend.decision_engine.orchestrator import (
    DecisionEngine,
    DecisionEngineConfig,
    DecisionOutcome,
)
from backend.models import enums
from backend.models.core_entities import Payment, RecoveryCase
from backend.models.decision import DecisionRecord, Intervention
from backend.models.governance import Policy
from backend.policies.engine import PolicyContext
from backend.repositories.core import (
    PaymentEventRepository,
    PaymentRepository,
    RecoveryCaseRepository,
)
from backend.repositories.decision import DecisionCycleRepository
from backend.repositories.governance import (
    ExperimentRepository,
    ModelVersionRepository,
    PolicyRepository,
)
from backend.repositories.training import TrainingExampleRepository
from backend.services.feature_snapshot import build_case_feature_snapshot
from backend.services.model_provider import PromotedModel, get_promoted_model
from ml.inference.recovery import load_for_model_version

_S = enums.RecoveryCaseStatus
_ACTION = enums.Action
_EXEC = enums.ExecutionStatus
_EXECUTABLE = frozenset(enums.EXECUTABLE_ACTIONS)

#: Recovery-eligibility floor — a payment below this is not worth a case
#: (mirrors the simulator's ``spec.amount < 20.0`` gate).
MIN_ELIGIBLE_AMOUNT = 20.0


def _aware(dt: datetime | None) -> datetime | None:
    """SQLite round-trips tz-aware timestamps as naive UTC — normalise so
    comparisons/arithmetic never mix naive and aware."""
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


class RecoveryFlowError(RuntimeError):
    """A flow precondition failed (ineligible payment, terminal case, ...)."""


@dataclass
class EligibilityResult:
    eligible: bool
    reason: str


@dataclass
class EvaluationResult:
    case: RecoveryCase
    decision: DecisionOutcome
    cycle_number: int
    case_status: str
    stopped_early: bool = False
    stop_reason: str | None = None


# --------------------------------------------------------------------- ingest


def ingest_failed_payment(
    db: Session,
    *,
    merchant_id,
    customer_id: str,
    amount: Decimal | float | str,
    currency: str = "INR",
    payment_method: str | None = None,
    failure_category: str | None = None,
    failure_code: str | None = None,
    external_payment_id: str | None = None,
    created_at: datetime | None = None,
    failed_at: datetime | None = None,
) -> Payment:
    """Create a Payment and append its PAYMENT_CREATED + PAYMENT_FAILED
    events (append-only). Sets the denormalized status to FAILED. Does NOT
    open a case — call :func:`evaluate_recovery` for that."""
    now = created_at or utcnow()
    failed = failed_at or (now + timedelta(minutes=1))
    p_repo = PaymentRepository(db)
    pe_repo = PaymentEventRepository(db)

    payment = p_repo.create(
        merchant_id=merchant_id,
        customer_id=customer_id,
        amount=Decimal(str(amount)),
        currency=currency,
        status=enums.PaymentStatus.CREATED.value,
        external_payment_id=external_payment_id,
        payment_method=payment_method,
    )
    pe_repo.append(
        payment_id=payment.id,
        event_type=enums.PaymentEventType.PAYMENT_CREATED.value,
        event_timestamp=now,
    )
    pe_repo.append(
        payment_id=payment.id,
        event_type=enums.PaymentEventType.PAYMENT_FAILED.value,
        event_timestamp=failed,
        attempt_number=1,
        metadata={"failure_code": failure_code, "failure_category": failure_category},
    )
    p_repo.set_status(payment, enums.PaymentStatus.FAILED.value)
    db.flush()
    return payment


# ---------------------------------------------------------------- eligibility


def check_eligibility(db: Session, payment: Payment) -> EligibilityResult:
    """Recovery-eligibility gate. Observable inputs only."""
    if payment.status != enums.PaymentStatus.FAILED.value:
        return EligibilityResult(False, f"payment status is {payment.status}, not FAILED")
    if float(payment.amount) < MIN_ELIGIBLE_AMOUNT:
        return EligibilityResult(
            False, f"amount {payment.amount} below eligibility floor {MIN_ELIGIBLE_AMOUNT}"
        )
    existing = RecoveryCaseRepository(db).active_for_payment(payment.id)
    if existing is not None:
        return EligibilityResult(
            True, f"active RecoveryCase {existing.display_id} already open"
        )
    # already terminally handled?
    terminal = db.scalar(
        select(RecoveryCase).where(
            RecoveryCase.payment_id == payment.id,
            RecoveryCase.status.in_(list(enums.TERMINAL_CASE_STATUSES)),
        )
    )
    if terminal is not None and terminal.status == _S.RECOVERED.value:
        return EligibilityResult(False, "payment already recovered")
    return EligibilityResult(True, "eligible")


# ------------------------------------------------------------ policy context


def build_policy_context(
    db: Session, *, case: RecoveryCase, policy: Policy
) -> PolicyContext:
    """Count prior RETRY attempts and prior customer contacts (MESSAGE
    interventions inside the contact window) from this case's own decision
    history. Deterministic; observable only."""
    dc = DecisionCycleRepository(db)
    cycles = dc.cycles_for_case(case.id)
    retry_attempts = 0
    contacts = 0
    window_start = utcnow() - timedelta(days=int(policy.contact_window_days or 7))
    for dr in cycles:
        intv = dr.intervention
        if intv is None:
            continue
        if intv.action == _ACTION.RETRY.value:
            retry_attempts += 1
        elif intv.action == _ACTION.MESSAGE.value:
            requested = intv.requested_at
            if requested is None or _aware(requested) >= window_start:
                contacts += 1
    return PolicyContext(
        retry_attempts_so_far=retry_attempts,
        contacts_in_window=contacts,
        amount_at_risk=float(case.amount_at_risk),
        has_risk_flag=False,
    )


# ------------------------------------------------------------------- evaluate


def _resolve_policy(db: Session, *, case: RecoveryCase, policy: Policy | None) -> Policy:
    if policy is not None:
        return policy
    active = PolicyRepository(db).active_for_merchant(case.merchant_id)
    if active is None:
        raise RecoveryFlowError(
            f"no active Policy for merchant {case.merchant_id} and none supplied"
        )
    return active


def _resolve_model_for_case(db: Session, *, case: RecoveryCase) -> PromotedModel:
    """Case-level experiment integration (data-model.md "Experimental
    model versions" / ADR-011): if this case is assigned to TREATMENT and
    its ExperimentAssignment references a usable (VALIDATED or PROMOTED)
    ModelVersion via ``experimental_config_ref``, use that model for this
    case's predictions instead of the production default — without
    touching the default for every other case. CONTROL, an unassigned
    case, or a TREATMENT assignment with no usable config_ref all fall
    back to the promoted model (today's unchanged behavior). This only
    ever changes which model supplies the probabilities — EIRV, the
    policy veto loop, and action selection are untouched, so a case can
    never bypass safety by virtue of its experiment arm."""
    assignment = ExperimentRepository(db).assignment_for_case(case.id)
    if (
        assignment is not None
        and assignment.arm == enums.ExperimentArm.TREATMENT.value
        and assignment.experimental_config_ref is not None
    ):
        mv = ModelVersionRepository(db).get(assignment.experimental_config_ref)
        if mv is not None and mv.status in (
            enums.ModelVersionStatus.VALIDATED.value,
            enums.ModelVersionStatus.PROMOTED.value,
        ):
            return PromotedModel(predictor=load_for_model_version(mv), model_version=mv)
    return get_promoted_model(db)


def _open_or_reuse_case(
    db: Session,
    *,
    payment: Payment,
    failure_category: str | None,
    failure_code: str | None,
    recovery_window_days: int,
    opened_at: datetime | None,
) -> tuple[RecoveryCase, bool]:
    case_repo = RecoveryCaseRepository(db)
    existing = case_repo.active_for_payment(payment.id)
    if existing is not None:
        return existing, False
    case = case_repo.open_case(
        payment=payment,
        amount_at_risk=payment.amount,
        failure_category=failure_category,
        failure_code=failure_code,
        recovery_window_days=recovery_window_days,
        opened_at=opened_at,
    )
    return case, True


def evaluate_recovery(
    db: Session,
    *,
    payment: Payment,
    policy: Policy | None = None,
    promoted: PromotedModel | None = None,
    decision_time: datetime | None = None,
    recovery_window_days: int = 14,
    engine_config: DecisionEngineConfig | None = None,
) -> EvaluationResult:
    """Run ONE decision cycle for a failed payment: eligibility -> case ->
    DecisionEngine.run_cycle -> advance the case state machine -> (mock
    execute nothing yet; that's :func:`execute_decision`). If the very
    first cycle recommends NO_ACTION with nothing else to do, the case is
    STOPPED here (a valid "do nothing" terminal)."""
    now = decision_time or utcnow()
    elig = check_eligibility(db, payment)
    if not elig.eligible:
        raise RecoveryFlowError(f"payment not eligible for recovery: {elig.reason}")

    case, opened = _open_or_reuse_case(
        db,
        payment=payment,
        failure_category=(payment_failure_category(db, payment)),
        failure_code=(payment_failure_code(db, payment)),
        recovery_window_days=recovery_window_days,
        opened_at=now,
    )
    if case.status in enums.TERMINAL_CASE_STATUSES:
        raise RecoveryFlowError(
            f"RecoveryCase {case.display_id} is terminal ({case.status})"
        )

    promoted = promoted or _resolve_model_for_case(db, case=case)
    policy = _resolve_policy(db, case=case, policy=policy)

    # stopping rule: recovery window elapsed
    if case.expires_at is not None and _aware(now) >= _aware(case.expires_at):
        _to_terminal(db, case, _S.EXPIRED.value, "recovery window elapsed", now)
        raise RecoveryFlowError(
            f"RecoveryCase {case.display_id} has expired; no further cycles"
        )

    case_repo = RecoveryCaseRepository(db)
    # OPEN -> ANALYZING  (or WAITING_FOR_OUTCOME -> ANALYZING on re-eval)
    if case.status == _S.OPEN.value:
        case_repo.transition(case, _S.ANALYZING.value, occurred_at=now)
    elif case.status == _S.WAITING_FOR_OUTCOME.value:
        case_repo.transition(
            case, _S.ANALYZING.value, reason="re-evaluate", occurred_at=now
        )
    elif case.status != _S.ANALYZING.value:
        raise RecoveryFlowError(
            f"RecoveryCase {case.display_id} in {case.status}; cannot evaluate"
        )

    snapshot = build_case_feature_snapshot(db, case=case, decision_time=now)
    ctx = build_policy_context(db, case=case, policy=policy)

    engine = DecisionEngine(
        db,
        predictor=promoted.predictor,
        model_version=promoted.model_version,
        config=engine_config,
    )
    outcome = engine.run_cycle(
        case=case,
        feature_snapshot=snapshot,
        policy=policy,
        policy_context=ctx,
        decision_timestamp=now,
    )

    # advance the case state machine. If NO_ACTION is the authorized action
    # on the FIRST cycle of the case, "do nothing" is a valid terminal:
    # STOPPED directly from ANALYZING (a valid edge). Otherwise walk the
    # case to WAITING_FOR_OUTCOME so an outcome can still be recorded.
    stopped_early = False
    stop_reason = None
    is_first_cycle = _dr_count(db, case) == 1
    if not outcome.intervention_created and is_first_cycle:
        case_repo.transition(
            case,
            _S.STOPPED.value,
            reason=f"first cycle: {outcome.decision_reason}",
            occurred_at=now,
        )
        stopped_early = True
        stop_reason = outcome.decision_reason
    else:
        case_repo.transition(case, _S.ACTION_SELECTED.value, occurred_at=now)
        case_repo.transition(case, _S.ACTION_EXECUTED.value, occurred_at=now)
        case_repo.transition(case, _S.WAITING_FOR_OUTCOME.value, occurred_at=now)

    db.flush()
    return EvaluationResult(
        case=case,
        decision=outcome,
        cycle_number=outcome.decision_record.cycle_number,
        case_status=case.status,
        stopped_early=stopped_early,
        stop_reason=stop_reason,
    )


# -------------------------------------------------------------- mock execute


def execute_decision(
    db: Session,
    *,
    decision_record_id,
    force_status: str | None = None,
    resolved_at: datetime | None = None,
) -> Intervention | None:
    """Mock execution path — no real payment provider. Moves the cycle's
    Intervention from REQUESTED to a resolved ``execution_status``
    (ACCEPTED by default; ``force_status`` may pick REJECTED / FAILED for
    a demo). Returns ``None`` for a NO_ACTION cycle (nothing to execute)."""
    dc = DecisionCycleRepository(db)
    dr = dc.get(decision_record_id)
    if dr is None:
        raise RecoveryFlowError(f"DecisionRecord {decision_record_id} not found")
    intv = dr.intervention
    if intv is None:
        return None  # NO_ACTION cycle
    if intv.execution_status != _EXEC.REQUESTED.value:
        return intv  # already resolved — idempotent
    status = force_status or _EXEC.ACCEPTED.value
    if status not in enums.values(enums.ExecutionStatus):
        raise RecoveryFlowError(f"invalid execution status {status!r}")
    dc.update_execution_status(intv, status, resolved_at=resolved_at or utcnow())
    db.flush()
    return intv


# ------------------------------------------------------------------- outcome


def record_outcome(
    db: Session,
    *,
    decision_record_id,
    result: str,
    recovery_amount: Decimal | float | str = 0,
    observed_at: datetime | None = None,
) -> DecisionRecord:
    """Attach the observed Outcome to THIS decision cycle, then move the
    case: RECOVERED (+ PAYMENT_SUCCEEDED) on a recovery, otherwise keep it
    open for another cycle or EXPIRE it at the window end. Terminal +
    labellable cases get their TrainingExamples derived."""
    now = observed_at or utcnow()
    dc = DecisionCycleRepository(db)
    dr = dc.get(decision_record_id)
    if dr is None:
        raise RecoveryFlowError(f"DecisionRecord {decision_record_id} not found")
    if result not in enums.values(enums.OutcomeResult):
        raise RecoveryFlowError(f"invalid outcome result {result!r}")

    case = RecoveryCaseRepository(db).get(dr.recovery_case_id)
    if case.status == _S.ANALYZING.value:
        # a re-eval cycle that hasn't executed — push it through the states
        _advance_to_waiting(db, case, now)

    intv = dr.intervention
    dc.record_outcome(
        decision_record=dr,
        result=result,
        recovery_amount=Decimal(str(recovery_amount)),
        observed_at=now,
        intervention=intv,
    )

    if result == enums.OutcomeResult.RECOVERED.value:
        _mark_recovered(db, case, dr, now)
    else:
        _close_or_continue(db, case, now)

    _derive_training_examples_if_terminal(db, case)
    db.flush()
    return dr


# ---------------------------------------------------------------- re-evaluate


def reevaluate(
    db: Session,
    *,
    case: RecoveryCase,
    policy: Policy | None = None,
    promoted: PromotedModel | None = None,
    decision_time: datetime | None = None,
    engine_config: DecisionEngineConfig | None = None,
) -> EvaluationResult:
    """Open a NEW decision cycle on an existing, non-terminal case. Cycle
    N stays immutable; this creates cycle N+1."""
    if case.status in enums.TERMINAL_CASE_STATUSES:
        raise RecoveryFlowError(
            f"RecoveryCase {case.display_id} is terminal ({case.status}); cannot re-evaluate"
        )
    payment = db.get(Payment, case.payment_id)
    return evaluate_recovery(
        db,
        payment=payment,
        policy=policy,
        promoted=promoted,
        decision_time=decision_time,
        engine_config=engine_config,
    )


# --------------------------------------------------------------------- helpers


def payment_failure_category(db: Session, payment: Payment) -> str | None:
    ev = _last_failed_event(db, payment)
    if ev is not None and ev.event_metadata:
        return ev.event_metadata.get("failure_category")
    return None


def payment_failure_code(db: Session, payment: Payment) -> str | None:
    ev = _last_failed_event(db, payment)
    if ev is not None and ev.event_metadata:
        return ev.event_metadata.get("failure_code")
    return None


def _last_failed_event(db: Session, payment: Payment):
    from backend.models.core_entities import PaymentEvent

    return db.scalar(
        select(PaymentEvent)
        .where(
            PaymentEvent.payment_id == payment.id,
            PaymentEvent.event_type == enums.PaymentEventType.PAYMENT_FAILED.value,
        )
        .order_by(PaymentEvent.event_timestamp.desc())
    )


def _dr_count(db: Session, case: RecoveryCase) -> int:
    return len(DecisionCycleRepository(db).cycles_for_case(case.id))


def _advance_to_waiting(db: Session, case: RecoveryCase, when: datetime) -> None:
    repo = RecoveryCaseRepository(db)
    if case.status == _S.ANALYZING.value:
        repo.transition(case, _S.ACTION_SELECTED.value, occurred_at=when)
    if case.status == _S.ACTION_SELECTED.value:
        repo.transition(case, _S.ACTION_EXECUTED.value, occurred_at=when)
    if case.status == _S.ACTION_EXECUTED.value:
        repo.transition(case, _S.WAITING_FOR_OUTCOME.value, occurred_at=when)


def _mark_recovered(
    db: Session, case: RecoveryCase, dr: DecisionRecord, when: datetime
) -> None:
    payment = db.get(Payment, case.payment_id)
    PaymentEventRepository(db).append(
        payment_id=payment.id,
        event_type=enums.PaymentEventType.PAYMENT_SUCCEEDED.value,
        event_timestamp=when,
    )
    PaymentRepository(db).set_status(payment, enums.PaymentStatus.SUCCEEDED.value)
    RecoveryCaseRepository(db).transition(
        case,
        _S.RECOVERED.value,
        reason=f"recovered via {dr.final_action}",
        occurred_at=when,
    )


def _close_or_continue(db: Session, case: RecoveryCase, when: datetime) -> None:
    """A NOT_RECOVERED cycle: expire the case if the window has elapsed,
    otherwise leave it WAITING_FOR_OUTCOME so the caller may re-evaluate."""
    if case.expires_at is not None and _aware(when) >= _aware(case.expires_at):
        RecoveryCaseRepository(db).transition(
            case, _S.EXPIRED.value, reason="recovery window elapsed", occurred_at=when
        )


def _to_terminal(
    db: Session, case: RecoveryCase, status: str, reason: str, when: datetime
) -> None:
    """Drive a case to a terminal state from wherever it is, walking only
    valid state-machine edges. Used by the stopping rules."""
    repo = RecoveryCaseRepository(db)
    if status == _S.STOPPED.value:
        # STOPPED is reachable from ANALYZING or WAITING_FOR_OUTCOME.
        if case.status == _S.OPEN.value:
            repo.transition(case, _S.ANALYZING.value, occurred_at=when)
        if case.status in (_S.ANALYZING.value, _S.WAITING_FOR_OUTCOME.value):
            repo.transition(case, _S.STOPPED.value, reason=reason, occurred_at=when)
            return
    if status == _S.EXPIRED.value:
        # EXPIRED is only reachable from WAITING_FOR_OUTCOME; push through.
        if case.status == _S.OPEN.value:
            repo.transition(case, _S.ANALYZING.value, occurred_at=when)
        if case.status == _S.ANALYZING.value:
            repo.transition(case, _S.ACTION_SELECTED.value, occurred_at=when)
        if case.status == _S.ACTION_SELECTED.value:
            repo.transition(case, _S.ACTION_EXECUTED.value, occurred_at=when)
        if case.status == _S.ACTION_EXECUTED.value:
            repo.transition(case, _S.WAITING_FOR_OUTCOME.value, occurred_at=when)
        repo.transition(case, _S.EXPIRED.value, reason=reason, occurred_at=when)
        return
    repo.transition(case, status, reason=reason, occurred_at=when)


def _derive_training_examples_if_terminal(db: Session, case: RecoveryCase) -> int:
    if case.status not in enums.LABELLABLE_TERMINAL_STATUSES:
        return 0
    te_repo = TrainingExampleRepository(db)
    dc = DecisionCycleRepository(db)
    n = 0
    for dr in dc.cycles_for_case(case.id):
        n += len(te_repo.generate_for_decision_record(dr))
    return n
