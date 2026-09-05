"""Phase 4 model bake-off — reproducible, single entry point.

Lives under ``simulation/evaluation/`` because it is the ONLY place a
comparison may touch the simulator's hidden ground truth (via
``uplift_report`` / the ``Oracle``). Nothing under ``ml/`` or ``backend/``
imports this module — hidden truth flows strictly *out* to evaluation.

    from simulation.evaluation.phase4_compare import run_comparison
    result = run_comparison(seed=42, n_cases=1500)

Pipeline:
    simulator run (fixed seed / size)
      -> persist observable data via Phase 1B repos + hidden GT sidecar
      -> build case-level train/val/test dataset from TrainingExamples
      -> train candidates: s_learner, t_learner, tree_s_learner,
         lgbm_s_learner (if lightgbm installed)
      -> observational metrics on the TEST split (ml.evaluation.compare —
         no hidden truth)
      -> oracle decision-quality on the TEST-split cases (uplift_report)
      -> compact metrics table + JSON under
         simulation/evaluation/artifacts/phase4_comparison_<seed>_<n>.json

The Oracle is read ONLY after every model's predictions are produced.
No hidden value enters training features / labels / inference.
"""

from __future__ import annotations

import json
import statistics
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from backend.models import Base
from ml.data.dataset import build_dataset
from ml.evaluation.compare import observational_metrics
from ml.models.uplift import ALL_KINDS, build_model, lightgbm_available
from simulation.config import SimConfig
from simulation.evaluation.uplift_report import build_decision_quality
from simulation.generator.runner import run_simulation
from simulation.ground_truth.store import RUNS_DIR

ARTIFACT_DIR = Path(__file__).resolve().parent / "artifacts"

#: Documented reproducible default configuration (docs cite these numbers).
DEFAULT_SEED = 42
DEFAULT_N_CASES = 1500
DEFAULT_CUSTOMERS_PER_MERCHANT = 250
DEFAULT_SPLIT = (0.7, 0.15, 0.15)
#: Seeds averaged for the canonical multi-seed comparison / model selection.
DEFAULT_SEEDS = (42, 7, 123)


@dataclass
class Phase4Result:
    seed: int
    n_cases: int
    dataset: dict
    models: dict[str, dict]
    table_markdown: str
    selected_model: str
    selection_rationale: str
    generated_at: str
    seconds: float
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "seed": self.seed,
            "n_cases": self.n_cases,
            "dataset": self.dataset,
            "models": self.models,
            "table_markdown": self.table_markdown,
            "selected_model": self.selected_model,
            "selection_rationale": self.selection_rationale,
            "generated_at": self.generated_at,
            "seconds": self.seconds,
            "notes": self.notes,
        }


def _fresh_db(url: str = "sqlite://"):
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    eng = create_engine(url, connect_args=connect_args, future=True)
    if eng.dialect.name == "sqlite":
        @event.listens_for(eng, "connect")
        def _fk(dbapi_conn, _rec):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()
    Base.metadata.create_all(eng)
    return eng, sessionmaker(bind=eng, future=True, expire_on_commit=False)


def _fmt(x, nd=4):
    return "n/a" if x is None else f"{x:.{nd}f}"


