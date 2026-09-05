"""Named scenario presets — thin wrappers over :class:`SimConfig`.

A scenario just fixes a few knobs so a demo run has a predictable shape.
"""

from __future__ import annotations

from dataclasses import replace

from simulation.config import SimConfig


def _base(seed: int) -> SimConfig:
    return SimConfig(seed=seed)


SCENARIOS = {
    # balanced demo dataset — a mix of all regimes / best actions
    "default": lambda seed: _base(seed),
    # heavier re-evaluation: more multi-cycle cases
    "multi_cycle": lambda seed: replace(_base(seed), max_cycles=4),
    # stress execution failures (REJECTED / FAILED interventions)
    "flaky_execution": lambda seed: replace(
        _base(seed), exec_reject_rate=0.15, exec_fail_rate=0.12
    ),
    # everyone in the CONTROL/TREATMENT experiment
    "full_experiment": lambda seed: replace(
        _base(seed), experiment_fraction=1.0
    ),
    # mostly delayed outcomes
    "delayed_outcomes": lambda seed: replace(
        _base(seed), delayed_outcome_fraction=0.9
    ),
}


def get_scenario(name: str, *, seed: int) -> SimConfig:
    if name not in SCENARIOS:
        raise KeyError(f"unknown scenario {name!r}; options: {sorted(SCENARIOS)}")
    return SCENARIOS[name](seed)
