"""Phase 4 — evaluation isolation & determinism.

* observational metrics (ml.evaluation.compare) use no simulator truth
* the oracle report / bake-off live under simulation/evaluation/ only
* the comparison is deterministic for a fixed seed
* no ml/** module imports simulation.ground_truth / simulation.evaluation
"""

from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from backend.models import Base
from ml.data.dataset import build_dataset
from ml.evaluation.compare import observational_metrics
from ml.models.uplift import build_model
from simulation.config import SimConfig
from simulation.generator.runner import run_simulation

_REPO = Path(__file__).resolve().parents[2]
_FORBIDDEN = ("simulation.ground_truth", "simulation.evaluation")
_PHASE4_ML_MODULES = [
    "ml/models/uplift.py",
    "ml/models/artifact.py",
    "ml/evaluation/compare.py",
    "ml/training/uplift.py",
    "ml/inference/recovery.py",
]


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


@pytest.mark.parametrize("rel", _PHASE4_ML_MODULES)
def test_phase4_ml_module_has_no_hidden_truth_import(rel):
    mods = _imports(_REPO / rel)
    bad = [m for m in mods if any(m == f or m.startswith(f + ".") for f in _FORBIDDEN)]
    assert not bad, f"{rel} imports hidden ground truth: {bad}"


def test_ml_evaluation_compare_takes_only_training_rows():
    """`observational_metrics` signature carries no oracle / ground-truth
    parameter — it only sees TrainingRow objects."""
    import inspect

    sig = inspect.signature(observational_metrics)
    params = set(sig.parameters)
    assert params == {"model", "rows", "name"}


@pytest.fixture(scope="module")
def small_ds():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False})

    @event.listens_for(eng, "connect")
    def _fk(c, _):
        cur = c.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng, future=True, expire_on_commit=False)()
    run_simulation(
        db, replace(SimConfig(seed=23), n_cases=450, customers_per_merchant=120)
    )
    ds = build_dataset(db, seed=23)
    try:
        yield ds
    finally:
        db.close()
        eng.dispose()


def test_observational_metrics_deterministic(small_ds):
    m = build_model("t_learner", small_ds.rows_train, seed=23)
    a = observational_metrics(m, small_ds.rows_test, name="t_learner").as_dict()
    b = observational_metrics(m, small_ds.rows_test, name="t_learner").as_dict()
    assert a == b
    assert a["n"] == len(small_ds.rows_test)
    assert set(a["mean_proba_by_action"]) == {"RETRY", "MESSAGE", "NO_ACTION"}


def test_oracle_report_module_lives_under_simulation():
    # the sanctioned readers exist where they should
    from simulation.evaluation.uplift_report import build_decision_quality
    from simulation.evaluation.phase4_compare import run_comparison

    assert callable(build_decision_quality)
    assert callable(run_comparison)
