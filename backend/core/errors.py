"""Domain-level exceptions for data-contract rules that the DB can't
express directly (enforced in the repository/service layer + tests)."""

from __future__ import annotations


class DataContractError(Exception):
    """Base class for a violation of a finalized Phase 1A data contract."""


class ImmutableRecordError(DataContractError):
    """Attempt to mutate a record that the contract declares immutable
    (Prediction, DecisionRecord, PolicyEvaluation, resolved Outcome,
    ExperimentAssignment, Policy rule fields, ModelVersion non-status
    fields, any PaymentEvent)."""


class InvalidTransitionError(DataContractError):
    """Illegal lifecycle transition (e.g. ModelVersion REJECTED -> PROMOTED,
    RecoveryCase terminal -> anything)."""


class ActiveCaseExistsError(DataContractError):
    """A payment already has an active (non-terminal) RecoveryCase."""


class PromotedModelExistsError(DataContractError):
    """A model role already has a PROMOTED ModelVersion."""


class ExperimentAlreadyAssignedError(DataContractError):
    """A RecoveryCase already has an ExperimentAssignment (case-level, once)."""