def _build_table(models: dict[str, dict]) -> str:
    lines = [
        "| Model | Brier | ROC-AUC | ECE | Incremental MAE | Action Agreement "
        "| Mean EIRV Regret |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for kind, m in models.items():
        if m.get("skipped"):
            lines.append(f"| {kind} | _skipped: {m['skipped']}_ | | | | | |")
            continue
        o, d = m["observational"], m["decision_quality"]
        lines.append(
            f"| {kind} | {_fmt(o['brier'])} | {_fmt(o['roc_auc'])} | {_fmt(o['ece'])} "
            f"| {_fmt(d['incremental_mae'])} | {_fmt(d['action_agreement'])} "
            f"| {_fmt(d['mean_eirv_regret'], 2)} |"
        )
    return "\n".join(lines)


#: A model whose chosen-action mix is more concentrated than this on a
#: single action is "degenerate" — it isn't making a per-action
#: distinction, so a low EIRV regret is just the modal-action base rate.
_DEGENERATE_CONCENTRATION = 0.90


def _is_degenerate(dq: dict) -> bool:
    mix = dq.get("model_action_mix") or {}
    return bool(mix) and max(mix.values()) >= _DEGENERATE_CONCENTRATION


def _select(models: dict[str, dict]) -> tuple[str, str]:
    """Primary criterion = decision quality. Degenerate models (>90% of
    cases funnelled to one action) are excluded first — they don't make an
    incremental decision. Then: lowest mean EIRV regret, then highest
    action agreement, then lowest incremental MAE; Brier breaks a tie."""
    live = {k: v for k, v in models.items() if not v.get("skipped")}
    if not live:
        return "none", "no candidate trained"

    non_degenerate = {
        k: v for k, v in live.items() if not _is_degenerate(v["decision_quality"])
    }
    pool = non_degenerate or live
    excluded = sorted(set(live) - set(pool))

    def key(item):
        _, m = item
        d, o = m["decision_quality"], m["observational"]
        return (
            round(d["mean_eirv_regret"], 2),
            -round(d["action_agreement"], 4),
            round(d["incremental_mae"], 4),
            round(o["brier"] if o["brier"] is not None else 1.0, 4),
        )

    best_k, best_m = sorted(pool.items(), key=key)[0]
    d = best_m["decision_quality"]
    rationale = (
        f"lowest mean EIRV regret ({d['mean_eirv_regret']:.2f}) among "
        f"non-degenerate candidates, with action agreement "
        f"{d['action_agreement']:.3f} and incremental MAE "
        f"{d['incremental_mae']:.4f}; decision quality is the primary Phase 4 "
        f"criterion (not ROC-AUC alone)."
    )
    if excluded:
        rationale += (
            f" Excluded as degenerate (>{int(_DEGENERATE_CONCENTRATION*100)}% "
            f"of cases to one action): {', '.join(excluded)}."
        )
    return best_k, rationale


def run_comparison(
    *,
    seed: int = DEFAULT_SEED,
    n_cases: int = DEFAULT_N_CASES,
    customers_per_merchant: int = DEFAULT_CUSTOMERS_PER_MERCHANT,
    split: tuple[float, float, float] = DEFAULT_SPLIT,
    write_artifact: bool = True,
    db_url: str = "sqlite://",
) -> Phase4Result:
    t0 = time.perf_counter()
    eng, Session = _fresh_db(db_url)
    db = Session()
    try:
        cfg = SimConfig(seed=seed, n_cases=n_cases,
                        customers_per_merchant=customers_per_merchant)
        sim = run_simulation(db, cfg)

        ds = build_dataset(db, seed=seed, ratios=split)
        test_case_ids = sorted({r.recovery_case_id for r in ds.rows_test})

        models: dict[str, dict] = {}
        for kind in ALL_KINDS:
            if kind == "lgbm_s_learner" and not lightgbm_available():
                models[kind] = {"skipped": "lightgbm not installed"}
                continue
            try:
                model = build_model(kind, ds.rows_train, seed=seed)
            except Exception as exc:  # pragma: no cover - defensive
                models[kind] = {"skipped": f"{type(exc).__name__}: {exc}"}
                continue
            obs = observational_metrics(model, ds.rows_test, name=kind).as_dict()
            dq = build_decision_quality(
                db, run_id=sim.run_id, model=model, model_name=kind,
                test_case_ids=test_case_ids, cfg=cfg,
            ).as_dict()
            models[kind] = {"observational": obs, "decision_quality": dq}

        table = _build_table(models)
        selected, rationale = _select(models)

        result = Phase4Result(
            seed=seed,
            n_cases=n_cases,
            dataset={
                "sim_run_id": sim.run_id,
                "cases_created": sim.cases_created,
                "training_examples": sim.training_examples,
                "n_train": ds.n_train,
                "n_val": ds.n_val,
                "n_test": ds.n_test,
                "n_test_cases": len(test_case_ids),
                "snapshot_id": ds.snapshot_id,
                "split_ratios": list(split),
                "customers_per_merchant": customers_per_merchant,
            },
            models=models,
            table_markdown=table,
            selected_model=selected,
            selection_rationale=rationale,
            generated_at=datetime.now(timezone.utc).isoformat(),
            seconds=round(time.perf_counter() - t0, 2),
        )

        if write_artifact:
            ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
            path = ARTIFACT_DIR / f"phase4_comparison_{seed}_{n_cases}.json"
            path.write_text(json.dumps(result.as_dict(), indent=2))
            result.notes.append(f"artifact written: {path}")

        try:
            (RUNS_DIR / f"{sim.run_id}.json").unlink(missing_ok=True)
        except Exception:
            pass
        return result
    finally:
        db.close()
        eng.dispose()


def run_multi_seed(
    *,
    seeds: tuple[int, ...] = DEFAULT_SEEDS,
    n_cases: int = DEFAULT_N_CASES,
    customers_per_merchant: int = DEFAULT_CUSTOMERS_PER_MERCHANT,
    write_artifact: bool = True,
) -> dict:
    """Run ``run_comparison`` for each seed and aggregate (mean ± stdev) the
    key metrics per model. Selection is by mean EIRV regret, then mean
    action agreement — decision quality, not ROC-AUC."""
    per_seed: list[dict] = []
    for s in seeds:
        r = run_comparison(seed=s, n_cases=n_cases,
                           customers_per_merchant=customers_per_merchant,
                           write_artifact=False)
        per_seed.append(r.as_dict())

    agg: dict[str, dict] = {}
    for kind in ALL_KINDS:
        rows = [ps["models"][kind] for ps in per_seed
                if kind in ps["models"] and not ps["models"][kind].get("skipped")]
        if not rows:
            agg[kind] = {"skipped": "not evaluated on any seed"}
            continue

        def col(path_a, path_b):
            return [r[path_a][path_b] for r in rows if r[path_a][path_b] is not None]

        def ms(vals):
            return {
                "mean": round(statistics.fmean(vals), 4) if vals else None,
                "stdev": round(statistics.pstdev(vals), 4) if len(vals) > 1 else 0.0,
            }

        agg[kind] = {
            "brier": ms(col("observational", "brier")),
            "roc_auc": ms(col("observational", "roc_auc")),
            "ece": ms(col("observational", "ece")),
            "incremental_mae": ms(col("decision_quality", "incremental_mae")),
            "action_agreement": ms(col("decision_quality", "action_agreement")),
            "mean_eirv_regret": ms(col("decision_quality", "mean_eirv_regret")),
        }

    live = {k: v for k, v in agg.items() if not v.get("skipped")}
    # a model degenerate on >= half the seeds is excluded from selection
    def _degen_frac(kind: str) -> float:
        seen = [ps["models"][kind] for ps in per_seed
                if kind in ps["models"] and not ps["models"][kind].get("skipped")]
        if not seen:
            return 1.0
        return sum(
            1 for m in seen if _is_degenerate(m["decision_quality"])
        ) / len(seen)

    non_degen = {k: v for k, v in live.items() if _degen_frac(k) < 0.5}
    pool = non_degen or live
    excluded = sorted(set(live) - set(pool))
    selected = min(
        pool,
        key=lambda k: (
            round(pool[k]["mean_eirv_regret"]["mean"], 2),
            -round(pool[k]["action_agreement"]["mean"], 4),
            round(pool[k]["incremental_mae"]["mean"], 4),
        ),
    ) if pool else "none"

    lines = [
        "| Model | Brier | ROC-AUC | ECE | Incremental MAE | Action Agreement "
        "| Mean EIRV Regret |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for kind, m in agg.items():
        if m.get("skipped"):
            lines.append(f"| {kind} | _skipped_ | | | | | |")
            continue
        g = lambda k, nd=4: f"{m[k]['mean']:.{nd}f}+/-{m[k]['stdev']:.{nd}f}"
        lines.append(
            f"| {kind} | {g('brier')} | {g('roc_auc')} | {g('ece')} "
            f"| {g('incremental_mae')} | {g('action_agreement')} "
            f"| {g('mean_eirv_regret', 2)} |"
        )
    table = "\n".join(lines)

    out = {
        "seeds": list(seeds),
        "n_cases": n_cases,
        "customers_per_merchant": customers_per_merchant,
        "per_seed": per_seed,
        "aggregate": agg,
        "table_markdown": table,
        "selected_model": selected,
        "excluded_as_degenerate": excluded,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    if write_artifact:
        ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
        path = ARTIFACT_DIR / f"phase4_comparison_multiseed_{n_cases}.json"
        path.write_text(json.dumps(out, indent=2))
        out["artifact_path"] = str(path)
    return out


if __name__ == "__main__":  # pragma: no cover
    import argparse

    ap = argparse.ArgumentParser(prog="simulation.evaluation.phase4_compare")
    ap.add_argument("--seed", type=int, default=None,
                    help="single-seed run (default: multi-seed 42,7,123)")
    ap.add_argument("--n-cases", type=int, default=DEFAULT_N_CASES)
    ap.add_argument("--no-artifact", action="store_true")
    args = ap.parse_args()
    if args.seed is not None:
        res = run_comparison(seed=args.seed, n_cases=args.n_cases,
                             write_artifact=not args.no_artifact)
        print(res.table_markdown)
        print("\nselected:", res.selected_model)
        print("rationale:", res.selection_rationale)
        for n in res.notes:
            print("-", n)
    else:
        out = run_multi_seed(n_cases=args.n_cases,
                             write_artifact=not args.no_artifact)
        print(out["table_markdown"])
        print("\nselected:", out["selected_model"])
        if "artifact_path" in out:
            print("artifact:", out["artifact_path"])
