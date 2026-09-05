"""Model evaluation — **observational only**.

Metrics here are computed from held-out ``TrainingExample`` rows (the
observed action's outcome per cycle). This package must never import
``simulation.ground_truth`` / ``simulation.evaluation`` — the simulator
oracle / hidden-truth decision-quality report lives in
``simulation/evaluation/model_report.py`` (the sanctioned ground-truth
reader), keeping hidden truth flowing *out* to evaluation, never *in*.
"""

from ml.evaluation.evaluate import (
    ActionSeparation,
    EvalReport,
    evaluate_model,
    predictive_metrics,
)
from ml.evaluation.compare import ObsMetrics, observational_metrics

__all__ = [
    "EvalReport",
    "ActionSeparation",
    "predictive_metrics",
    "evaluate_model",
    "ObsMetrics",
    "observational_metrics",
]
