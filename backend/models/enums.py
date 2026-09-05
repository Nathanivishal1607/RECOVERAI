"""Controlled vocabularies — the finalized Phase 1A MVP enums.

These are the *only* valid values. They are enforced as CHECK constraints
in the ORM and re-tested in tests/backend. Do not add values without a
documented contract change (see docs/decisions/architecture-decisions.md).
"""

from __future__ import annotations

import enum


class MerchantStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class PaymentStatus(str, enum.Enum):
    """Lean internal payment lifecycle (Phase 1A.1 / ADR-009).

    Provider states are *mapped* to these — never passed through raw.
    """

    CREATED = "CREATED"
    PROCESSING = "PROCESSING"
    FAILED = "FAILED"
    SUCCEEDED = "SUCCEEDED"
    CANCELLED = "CANCELLED"


class PaymentEventType(str, enum.Enum):
    """Authoritative, append-only payment-lifecycle event vocabulary."""

    PAYMENT_CREATED = "PAYMENT_CREATED"
    PAYMENT_FAILED = "PAYMENT_FAILED"
    RETRY_ATTEMPTED = "RETRY_ATTEMPTED"
    PAYMENT_SUCCEEDED = "PAYMENT_SUCCEEDED"
    PAYMENT_CANCELLED = "PAYMENT_CANCELLED"


class RecoveryCaseStatus(str, enum.Enum):
    OPEN = "OPEN"
    ANALYZING = "ANALYZING"
    ACTION_SELECTED = "ACTION_SELECTED"
    ACTION_EXECUTED = "ACTION_EXECUTED"
    WAITING_FOR_OUTCOME = "WAITING_FOR_OUTCOME"
    # terminal
    RECOVERED = "RECOVERED"
    STOPPED = "STOPPED"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"


TERMINAL_CASE_STATUSES: frozenset[str] = frozenset(
    {
        RecoveryCaseStatus.RECOVERED.value,
        RecoveryCaseStatus.STOPPED.value,
        RecoveryCaseStatus.EXPIRED.value,
        RecoveryCaseStatus.FAILED.value,
    }
)

# Terminal statuses whose cases carry a usable ML outcome (FAILED excluded).
LABELLABLE_TERMINAL_STATUSES: frozenset[str] = frozenset(
    {
        RecoveryCaseStatus.RECOVERED.value,
        RecoveryCaseStatus.STOPPED.value,
        RecoveryCaseStatus.EXPIRED.value,
    }
)


class Action(str, enum.Enum):
    """The MVP candidate action set."""

    RETRY = "RETRY"
    MESSAGE = "MESSAGE"
    NO_ACTION = "NO_ACTION"


#: Actions that, as a final action, produce an Intervention row.
EXECUTABLE_ACTIONS: frozenset[str] = frozenset({Action.RETRY.value, Action.MESSAGE.value})


class PolicyResult(str, enum.Enum):
    ALLOWED = "ALLOWED"
    BLOCKED = "BLOCKED"


class ExecutionStatus(str, enum.Enum):
    """Intervention execution status.

    There is intentionally NO ``SUCCEEDED`` — whether a recovery attempt
    "succeeded" is an :class:`OutcomeResult` question, not an execution one.
    """

    REQUESTED = "REQUESTED"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


class OutcomeResult(str, enum.Enum):
    RECOVERED = "RECOVERED"
    NOT_RECOVERED = "NOT_RECOVERED"


class ExperimentArm(str, enum.Enum):
    CONTROL = "CONTROL"
    TREATMENT = "TREATMENT"


class ExperimentStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    RUNNING = "RUNNING"
    ENDED = "ENDED"


class ModelVersionStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    PROMOTED = "PROMOTED"
    RETIRED = "RETIRED"
    REJECTED = "REJECTED"


#: Allowed ModelVersion lifecycle transitions (Phase 1A.3 / ADR-011).
#: A REJECTED version can never become PROMOTED; DRAFT must pass VALIDATED.
MODEL_VERSION_TRANSITIONS: dict[str, frozenset[str]] = {
    ModelVersionStatus.DRAFT.value: frozenset(
        {ModelVersionStatus.VALIDATED.value, ModelVersionStatus.REJECTED.value}
    ),
    ModelVersionStatus.VALIDATED.value: frozenset(
        {ModelVersionStatus.PROMOTED.value, ModelVersionStatus.REJECTED.value}
    ),
    ModelVersionStatus.PROMOTED.value: frozenset({ModelVersionStatus.RETIRED.value}),
    ModelVersionStatus.RETIRED.value: frozenset(),
    ModelVersionStatus.REJECTED.value: frozenset(),
}


class DecisionRecordStatus(str, enum.Enum):
    DECIDED = "DECIDED"
    EXECUTING = "EXECUTING"
    RESOLVED = "RESOLVED"


def values(e: type[enum.Enum]) -> list[str]:
    """String values of an enum, for CHECK constraints."""
    return [m.value for m in e]
