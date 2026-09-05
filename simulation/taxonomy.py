"""Simulator failure taxonomy.

These are **simulator categories**, not Razorpay's production failure
taxonomy. Each influences the hidden per-action recovery probabilities
differently (see ``simulation/ground_truth/potential_outcomes.py``).
"""

from __future__ import annotations

import enum


class FailureCategory(str, enum.Enum):
    TEMPORARY = "TEMPORARY"
    CUSTOMER_ACTION_REQUIRED = "CUSTOMER_ACTION_REQUIRED"
    PAYMENT_METHOD_ISSUE = "PAYMENT_METHOD_ISSUE"
    LIMIT_EXCEEDED = "LIMIT_EXCEEDED"
    UNKNOWN = "UNKNOWN"


#: How often each category occurs among failed payments (sums to 1).
CATEGORY_MIX: dict[str, float] = {
    FailureCategory.TEMPORARY.value: 0.34,
    FailureCategory.CUSTOMER_ACTION_REQUIRED.value: 0.26,
    FailureCategory.PAYMENT_METHOD_ISSUE.value: 0.18,
    FailureCategory.LIMIT_EXCEEDED.value: 0.12,
    FailureCategory.UNKNOWN.value: 0.10,
}

#: A short human-readable failure code per category (goes into
#: PaymentEvent.metadata / RecoveryCase.failure_code — observable).
CATEGORY_CODE: dict[str, str] = {
    FailureCategory.TEMPORARY.value: "SIM_GATEWAY_TIMEOUT",
    FailureCategory.CUSTOMER_ACTION_REQUIRED.value: "SIM_AUTH_REQUIRED",
    FailureCategory.PAYMENT_METHOD_ISSUE.value: "SIM_INSTRUMENT_DECLINED",
    FailureCategory.LIMIT_EXCEEDED.value: "SIM_LIMIT_EXCEEDED",
    FailureCategory.UNKNOWN.value: "SIM_UNKNOWN",
}

CATEGORY_NOTES: dict[str, str] = {
    FailureCategory.TEMPORARY.value: "transient — RETRY tends to do well",
    FailureCategory.CUSTOMER_ACTION_REQUIRED.value: "needs the customer — MESSAGE tends to do better",
    FailureCategory.PAYMENT_METHOD_ISSUE.value: "instrument problem — MESSAGE may beat immediate RETRY",
    FailureCategory.LIMIT_EXCEEDED.value: "limit/funds — mixed; often needs time",
    FailureCategory.UNKNOWN.value: "unclassified — lower recovery overall",
}
