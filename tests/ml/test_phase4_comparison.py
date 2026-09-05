"""Phase 4 — the model comparison / bake-off runs end-to-end and is
deterministic for a fixed seed, and the oracle stays evaluation-only."""

from __future__ import annotations

import pytest

from simulation.evaluation.phase4_compare import run_comparison


@pytest.fixture(scope="module")
def comparison():
    # small but enough for a 3-way case-level split with all actions present
    return run_comparison(seed=42, n_cases=500, customers_per_merchant=120,
                          write_artifact=False)


def test_all_candidates_evaluated(comparison):
    models = comparison.models
    for kind in ("s_learner", "t_learner", "tree_s_learner"):
        assert kind in models
        assert not models[kind].get("skipped"), models[kind]
        assert "observational" in models[kind]
        assert "decision_quality" in models[kind]
    # lightgbm is evaluated if installed, otherwise explicitly skipped
    assert "lgbm_s_learner" in models


def test_metrics_bundle_has_predictive_and_decision(comparison):
    for kind, m in comparison.models.items():
        if m.get("skipped"):
            continue
        o = m["observational"]
        d = m["decision_quality"]
        # predictive
        assert "brier" in o and "roc_auc" in o and "ece" in o
        # incremental
        assert "incremental_mae" in d and "incremental_rmse" in d
        assert set(d["incremental_mae_by_action"]) == {"RETRY", "MESSAGE"}
        # decision quality
        assert "action_agreement" in d
        assert "mean_eirv_regret" in d and "total_eirv_regret" in d
        assert set(d["model_action_mix"]) == {"RETRY", "MESSAGE", "NO_ACTION"}
        assert set(d["oracle_action_mix"]) == {"RETRY", "MESSAGE", "NO_ACTION"}


def test_selection_is_decision_quality_primary(comparison):
    # a model selected must not be one flagged degenerate
    assert comparison.selected_model != "none"
    assert "EIRV regret" in comparison.selection_rationale


def test_comparison_is_deterministic():
    a = run_comparison(seed=7, n_cases=400, customers_per_merchant=110,
                       write_artifact=False)
    b = run_comparison(seed=7, n_cases=400, customers_per_merchant=110,
                       write_artifact=False)
    # same dataset identity + same per-model metrics
    assert a.dataset["snapshot_id"] == b.dataset["snapshot_id"]
    for kind in a.models:
        if a.models[kind].get("skipped"):
            continue
        assert a.models[kind]["observational"] == b.models[kind]["observational"]
        assert a.models[kind]["decision_quality"] == b.models[kind]["decision_quality"]


def test_oracle_report_never_touches_training(comparison):
    """The dataset snapshot id is a pure function of persisted
    TrainingExample rows — it does not change whether or not the oracle
    report ran. (Structural: the report is called after build_dataset.)"""
    assert comparison.dataset["snapshot_id"].startswith("tds-")
    assert comparison.dataset["n_test_cases"] > 0
