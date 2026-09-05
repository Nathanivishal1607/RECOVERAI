"""Phase 5 — offline evaluation of the RecoverAI decision engine vs a
naive baseline, scored against the simulator's HIDDEN oracle.

This module lives under ``simulation/evaluation/`` — a sanctioned reader
of hidden ground truth (``docs/data/synthetic-data.md`` §3). It may import
``backend`` and ``ml``; nothing under ``backend/`` or ``ml/`` imports it,
so hidden truth flows strictly *out* to evaluation and never into
training / inference / persisted predictions.

Pipeline (fixed seed, documented config -> reproducible):

  1. run the deterministic simulator (writes cases + real decision-time
     ``feature_snapshot``s + hidden per-action ``p_by_action`` sidecar);
  2. train the T-learner (default) from the persisted ``TrainingExample``
     rows (case-level 70/15/15 split, no leakage) — this is Phase 4's
     selected candidate, trained exactly as ``ml.training.uplift`` does it;
  3. score two policies on the HELD-OUT TEST split only (case-level, never
     seen in training — the same methodology as Phase 4's
     ``phase4_compare``), reading each case's REAL persisted cycle-1
     ``feature_snapshot``:
       * NAIVE  — always RETRY once (the "smart retry" baseline);
       * RECOVERAI — model P(recovery|a) -> EIRV -> policy veto -> action;
  4. score each chosen action under the hidden oracle EIRV and compare.

Metrics per policy: recovery rate (oracle realised), total & mean
realised economic value (oracle EIRV of the chosen action), action
distribution, NO_ACTION frequency, policy-block count, and EIRV regret
vs the oracle-optimal action.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker

from backend.decision_engine.optimizer import rank_actions
from backend.decision_engine.value_engine import DEFAULT_COSTS, eirv_by_action
from backend.models import Base, enums
from backend.models.decision import DecisionRecord, Prediction
from backend.policies.engine import PolicyContext, check_policy
from backend.models.governance import Policy
from ml.data.dataset import build_dataset
from ml.inference.recovery import RecoveryInference, clear_cache, load_for_model_version
from ml.training.uplift import train_uplift_model
from simulation.config import SimConfig
from simulation.ground_truth.potential_outcomes import PotentialOutcomes, eirv
from simulation.ground_truth.store import GroundTruthStore

_ACTIONS = ("RETRY", "MESSAGE", "NO_ACTION")

DEFAULT_SEED = 42
DEFAULT_N_CASES = 1500  # matches Phase 4's validated bake-off config
DEFAULT_CUSTOMERS_PER_MERCHANT = 250
DEFAULT_SPLIT = (0.7, 0.15, 0.15)
ARTIFACT_DIR = Path(__file__).resolve().parent / "artifacts"


@dataclass
class PolicyScore:
    name: str
    n_cases: int
    recovery_rate: float
    total_realised_eirv: float
    mean_realised_eirv: float
    action_mix: dict[str, float]
    no_action_frequency: float
    policy_blocks: int
    action_agreement_with_oracle: float
    mean_eirv_regret: float
    total_eirv_regret: float

    def as_dict(self) -> dict:
        d = asdict(self)
        d["action_mix"] = {k: round(v, 4) for k, v in self.action_mix.items()}
        for k in (
            "recovery_rate", "mean_realised_eirv", "no_action_frequency",
            "action_agreement_with_oracle", "mean_eirv_regret",
        ):
            d[k] = round(d[k], 4)
        for k in ("total_realised_eirv", "total_eirv_regret"):
            d[k] = round(d[k], 2)
        return d


@dataclass
class EngineEvalResult:
    seed: int
    n_cases: int
    customers_per_merchant: int
    dataset_config: dict
    model_version: dict
    oracle_action_mix: dict[str, float]
    oracle_total_eirv: float
    scores: dict[str, dict] = field(default_factory=dict)
    generated_at: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------- internals


def _engine(url: str):
    eng = create_engine(url, connect_args={"check_same_thread": False}, future=True)
    if url.startswith("sqlite"):

        @event.listens_for(eng, "connect")
        def _fk(conn, _rec):  # noqa: ANN001
            cur = conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA synchronous=OFF")
            cur.close()

    return eng


def _cycle1_snapshots(db: Session) -> dict[str, dict]:
    """case_id -> the earliest DecisionRecord's Prediction.feature_snapshot
    (the REAL decision-time snapshot, never a proxy)."""
    rows = db.execute(
        select(
            DecisionRecord.recovery_case_id,
            DecisionRecord.cycle_number,
            Prediction.feature_snapshot,
        )
        .join(Prediction, Prediction.decision_record_id == DecisionRecord.id)
        .order_by(DecisionRecord.recovery_case_id, DecisionRecord.cycle_number)
    ).all()
    out: dict[str, dict] = {}
    for case_id, cycle, snap in rows:
        key = str(case_id)
        if key not in out:  # first (lowest cycle_number) wins
            out[key] = snap
    return out


def _naive_policy() -> Policy:
    """The 'smart retry' baseline: RETRY once, nothing else."""
    return Policy(
        policy_id="EVAL-NAIVE", policy_version="v1", merchant_id=None,
        is_active=False, max_retry_count=1, max_customer_contacts=0,
        contact_window_days=7, allowed_interventions=["RETRY"],
    )


def _recoverai_policy() -> Policy:
    return Policy(
        policy_id="EVAL-RECOVERAI", policy_version="v1", merchant_id=None,
        is_active=False, max_retry_count=3, max_customer_contacts=3,
        contact_window_days=7, allowed_interventions=["RETRY", "MESSAGE"],
    )


def _score_policy(
    *,
    name: str,
    choose,
    snapshots: dict[str, dict],
    case_ids: list[str],
    store: GroundTruthStore,
    cfg: SimConfig,
) -> PolicyScore:
    n = 0
    recovered = 0.0
    total_eirv = 0.0
    total_regret = 0.0
    agree = 0
    blocks = 0
    mix: Counter = Counter()

    for cid in case_ids:  # held-out TEST split only
        gt = store.get(cid)  # sanctioned reader
        if gt is None or not gt.cycles:
            continue
        snap = snapshots.get(cid)
        if snap is None:
            continue
        n += 1
        first = gt.cycles[0]
        po = PotentialOutcomes(
            case_index=0, p_by_action=first.p_by_action, regime=first.regime,
            amount=gt.payment_amount,
        )
        oracle_best = gt.oracle_best_action or "NO_ACTION"

        action, n_blocked = choose(snap, gt.payment_amount)
        blocks += n_blocked
        mix[action] += 1
        if action == oracle_best:
            agree += 1

        # realised value + recovery under hidden truth
        chosen_eirv = 0.0 if action == "NO_ACTION" else eirv(po, action, cfg=cfg)
        total_eirv += chosen_eirv
        best_eirv = 0.0 if oracle_best == "NO_ACTION" else eirv(po, oracle_best, cfg=cfg)
        total_regret += max(0.0, best_eirv - chosen_eirv)
        # "recovered" = the case's hidden p_by_action for the chosen action
        recovered += po.probability(action)

    mix_frac = {a: mix.get(a, 0) / n for a in _ACTIONS} if n else {a: 0.0 for a in _ACTIONS}
    return PolicyScore(
        name=name,
        n_cases=n,
        recovery_rate=recovered / n if n else 0.0,
        total_realised_eirv=total_eirv,
        mean_realised_eirv=total_eirv / n if n else 0.0,
        action_mix=mix_frac,
        no_action_frequency=mix_frac["NO_ACTION"],
        policy_blocks=blocks,
        action_agreement_with_oracle=agree / n if n else 0.0,
        mean_eirv_regret=total_regret / n if n else 0.0,
        total_eirv_regret=total_regret,
    )


def _naive_chooser(policy: Policy):
    def choose(snap: dict, amount: float) -> tuple[str, int]:
        ctx = PolicyContext(
            retry_attempts_so_far=0, contacts_in_window=0, amount_at_risk=amount
        )
        d = check_policy("RETRY", policy, ctx)
        if d.allowed:
            return "RETRY", 0
        return "NO_ACTION", 1

    return choose


def _recoverai_chooser(predictor: RecoveryInference, policy: Policy):
    def choose(snap: dict, amount: float) -> tuple[str, int]:
        probs = predictor.predict_all_actions(snap)
        ranked = rank_actions(eirv_by_action(probs, amount))
        ctx = PolicyContext(
            retry_attempts_so_far=0, contacts_in_window=0, amount_at_risk=amount
        )
        n_blocked = 0
        for cand in ranked:
            if check_policy(cand, policy, ctx).allowed:
                return cand, n_blocked
            n_blocked += 1
        return "NO_ACTION", n_blocked

    return choose


# ------------------------------------------------------------------- driver


def run_engine_eval(
    *,
    seed: int = DEFAULT_SEED,
    n_cases: int = DEFAULT_N_CASES,
    customers_per_merchant: int = DEFAULT_CUSTOMERS_PER_MERCHANT,
    kind: str = "t_learner",
    write_artifact: bool = True,
    db_url: str = "sqlite://",
    artifact_dir: Path | None = None,
) -> EngineEvalResult:
    clear_cache()
    eng = _engine(db_url)
    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng, future=True, expire_on_commit=False)()
    art = Path(artifact_dir or ARTIFACT_DIR)

    try:
        from simulation.generator.runner import run_simulation

        cfg = replace(
            SimConfig(seed=seed),
            n_cases=n_cases,
            customers_per_merchant=customers_per_merchant,
        )
        sim = run_simulation(db, cfg)
        store = GroundTruthStore.load(sim.run_id)

        tr = train_uplift_model(
            db, kind=kind, version=f"engine-eval-{seed}", seed=seed,
            ratios=DEFAULT_SPLIT, artifact_dir=art / "models",
        )
        predictor = load_for_model_version(tr.model_version)
        snapshots = _cycle1_snapshots(db)

        # score on the HELD-OUT test split only (case-level, unseen in
        # training) — same methodology as Phase 4's phase4_compare.
        ds = build_dataset(db, seed=seed, ratios=DEFAULT_SPLIT)
        test_case_ids = sorted({r.recovery_case_id for r in ds.rows_test})

        naive_pol = _naive_policy()
        rec_pol = _recoverai_policy()
        scores = {
            "naive_retry_once": _score_policy(
                name="naive_retry_once",
                choose=_naive_chooser(naive_pol),
                snapshots=snapshots, case_ids=test_case_ids, store=store, cfg=cfg,
            ),
            "recoverai": _score_policy(
                name="recoverai",
                choose=_recoverai_chooser(predictor, rec_pol),
                snapshots=snapshots, case_ids=test_case_ids, store=store, cfg=cfg,
            ),
        }

        oracle_mix: Counter = Counter()
        oracle_total = 0.0
        for cid in test_case_ids:
            gt = store.get(cid)
            if gt is None or not gt.cycles:
                continue
            ob = gt.oracle_best_action or "NO_ACTION"
            oracle_mix[ob] += 1
            po = PotentialOutcomes(
                case_index=0, p_by_action=gt.cycles[0].p_by_action,
                regime=gt.cycles[0].regime, amount=gt.payment_amount,
            )
            oracle_total += 0.0 if ob == "NO_ACTION" else eirv(po, ob, cfg=cfg)
        n_o = sum(oracle_mix.values()) or 1

        result = EngineEvalResult(
            seed=seed,
            n_cases=n_cases,
            customers_per_merchant=customers_per_merchant,
            dataset_config={
                "seed": seed,
                "n_cases": n_cases,
                "customers_per_merchant": customers_per_merchant,
                "n_merchants": cfg.n_merchants,
                "max_cycles": cfg.max_cycles,
                "costs": dict(DEFAULT_COSTS),
                "note": "SYNTHETIC — simulator parameters, not Razorpay data",
                "cases_scored": scores["recoverai"].n_cases,
            },
            model_version={
                "model_name": tr.model_version.model_name,
                "version": tr.model_version.version,
                "algorithm": tr.model_version.algorithm,
                "kind": kind,
                "training_dataset_snapshot_id": tr.model_version.training_dataset_snapshot_id,
                "feature_schema_id": tr.model_version.feature_schema_id,
                "status": tr.model_version.status,  # DRAFT — eval only, not promoted
            },
            oracle_action_mix={k: round(oracle_mix.get(k, 0) / n_o, 4) for k in _ACTIONS},
            oracle_total_eirv=round(oracle_total, 2),
            scores={k: v.as_dict() for k, v in scores.items()},
            generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )

        if write_artifact:
            art.mkdir(parents=True, exist_ok=True)
            out_path = art / f"phase5_engine_eval_{seed}_{n_cases}.json"
            out_path.write_text(json.dumps(result.as_dict(), indent=2), encoding="utf-8")
            result.dataset_config["artifact_path"] = str(out_path)

        return result
    finally:
        db.close()
        eng.dispose()
        try:
            GroundTruthStore.load(sim.run_id).path.unlink(missing_ok=True)
        except Exception:
            pass
        clear_cache()


def _print_report(r: EngineEvalResult) -> None:
    print("\n=== Phase 5 — Decision Engine vs Naive Baseline (oracle-scored) ===")
    print(f"seed={r.seed}  n_cases={r.n_cases}  cases_scored={r.dataset_config['cases_scored']}")
    print(f"model: {r.model_version['model_name']} {r.model_version['version']} "
          f"[{r.model_version['algorithm']}]  (status={r.model_version['status']}, eval-only)")
    print(f"oracle best-action mix : {r.oracle_action_mix}")
    print(f"oracle total EIRV      : {r.oracle_total_eirv}\n")
    cols = ["recovery_rate", "mean_realised_eirv", "total_realised_eirv",
            "no_action_frequency", "policy_blocks", "action_agreement_with_oracle",
            "mean_eirv_regret"]
    names = list(r.scores)
    w = 22
    print(f"{'metric':<28}" + "".join(f"{n:>{w}}" for n in names))
    for c in cols:
        row = f"{c:<28}"
        for n in names:
            row += f"{r.scores[n][c]:>{w}}"
        print(row)
    print("\naction mix:")
    for n in names:
        print(f"  {n:<20} {r.scores[n]['action_mix']}")
    ai = r.scores["recoverai"]["total_realised_eirv"]
    nv = r.scores["naive_retry_once"]["total_realised_eirv"]
    print(f"\nRecoverAI realised EIRV uplift vs naive: {round(ai - nv, 2)} "
          f"({round(ai, 2)} vs {round(nv, 2)}); oracle ceiling {r.oracle_total_eirv}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="simulation.evaluation.engine_eval")
    p.add_argument("--seed", type=int, default=DEFAULT_SEED)
    p.add_argument("--n-cases", type=int, default=DEFAULT_N_CASES)
    p.add_argument("--customers-per-merchant", type=int,
                   default=DEFAULT_CUSTOMERS_PER_MERCHANT)
    p.add_argument("--kind", default="t_learner",
                   choices=["s_learner", "t_learner", "tree_s_learner", "lgbm_s_learner"])
    p.add_argument("--no-artifact", action="store_true")
    args = p.parse_args(argv)

    res = run_engine_eval(
        seed=args.seed,
        n_cases=args.n_cases,
        customers_per_merchant=args.customers_per_merchant,
        kind=args.kind,
        write_artifact=not args.no_artifact,
    )
    _print_report(res)
    return 0


if __name__ == "__main__":
    sys.exit(main())
