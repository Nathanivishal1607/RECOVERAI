"""Simulator configuration.

Everything is a knob so runs are reproducible and tunable. Cost values
are **simulation parameters only** — NOT Razorpay pricing.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace

#: Named dataset sizes (number of RecoveryCases).
DATASET_SIZES: dict[str, int] = {
    "small": 100,
    "development": 1_000,
    "training": 10_000,
}


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, ""))
    except ValueError:
        return default


@dataclass(frozen=True)
class SimConfig:
    # --- reproducibility -------------------------------------------------
    seed: int = 42

    # --- volume -------------------------------------------------------
    n_cases: int = DATASET_SIZES["development"]
    n_merchants: int = 3
    customers_per_merchant: int = 400

    # --- costs (SIMULATION PARAMETERS ONLY — not Razorpay pricing) ---
    retry_cost: float = field(default_factory=lambda: _env_float("SIMULATED_RETRY_COST", 2.0))
    message_cost: float = field(default_factory=lambda: _env_float("SIMULATED_MESSAGE_COST", 3.0))
    no_action_cost: float = 0.0

    # --- scenario shape --------------------------------------------
    max_cycles: int = 3
    recovery_window_days: int = 14
    #: fraction of RETRY/MESSAGE dispatches that don't cleanly execute
    exec_reject_rate: float = 0.05
    exec_fail_rate: float = 0.04
    #: fraction of cases enrolled in the CONTROL/TREATMENT experiment
    experiment_fraction: float = 0.6
    control_fraction_within_experiment: float = 0.5
    #: fraction of recovered outcomes that are observed after a delay
    delayed_outcome_fraction: float = 0.45
    #: additive Gaussian noise sd on the hidden per-action probabilities
    ground_truth_noise_sd: float = 0.06
    #: also write placeholder (naive-prior) Predictions + derive TrainingExamples
    with_predictions: bool = True

    def cost_for(self, action: str) -> float:
        return {
            "RETRY": self.retry_cost,
            "MESSAGE": self.message_cost,
            "NO_ACTION": self.no_action_cost,
        }[action]

    def with_size(self, name_or_n: str | int) -> "SimConfig":
        if isinstance(name_or_n, int):
            return replace(self, n_cases=name_or_n)
        return replace(self, n_cases=DATASET_SIZES[name_or_n])
