"""Ground-truth store — kept OUT of the application database.

Hidden per-action potential outcomes and per-cycle realisations are
written to a JSON sidecar file under ``simulation/ground_truth/runs/``,
keyed by ``recovery_case_id``. Only ``simulation/evaluation`` loads it.
The backend / decision pipeline never imports this module.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

RUNS_DIR = Path(__file__).resolve().parent / "runs"


@dataclass
class CycleTruth:
    cycle_number: int
    attempt_number: int
    observed_action: str
    p_by_action: dict[str, float]      # hidden — for THIS cycle's context
    regime: str
    realised_recovered: bool
    realised_amount: float
    clean_exposure: bool


@dataclass
class CaseGroundTruth:
    recovery_case_id: str
    case_display_id: str
    payment_amount: float
    failure_category: str
    experiment_arm: str | None
    oracle_best_action: str | None = None
    cycles: list[CycleTruth] = field(default_factory=list)


class GroundTruthStore:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self._by_case: dict[str, CaseGroundTruth] = {}

    def add(self, gt: CaseGroundTruth) -> None:
        self._by_case[gt.recovery_case_id] = gt

    def get(self, recovery_case_id: str) -> CaseGroundTruth | None:
        return self._by_case.get(recovery_case_id)

    def setdefault_case(
        self,
        *,
        recovery_case_id: str,
        case_display_id: str,
        payment_amount: float,
        failure_category: str,
        experiment_arm: str | None,
        oracle_best_action: str | None = None,
    ) -> CaseGroundTruth:
        gt = self._by_case.get(recovery_case_id)
        if gt is None:
            gt = CaseGroundTruth(
                recovery_case_id=recovery_case_id,
                case_display_id=case_display_id,
                payment_amount=payment_amount,
                failure_category=failure_category,
                experiment_arm=experiment_arm,
                oracle_best_action=oracle_best_action,
            )
            self._by_case[recovery_case_id] = gt
        return gt

    def __len__(self) -> int:
        return len(self._by_case)

    @property
    def path(self) -> Path:
        return RUNS_DIR / f"{self.run_id}.json"

    def save(self) -> Path:
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "run_id": self.run_id,
            "note": (
                "SIMULATOR GROUND TRUTH — hackathon demo/evaluation only. "
                "NOT Razorpay production data, pricing, or recovery behaviour. "
                "Never fed to the model/decision/feature pipeline."
            ),
            "cases": {cid: asdict(gt) for cid, gt in self._by_case.items()},
        }
        self.path.write_text(json.dumps(payload, indent=2))
        return self.path

    @classmethod
    def load(cls, run_id: str) -> "GroundTruthStore":
        store = cls(run_id)
        raw = json.loads((RUNS_DIR / f"{run_id}.json").read_text())
        for cid, gt in raw["cases"].items():
            cycles = [CycleTruth(**c) for c in gt.pop("cycles", [])]
            store._by_case[cid] = CaseGroundTruth(cycles=cycles, **gt)
        return store
