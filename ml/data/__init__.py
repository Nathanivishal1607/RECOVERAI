"""Training-data access — builds an ML matrix from persisted, immutable
``TrainingExample`` rows (never from the simulator's hidden state)."""

from ml.data.dataset import (
    DatasetSplit,
    TrainingRow,
    build_dataset,
    load_training_rows,
    split_by_case,
)

__all__ = [
    "TrainingRow",
    "DatasetSplit",
    "load_training_rows",
    "split_by_case",
    "build_dataset",
]
