"""Phase 2 — the production-like pipeline must not be able to reach hidden
ground truth. Static check: nothing under ``backend/`` or ``ml/`` imports
``simulation.ground_truth`` or ``simulation.evaluation``."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FORBIDDEN = ("simulation.ground_truth", "simulation.evaluation")
_GUARDED_TREES = ("backend", "ml")


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


@pytest.mark.parametrize("tree", _GUARDED_TREES)
def test_backend_and_ml_never_import_hidden_ground_truth(tree: str):
    root = _REPO_ROOT / tree
    if not root.exists():
        pytest.skip(f"{tree}/ not present")
    offenders: list[str] = []
    for py in root.rglob("*.py"):
        for mod in _imports(py):
            if any(mod == f or mod.startswith(f + ".") for f in _FORBIDDEN):
                offenders.append(f"{py.relative_to(_REPO_ROOT)} imports {mod}")
    assert not offenders, "hidden ground truth leaked into the pipeline:\n" + "\n".join(
        offenders
    )


def test_evaluation_oracle_is_the_documented_reader():
    """The oracle is allowed to read ground truth; confirm the wiring exists."""
    from simulation.evaluation.oracle import Oracle

    assert hasattr(Oracle, "for_run")
    assert hasattr(Oracle, "best_action_distribution")
