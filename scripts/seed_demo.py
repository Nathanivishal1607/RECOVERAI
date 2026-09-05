"""Phase 6 — idempotent demo-data seed for the hackathon deployment.

Populates the configured database (``DATABASE_URL`` / ``--db``) with:

  1. A realistic bulk dataset from the existing Phase 2 simulator
     (``simulation.generator.runner.run_simulation``) — gives the
     dashboard/list screens real volume.
  2. A trained + PROMOTED T-learner (``ml.training.uplift``), required by
     the decision engine.
  3. The five deterministic Phase 5 demo scenarios A-E
     (``simulation.scenarios.demo_cases``) — RETRY / MESSAGE / NO_ACTION /
     policy-blocked / multi-cycle, all run through the REAL DecisionEngine
     + PolicyEngine (unlike the bulk simulator, which uses a lightweight
     heuristic policy).
  4. Two small additional cases (F, G) covering a REJECTED and a FAILED
     execution status — the one gap the demo scenarios don't otherwise
     exercise. No simulator/decision-engine code changes; this only calls
     the existing ``backend.services.recovery_flow`` API with a
     ``force_status`` override, same as the Phase 5 HTTP route does.

Idempotent: if the database already has any Merchant, the script does
nothing (so container restarts don't keep re-seeding). Everything here is
SYNTHETIC data, clearly labelled as such — no real Razorpay data.

    python scripts/seed_demo.py                  # uses settings.database_url
    python scripts/seed_demo.py --db sqlite:///demo.db
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import event, func, select
from sqlalchemy.orm import Session, sessionmaker

from backend.models import Base, enums
from backend.models.core_entities import Merchant
from backend.repositories.core import CustomerRepository, MerchantRepository
from backend.repositories.governance import ModelVersionRepository, PolicyRepository
from backend.services import recovery_flow as flow
from ml.inference.recovery import clear_cache
from ml.training.uplift import MODEL_ROLE, train_uplift_model
from simulation.config import SimConfig
from simulation.generator.runner import run_simulation
from simulation.scenarios.demo_cases import run_all


def _engine(url: str):
    from sqlalchemy import create_engine

    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    eng = create_engine(url, connect_args=connect_args, future=True, pool_pre_ping=True)
    if url.startswith("sqlite"):

        @event.listens_for(eng, "connect")
        def _fk(conn, _rec):  # noqa: ANN001
            cur = conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    return eng


def _already_seeded(db: Session) -> bool:
    return (db.scalar(select(func.count(Merchant.id))) or 0) > 0


def _train_and_promote(db: Session, *, seed: int) -> None:
    tr = train_uplift_model(db, kind="t_learner", version=f"seed-{seed}", seed=seed)
    repo = ModelVersionRepository(db)
    current = repo.promoted_for_role(MODEL_ROLE)
    if current is not None:
        repo.transition_status(current, enums.ModelVersionStatus.RETIRED.value)
    repo.transition_status(tr.model_version, enums.ModelVersionStatus.VALIDATED.value)
    repo.transition_status(tr.model_version, enums.ModelVersionStatus.PROMOTED.value)
    db.commit()
    val = (tr.evaluation.get("validation") or {})
    print(
        f"[seed] promoted {tr.model_version.model_name} {tr.model_version.version} "
        f"(val ROC-AUC={val.get('roc_auc')}, Brier={val.get('brier')})"
    )


# ---------------------------------------------------- scenarios F / G (execution)


_T0 = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


def _exec_status_scenario(db: Session, *, tag: str, force_status: str) -> str | None:
    """A RETRY/MESSAGE case whose Intervention is mock-executed with the
    given non-ACCEPTED ``force_status`` (REJECTED or FAILED), then closed
    with a NOT_RECOVERED outcome. Returns the case display_id, or None if
    the cycle happened not to select an executable action (rare)."""
    from backend.repositories.core import PaymentEventRepository
    from backend.services.model_provider import get_promoted_model

    m = MerchantRepository(db).create(name=f"Demo-{tag}", industry="ecommerce")
    CustomerRepository(db).create(
        customer_id=f"CUST-{tag}", merchant_id=m.id,
        transaction_count=40, successful_transactions=20, failed_transactions=20,
        historical_recovery_rate=Decimal("0.2"),
    )
    pol = PolicyRepository(db).create_version(
        policy_id=f"POL-{m.display_id}", policy_version="v1", merchant_id=m.id,
        max_retry_count=3, max_customer_contacts=3,
        allowed_interventions=["RETRY", "MESSAGE"],
    )
    pay = flow.ingest_failed_payment(
        db, merchant_id=m.id, customer_id=f"CUST-{tag}", amount=Decimal("2500.00"),
        currency="INR", payment_method="CARD",
        failure_category="TEMPORARY", failure_code="SIM_GATEWAY_TIMEOUT",
        created_at=_T0,
    )
    pe_repo = PaymentEventRepository(db)
    for i in range(3):
        pe_repo.append(
            payment_id=pay.id, event_type="RETRY_ATTEMPTED",
            event_timestamp=_T0 + timedelta(minutes=10 + i), attempt_number=2 + i,
        )
        pe_repo.append(
            payment_id=pay.id, event_type="PAYMENT_FAILED",
            event_timestamp=_T0 + timedelta(minutes=11 + i), attempt_number=2 + i,
            metadata={"failure_code": "SIM_GATEWAY_TIMEOUT", "failure_category": "TEMPORARY"},
        )
    db.flush()

    promoted = get_promoted_model(db)
    res = flow.evaluate_recovery(
        db, payment=pay, policy=pol, promoted=promoted,
        decision_time=_T0 + timedelta(minutes=30),
    )
    if not res.decision.intervention_created:
        db.rollback()
        return None
    dr_id = res.decision.decision_record.id
    flow.execute_decision(db, decision_record_id=dr_id, force_status=force_status)
    flow.record_outcome(
        db, decision_record_id=dr_id, result="NOT_RECOVERED",
        observed_at=_T0 + timedelta(hours=2),
    )
    db.commit()
    return res.case.display_id


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="scripts/seed_demo.py")
    p.add_argument("--db", default=None, help="SQLAlchemy URL (default: settings.database_url)")
    p.add_argument("--seed", type=int, default=int(os.environ.get("SEED_SEED", 42)))
    p.add_argument("--n-cases", type=int, default=int(os.environ.get("SEED_N_CASES", 1200)))
    p.add_argument(
        "--customers-per-merchant", type=int,
        default=int(os.environ.get("SEED_CUSTOMERS_PER_MERCHANT", 250)),
    )
    p.add_argument("--force", action="store_true", help="seed even if data already exists")
    args = p.parse_args(argv)

    if args.db:
        eng = _engine(args.db)
    else:
        from backend.database.session import get_engine

        eng = get_engine()

    Base.metadata.create_all(eng)
    Session = sessionmaker(bind=eng, future=True, expire_on_commit=False)
    db = Session()
    clear_cache()

    try:
        if not args.force and _already_seeded(db):
            print("[seed] database already has data — skipping (use --force to re-seed)")
            return 0

        print(
            f"[seed] generating synthetic bulk data: seed={args.seed} "
            f"n_cases={args.n_cases} (SYNTHETIC — not Razorpay data)"
        )
        run_simulation(
            db,
            replace(
                SimConfig(seed=args.seed),
                n_cases=args.n_cases,
                customers_per_merchant=args.customers_per_merchant,
            ),
        )
        _train_and_promote(db, seed=args.seed)

        print("[seed] running deterministic demo scenarios A-E ...")
        from backend.models.core_entities import RecoveryCase

        try:
            results = run_all(db)
        except flow.RecoveryFlowError as exc:
            # Scenarios already committed (run_all commits per-scenario) are
            # kept; a rare model-quality edge case shouldn't crash the seed.
            db.rollback()
            print(f"[seed]   WARNING: demo scenario run stopped early: {exc}")
            results = []
        for r in results:
            status = "OK" if r.ok else "MISMATCH"
            display_id = "-"
            if r.audits:
                case = db.get(RecoveryCase, r.audits[0].recovery_case_id)
                display_id = case.display_id if case else "-"
            print(f"[seed]   scenario {r.key} ({r.title}): {status}  case={display_id}")

        print("[seed] adding REJECTED / FAILED execution demo cases ...")
        rejected_case = _exec_status_scenario(db, tag="F-rejected", force_status="REJECTED")
        failed_case = _exec_status_scenario(db, tag="G-failed", force_status="FAILED")
        print(f"[seed]   REJECTED execution case: {rejected_case}")
        print(f"[seed]   FAILED execution case:   {failed_case}")

        print("[seed] done.")
        return 0
    finally:
        db.close()
        eng.dispose()


if __name__ == "__main__":
    sys.exit(main())
