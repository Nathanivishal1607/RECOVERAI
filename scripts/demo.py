"""Phase 5 end-to-end demo runner.

Spins up a throwaway SQLite database, generates a small synthetic dataset,
trains + promotes the T-learner, then runs the five demo scenarios (A-E)
and prints each decision-audit chain. Exits non-zero if any scenario's
expectation fails.

    python scripts/demo.py                       # full run, in-memory DB
    python scripts/demo.py --db sqlite:///demo.db --keep
    python scripts/demo.py --seed 42 --n-cases 1200

Everything here is SYNTHETIC and clearly labelled as such.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from backend.models import Base, enums
from backend.repositories.governance import ModelVersionRepository
from ml.inference.recovery import clear_cache
from ml.training.uplift import MODEL_ROLE, train_uplift_model
from simulation.config import SimConfig
from simulation.generator.runner import run_simulation
from simulation.scenarios.demo_cases import run_all


def _engine(url: str):
    eng = create_engine(url, connect_args={"check_same_thread": False}, future=True)
    if url.startswith("sqlite"):

        @event.listens_for(eng, "connect")
        def _fk(conn, _rec):  # noqa: ANN001
            cur = conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    return eng


def _train_and_promote(db, *, seed: int) -> None:
    tr = train_uplift_model(db, kind="t_learner", version=f"demo-{seed}", seed=seed)
    repo = ModelVersionRepository(db)
    current = repo.promoted_for_role(MODEL_ROLE)
    if current is not None:
        repo.transition_status(current, enums.ModelVersionStatus.RETIRED.value)
    repo.transition_status(tr.model_version, enums.ModelVersionStatus.VALIDATED.value)
    repo.transition_status(tr.model_version, enums.ModelVersionStatus.PROMOTED.value)
    db.commit()
    val = (tr.evaluation.get("validation") or {})
    print(
        f"[demo] promoted {tr.model_version.model_name} {tr.model_version.version} "
        f"(val ROC-AUC={val.get('roc_auc')}, Brier={val.get('brier')})"
    )


def _print_result(r) -> None:
    status = "OK " if r.ok else "FAIL"
    print(f"\n================  SCENARIO {r.key}: {r.title}  [{status}]")
    print(
        f"  expected recommendation : {r.expected_recommendation}\n"
        f"  recommended_action      : {r.recommended_action}\n"
        f"  final_action            : {r.final_action}   (blocked={r.was_blocked})\n"
        f"  case status             : {r.case_status}"
    )
    for note in r.notes:
        print(f"  !! {note}")
    for audit in r.audits:
        _print_audit(audit)


def _print_audit(a) -> None:
    mv = a.model_version
    print(
        f"  --- cycle {a.cycle_number}  (DecisionRecord {str(a.decision_record_id)[:8]}, "
        f"status {a.status}) ---"
    )
    print(f"      model_version : {mv.model_name} {mv.version} [{mv.algorithm}] "
          f"status={mv.status}" if mv else "      model_version : <none>")
    print(f"      {'action':<10} {'P(recover)':>11} {'incremental':>12} {'EIRV':>10}  "
          f"{'policy':>8}  flags")
    for c in a.actions_considered:
        flags = []
        if c.is_recommended:
            flags.append("RECOMMENDED")
        if c.is_final:
            flags.append("FINAL")
        pol = f"{c.policy_result or '-'}"
        print(
            f"      {c.action:<10} "
            f"{_f(c.recovery_probability, 4):>11} "
            f"{_f(c.incremental_probability, 4):>12} "
            f"{_f(c.eirv_value, 2):>10}  "
            f"{pol:>8}  {' '.join(flags)}"
            + (f"  ({c.policy_reason_code})" if c.policy_reason_code and c.policy_result == 'BLOCKED' else "")
        )
    print(f"      decision_reason : {a.decision_reason}")
    if a.was_blocked:
        print(f"      BLOCKED         : {', '.join(a.block_reason_codes) or 'policy veto'}")
    if a.intervention_action:
        print(
            f"      intervention    : {a.intervention_action} "
            f"({a.intervention_channel or 'n/a'})  execution_status={a.execution_status}"
        )
    else:
        print("      intervention    : none (NO_ACTION never creates one)")
    if a.outcome_result:
        print(
            f"      outcome         : {a.outcome_result} "
            f"(amount {a.outcome_recovery_amount}) observed_at {a.outcome_observed_at}"
        )
    if a.previous_cycles:
        prev = ", ".join(
            f"#{p.cycle_number}:{p.final_action}->{p.outcome_result or 'pending'}"
            for p in a.previous_cycles
        )
        print(f"      previous_cycles : {prev}")


def _f(v, nd) -> str:
    return "-" if v is None else f"{v:.{nd}f}"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="scripts/demo.py")
    p.add_argument("--db", default="sqlite://", help="SQLAlchemy URL (default in-memory)")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--n-cases", type=int, default=1200)
    p.add_argument("--customers-per-merchant", type=int, default=250)
    p.add_argument("--keep", action="store_true", help="do not drop tables on exit")
    args = p.parse_args(argv)

    eng = _engine(args.db)
    Base.metadata.create_all(eng)
    Session = sessionmaker(bind=eng, future=True, expire_on_commit=False)
    db = Session()
    clear_cache()

    try:
        print(
            f"[demo] generating synthetic data: seed={args.seed} "
            f"n_cases={args.n_cases} (SYNTHETIC - not Razorpay data)"
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

        results = run_all(db)
        for r in results:
            _print_result(r)

        failed = [r for r in results if not r.ok]
        print("\n================  SUMMARY  ================")
        for r in results:
            print(f"  {r.key}  {'OK  ' if r.ok else 'FAIL'}  {r.title}")
        if failed:
            print(f"\n{len(failed)} scenario(s) failed.")
            return 1
        print("\nAll 5 demo scenarios passed.")
        return 0
    finally:
        db.close()
        if not args.keep:
            Base.metadata.drop_all(eng)
        eng.dispose()


if __name__ == "__main__":
    sys.exit(main())
