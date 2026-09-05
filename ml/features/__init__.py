"""Feature engineering — shared by training (``ml/training``) and inference
(``ml/inference``) so the two never drift apart.

Phase 3: the feature representation is the Phase 2 observable
``sim-feature-schema-v1`` snapshot (18 decision-time fields) plus the
candidate ``action`` as the S-learner treatment feature. Nothing hidden
(latent reliability, potential outcomes, oracle, regime, future events)
ever enters here — the snapshot is produced upstream by
``simulation/features.py::build_feature_snapshot`` which already runs a
leakage guard.
"""

from ml.features.schema import (
    ACTIONS,
    FEATURE_SCHEMA_ID,
    NUMERIC_FEATURES,
    CATEGORICAL_FEATURES,
    assert_snapshot_clean,
    column_names,
    feature_column_names,
    feature_matrix,
    features_only_matrix,
    vectorize,
    vectorize_features,
)

__all__ = [
    "ACTIONS",
    "FEATURE_SCHEMA_ID",
    "NUMERIC_FEATURES",
    "CATEGORICAL_FEATURES",
    "assert_snapshot_clean",
    "column_names",
    "feature_column_names",
    "feature_matrix",
    "features_only_matrix",
    "vectorize",
    "vectorize_features",
]
