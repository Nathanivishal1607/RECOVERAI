"""Phase 5 — the offline decision-engine evaluation harness
(``simulation.evaluation.engine_eval``).

Checks that it produces the full metrics bundle, that RecoverAI beats the
naive baseline on decision quality, and that a run is deterministic for a
fixed seed. Also re-asserts the hidden-ground-truth boundary: nothing
under ``backend/`` or ``ml/`` imports the harness.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from simulation.evaluation.engine_eval import run_engine_eval

# small config: fast, still large enough for a non-empty case-level test split
_SEED = 42
_N_CASES = 400
_CUST = 120


@pytest.fixture(scope="module")
def eval_result():
    return run_engine_eval(
        seed=_SEED, n_cases=_N_CASES, customers_per_merchant=_CUST,
        write_artifact=False,
    )


def test_metrics_bundle_present(eval_result):
    r = eval_result.as_dict()
    assert set(r["scores"]) == {"naive_retry_once", "recoverai"}
    for name in ("naive_retry_once", "recoverai"):
        s = r["scores"][name]
        for key in (
            "recovery_rate", "total_realised_eirv", "mean_realised_eirv",
            "action_mix", "no_action_frequency", "policy_blocks",
            "action_agreement_with_oracle", "mean_eirv_regret", "total_eirv_regret",
        ):
            assert key in s, f"{name} missing {key}"
        assert set(s["action_mix"]) == {"RETRY", "MESSAGE", "NO_ACTION"}
    # model was trained but NOT promoted (eval only)
    assert r["model_version"]["status"] == "DRAFT"
    assert r["dataset_config"]["seed"] == _SEED
    assert r["dataset_config"]["cases_scored"] > 0


def test_recoverai_beats_naive_on_decision_quality(eval_result):
    naive = eval_result.scores["naive_retry_once"]
    rec = eval_result.scores["recoverai"]
    # naive always retries; RecoverAI uses a real action mix
    assert naive["action_mix"]["RETRY"] == 1.0
    assert rec["action_mix"]["MESSAGE"] > 0.0
    # RecoverAI agrees with the oracle more and regrets less
    assert rec["action_agreement_with_oracle"] > naive["action_agreement_with_oracle"]
    assert rec["mean_eirv_regret"] < naive["mean_eirv_regret"]


def test_engine_eval_is_deterministic():
    a = run_engine_eval(seed=7, n_cases=400, customers_per_merchant=120,
                        write_artifact=False)
    b = run_engine_eval(seed=7, n_cases=400, customers_per_merchant=120,
                        write_artifact=False)
    assert a.scores == b.scores
    assert a.oracle_action_mix == b.oracle_action_mix
    assert a.oracle_total_eirv == b.oracle_total_eirv


def test_engine_eval_not_imported_by_backend_or_ml():
    root = Path(__file__).resolve().parents[2]
    target = "simulation.evaluation.engine_eval"
    offenders: list[str] = []
    for pkg in ("backend", "ml"):
        for path in (root / pkg).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                mods: list[str] = []
                if isinstance(node, ast.Import):
                    mods = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    mods = [node.module or ""]
                if any(m == target or m.startswith(target + ".") for m in mods):
                    offenders.append(str(path.relative_to(root)))
    assert not offenders, f"engine_eval imported by: {offenders}"
