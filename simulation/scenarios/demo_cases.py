"""Phase 5 — five deterministic demo scenarios (A-E).

Each scenario builds a merchant / customer / failed payment / policy
directly, runs it through ``backend.services.recovery_flow`` with the
PROMOTED recovery model, and returns the full decision-audit chain so a
judge can inspect exactly what the engine considered and why.

    A  RETRY is the best economic action
    B  MESSAGE is the best economic action
    C  NO_ACTION is the best economic action
    D  the economic recommendation is blocked by policy; final_action differs
    E  a RecoveryCase is evaluated twice — cycle-1 DecisionRecord stays
       immutable, cycle 2 is a new DecisionRecord

These are SYNTHETIC scenarios. The feature values are chosen so the
trained model's per-action probabilities push a specific action's EIRV
highest; nothing here reads simulator hidden ground truth.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from backend.models import enums
from backend.repositories.core import (
    CustomerRepository,
    MerchantRepository,
    PaymentEventRepository,
)
from backend.repositories.governance import PolicyRepository
from backend.schemas.audit import DecisionAuditRead, build_decision_audit
from backend.services import recovery_flow as flow
from backend.services.model_provider import PromotedModel, get_promoted_model

T0 = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)


@dataclass
class DemoResult:
    key: str
    title: str
    expected_recommendation: str
    recommended_action: str
    final_action: str
    was_blocked: bool
    case_status: str
    audits: list[DecisionAuditRead] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.notes


# --------------------------------------------------------------- fixtures


def _merchant_customer_policy(
    db: Session,
    *,
    tag: str,
    prev_recovery_rate: float = 0.4,
    hist_success_rate: float = 0.7,
    allowed=("RETRY", "MESSAGE"),
    max_retry_count: int = 3,
    max_customer_contacts: int = 3,
    min_amount: float | None = None,
):
    m = MerchantRepository(db).create(name=f"Demo-{tag}", industry="ecommerce")
    n = 40
    succ = int(n * hist_success_rate)
    CustomerRepository(db).create(
        customer_id=f"CUST-{tag}",
        merchant_id=m.id,
        transaction_count=n,
        successful_transactions=succ,
        failed_transactions=n - succ,
        historical_recovery_rate=Decimal(str(prev_recovery_rate)),
    )
    pol = PolicyRepository(db).create_version(
        policy_id=f"POL-{m.display_id}",
        policy_version="v1",
        merchant_id=m.id,
        max_retry_count=max_retry_count,
        max_customer_contacts=max_customer_contacts,
        contact_window_days=7,
        allowed_interventions=list(allowed),
        minimum_amount=Decimal(str(min_amount)) if min_amount is not None else None,
    )
    db.flush()
    return m, f"CUST-{tag}", pol


def _ingest(
    db: Session,
    *,
    merchant_id,
    customer_id: str,
    amount: float,
    failure_category: str,
    failure_code: str,
    method: str = "CARD",
    at: datetime = T0,
):
    return flow.ingest_failed_payment(
        db,
        merchant_id=merchant_id,
        customer_id=customer_id,
        amount=Decimal(str(amount)),
        currency="INR",
        payment_method=method,
        failure_category=failure_category,
        failure_code=failure_code,
        created_at=at,
    )


def _add_prior_attempts(db: Session, payment, *, n: int, category: str, code: str) -> None:
    """Append ``n`` prior RETRY_ATTEMPTED + PAYMENT_FAILED event pairs so
    the feature builder's ``attempt_number`` reflects a case that has
    already been tried a few times."""
    pe = PaymentEventRepository(db)
    for i in range(n):
        pe.append(
            payment_id=payment.id, event_type="RETRY_ATTEMPTED",
            event_timestamp=T0 + timedelta(minutes=10 + i), attempt_number=2 + i,
        )
        pe.append(
            payment_id=payment.id, event_type="PAYMENT_FAILED",
            event_timestamp=T0 + timedelta(minutes=11 + i), attempt_number=2 + i,
            metadata={"failure_code": code, "failure_category": category},
        )
    db.flush()


# ------------------------------------------------------------- scenarios


def scenario_a_retry(db: Session, promoted: PromotedModel) -> DemoResult:
    """A transient (TEMPORARY) failure that has already failed a few
    times, mid-size amount — an immediate RETRY has by far the highest
    EIRV; a MESSAGE is a waste and NO_ACTION leaves revenue on the table."""
    m, cust, pol = _merchant_customer_policy(
        db, tag="A", prev_recovery_rate=0.2, hist_success_rate=0.5
    )
    pay = _ingest(
        db, merchant_id=m.id, customer_id=cust, amount=2500.0,
        failure_category="TEMPORARY", failure_code="SIM_GATEWAY_TIMEOUT",
    )
    _add_prior_attempts(db, pay, n=3, category="TEMPORARY", code="SIM_GATEWAY_TIMEOUT")
    res = flow.evaluate_recovery(
        db, payment=pay, policy=pol, promoted=promoted,
        decision_time=T0 + timedelta(minutes=30),
    )
    audit = build_decision_audit(db, res.decision.decision_record)
    return _finish("A", "RETRY is the best economic action", "RETRY", res, [audit])


def scenario_b_message(db: Session, promoted: PromotedModel) -> DemoResult:
    """A CUSTOMER_ACTION_REQUIRED failure (the customer must
    re-authenticate) that has already been retried — blindly retrying
    again barely helps, but a MESSAGE prompting the customer has the
    highest EIRV."""
    m, cust, pol = _merchant_customer_policy(
        db, tag="B", prev_recovery_rate=0.25, hist_success_rate=0.4
    )
    pay = _ingest(
        db, merchant_id=m.id, customer_id=cust, amount=1500.0,
        failure_category="CUSTOMER_ACTION_REQUIRED", failure_code="SIM_AUTH_REQUIRED",
    )
    _add_prior_attempts(
        db, pay, n=2, category="CUSTOMER_ACTION_REQUIRED", code="SIM_AUTH_REQUIRED"
    )
    res = flow.evaluate_recovery(
        db, payment=pay, policy=pol, promoted=promoted,
        decision_time=T0 + timedelta(minutes=30),
    )
    audit = build_decision_audit(db, res.decision.decision_record)
    return _finish("B", "MESSAGE is the best economic action", "MESSAGE", res, [audit])


def scenario_c_no_action(db: Session, promoted: PromotedModel) -> DemoResult:
    """A very reliable customer, tiny amount, transient failure on the
    first cycle — they will almost certainly pay on their own, so every
    intervention's EIRV is negative. NO_ACTION wins and the case is
    STOPPED on the first cycle ("do nothing" is a valid decision)."""
    m, cust, pol = _merchant_customer_policy(
        db, tag="C", prev_recovery_rate=0.9, hist_success_rate=0.95
    )
    pay = _ingest(
        db, merchant_id=m.id, customer_id=cust, amount=120.0,
        failure_category="TEMPORARY", failure_code="SIM_GATEWAY_TIMEOUT",
    )
    res = flow.evaluate_recovery(
        db, payment=pay, policy=pol, promoted=promoted,
        decision_time=T0 + timedelta(minutes=5),
    )
    audit = build_decision_audit(db, res.decision.decision_record)
    return _finish("C", "NO_ACTION is the best economic action", "NO_ACTION", res, [audit])


def scenario_d_policy_block(db: Session, promoted: PromotedModel) -> DemoResult:
    """Same profile as scenario A (RETRY is the economic recommendation),
    but the merchant policy permits only MESSAGE and caps retries at 0 —
    the recommendation is blocked and ``final_action`` differs from it."""
    m, cust, pol = _merchant_customer_policy(
        db, tag="D", prev_recovery_rate=0.2, hist_success_rate=0.5,
        allowed=("MESSAGE",), max_retry_count=0, max_customer_contacts=3,
    )
    pay = _ingest(
        db, merchant_id=m.id, customer_id=cust, amount=2500.0,
        failure_category="TEMPORARY", failure_code="SIM_GATEWAY_TIMEOUT",
    )
    _add_prior_attempts(db, pay, n=3, category="TEMPORARY", code="SIM_GATEWAY_TIMEOUT")
    res = flow.evaluate_recovery(
        db, payment=pay, policy=pol, promoted=promoted,
        decision_time=T0 + timedelta(minutes=30),
    )
    audit = build_decision_audit(db, res.decision.decision_record)
    r = _finish("D", "Recommendation blocked by policy", "RETRY", res, [audit])
    # the expectation here is about the BLOCK, not just the recommendation
    r.notes = []
    if res.decision.recommended_action != "RETRY":
        r.notes.append(
            f"expected economic recommendation RETRY, got {res.decision.recommended_action}"
        )
    if res.decision.final_action == res.decision.recommended_action:
        r.notes.append("expected final_action to differ from the recommendation")
    if res.decision.final_action not in ("MESSAGE", "NO_ACTION"):
        r.notes.append(f"unexpected final_action {res.decision.final_action}")
    return r


def scenario_e_reevaluation(db: Session, promoted: PromotedModel) -> DemoResult:
    """Evaluate a case, record a NOT_RECOVERED outcome on cycle 1, then
    re-evaluate. Cycle-1 DecisionRecord must be byte-identical afterwards;
    cycle 2 is a brand-new DecisionRecord with cycle_number == 2."""
    m, cust, pol = _merchant_customer_policy(
        db, tag="E", prev_recovery_rate=0.2, hist_success_rate=0.5
    )
    pay = _ingest(
        db, merchant_id=m.id, customer_id=cust, amount=2500.0,
        failure_category="TEMPORARY", failure_code="SIM_GATEWAY_TIMEOUT",
    )
    _add_prior_attempts(db, pay, n=3, category="TEMPORARY", code="SIM_GATEWAY_TIMEOUT")

    res1 = flow.evaluate_recovery(
        db, payment=pay, policy=pol, promoted=promoted,
        decision_time=T0 + timedelta(minutes=30),
    )
    dr1 = res1.decision.decision_record
    dr1_id = dr1.id
    snap1 = _dr_fingerprint(dr1)

    notes: list[str] = []
    if res1.decision.intervention_created:
        flow.execute_decision(db, decision_record_id=dr1_id)
        flow.record_outcome(
            db, decision_record_id=dr1_id, result="NOT_RECOVERED",
            observed_at=T0 + timedelta(hours=2),
        )
    else:
        notes.append("scenario E expected cycle 1 to create an Intervention")

    case = res1.case
    res2 = flow.reevaluate(
        db, case=case, policy=pol, promoted=promoted,
        decision_time=T0 + timedelta(hours=4),
    )
    dr2 = res2.decision.decision_record

    db.refresh(dr1)
    snap1_after = _dr_fingerprint(dr1)
    if snap1_after != snap1:
        notes.append(
            f"cycle-1 DecisionRecord changed after re-evaluation: "
            f"{snap1} -> {snap1_after}"
        )
    if dr2.cycle_number != 2:
        notes.append(f"expected cycle 2, got cycle_number={dr2.cycle_number}")
    if dr2.id == dr1_id:
        notes.append("re-evaluation reused the cycle-1 DecisionRecord id")

    audits = [
        build_decision_audit(db, dr1),
        build_decision_audit(db, dr2),
    ]
    r = DemoResult(
        key="E",
        title="Re-evaluation: cycle 1 immutable, cycle 2 is a new DecisionRecord",
        expected_recommendation="(n/a - structural check)",
        recommended_action=res2.decision.recommended_action,
        final_action=res2.decision.final_action,
        was_blocked=res2.decision.recommended_action != res2.decision.final_action,
        case_status=db.get(type(case), case.id).status,
        audits=audits,
        notes=notes,
    )
    return r


# ----------------------------------------------------------------- helpers


def _finish(
    key: str, title: str, expected: str, res: flow.EvaluationResult, audits
) -> DemoResult:
    rec = res.decision.recommended_action
    notes: list[str] = []
    if rec != expected:
        notes.append(f"expected recommendation {expected}, got {rec}")
    return DemoResult(
        key=key,
        title=title,
        expected_recommendation=expected,
        recommended_action=rec,
        final_action=res.decision.final_action,
        was_blocked=rec != res.decision.final_action,
        case_status=res.case_status,
        audits=list(audits),
        notes=notes,
    )


def _dr_fingerprint(dr) -> tuple:
    """A structural fingerprint of a DecisionRecord's immutable content."""
    preds = tuple(
        (p.action, str(p.recovery_probability), str(p.model_version_id))
        for p in sorted(dr.predictions, key=lambda x: x.action)
    )
    pol = tuple(
        (pe.action, pe.result, pe.reason_code)
        for pe in sorted(dr.policy_evaluations, key=lambda x: x.action)
    )
    return (
        dr.cycle_number,
        dr.recommended_action,
        dr.final_action,
        dr.decision_reason,
        preds,
        pol,
    )


ALL_SCENARIOS = (
    scenario_a_retry,
    scenario_b_message,
    scenario_c_no_action,
    scenario_d_policy_block,
    scenario_e_reevaluation,
)


def run_all(db: Session, promoted: PromotedModel | None = None) -> list[DemoResult]:
    promoted = promoted or get_promoted_model(db)
    out: list[DemoResult] = []
    for fn in ALL_SCENARIOS:
        out.append(fn(db, promoted))
        db.commit()
    return out
