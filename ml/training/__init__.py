"""Training pipeline: persisted TrainingExamples -> fitted model ->
artifact -> immutable ``ModelVersion`` (status DRAFT).

* ``train_recovery_model``  — Phase 3 S-learner (logistic regression).
* ``train_uplift_model``    — Phase 4: any incremental candidate
                              (s_learner / t_learner / tree_s_learner /
                              lgbm_s_learner) via the kind-tagged artifact.
"""

from ml.training.train import TrainResult, train_recovery_model
from ml.training.uplift import UpliftTrainResult, train_uplift_model

__all__ = [
    "TrainResult",
    "train_recovery_model",
    "UpliftTrainResult",
    "train_uplift_model",
]
