"""Phase 3 — hidden-ground-truth isolation is preserved.

The Phase 2 static check (tests/simulation/test_dependency_rules.py) already
asserts nothing under backend/ or ml/ imports simulation.ground_truth /
simulation.evaluation. These tests re-assert it for the NEW Phase 3
modules specifically, and confirm persisted predictions stay clean.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_FORBIDDEN = ("simulation.ground_truth", "simulation.evaluation")
_PHASE3_MODULES = [
    "ml/features/schema.py",
    "ml/data/dataset.py",
    "ml/models/recovery_model.py",
    "ml/training/train.py",
    "ml/evaluation/evaluate.py",
    "ml/inference/recovery.py",
    "ml/cli.py",
    "backend/decision_engine/orchestrator.py",
    "backend/decision_engine/value_engine.py",
    "backend/decision_engine/optimizer.py",
    "backend/policies/engine.py",
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


@pytest.mark.parametrize("rel", _PHASE3_MODULES)
def test_phase3_module_does_not_import_hidden_truth(rel):
    mods = _imports(_REPO / rel)
    bad = [m for m in mods if any(m == f or m.startswith(f + ".") for f in _FORBIDDEN)]
    assert not bad, f"{rel} imports hidden ground truth: {bad}"


def test_policy_engine_does_not_import_decision_engine_or_ml():
    mods = _imports(_REPO / "backend/policies/engine.py")
    assert not any(m.startswith("backend.decision_engine") for m in mods)
    assert not any(m == "ml" or m.startswith("ml.") for m in mods)


def test_persisted_predictions_have_no_hidden_tokens(ml_run):
    from backend.models.decision import Prediction

    forbidden = ("reliability", "p_by_action", "regime", "oracle", "potential",
                 "recovered", "recovery_amount", "true_", "p_retry", "p_message")
    preds = ml_run["db"].query(Prediction).limit(500).all()
    assert preds
    for p in preds:
        keys = {k.lower() for k in (p.feature_snapshot or {})}
        assert not any(any(tok in k for tok in forbidden) for k in keys)
