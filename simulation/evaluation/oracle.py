"""Evaluation oracle — the ONLY sanctioned reader of simulator ground
truth.

Loads a run's hidden potential outcomes and answers counterfactual
questions the observational data cannot ("what would MESSAGE have done?").
Nothing in ``backend/`` imports this module; nothing here writes to the
application database.
"""

from __future__ import annotations

from collections import Counter

from simulation.config import SimConfig
from simulation.ground_truth.potential_outcomes import PotentialOutcomes, eirv
from simulation.ground_truth.store import GroundTruthStore


class Oracle:
    def __init__(self, store: GroundTruthStore, cfg: SimConfig) -> None:
        self._store = store
        self._cfg = cfg

    @classmethod
    def for_run(cls, run_id: str, cfg: SimConfig | None = None) -> "Oracle":
        return cls(GroundTruthStore.load(run_id), cfg or SimConfig())

    # --- counterfactual lookups ------------------------------------

    def potential_outcomes(self, recovery_case_id: str, cycle_number: int = 1):
        gt = self._store.get(recovery_case_id)
        if gt is None:
            return None
        for c in gt.cycles:
            if c.cycle_number == cycle_number:
                return c.p_by_action
        return gt.cycles[0].p_by_action if gt.cycles else None

    def best_action(self, recovery_case_id: str) -> str | None:
        gt = self._store.get(recovery_case_id)
        return gt.oracle_best_action if gt else None

    # --- aggregate reports --------------------------------------

    def best_action_distribution(self) -> dict[str, int]:
        c = Counter(
            gt.oracle_best_action
            for gt in self._store._by_case.values()
            if gt.oracle_best_action
        )
        return dict(c)

    def realised_recovery_rate(self) -> float:
        cases = list(self._store._by_case.values())
        if not cases:
            return 0.0
        rec = sum(1 for gt in cases if any(cy.realised_recovered for cy in gt.cycles))
        return round(rec / len(cases), 4)

    def realised_incremental_value(self) -> float:
        """Sum over cases of oracle EIRV of the action actually taken in the
        cycle that resolved the case (0 for NO_ACTION / unresolved)."""
        total = 0.0
        for gt in self._store._by_case.values():
            for cy in gt.cycles:
                po = PotentialOutcomes(
                    case_index=0, p_by_action=cy.p_by_action, regime=cy.regime,
                    amount=gt.payment_amount,
                )
                if cy.observed_action != "NO_ACTION" and cy.clean_exposure:
                    total += eirv(po, cy.observed_action, cfg=self._cfg)
        return round(total, 2)
