"""Schemas for the Phase 1A.1 core entities."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from backend.models import enums

_ORM = ConfigDict(from_attributes=True)


class MerchantCreate(BaseModel):
    name: str
    industry: str | None = None
    currency: str = "INR"
    status: str = enums.MerchantStatus.ACTIVE.value


class MerchantRead(BaseModel):
    model_config = _ORM
    id: uuid.UUID
    display_id: str
    name: str
    status: str
    industry: str | None
    currency: str
    created_at: datetime


class PaymentCreate(BaseModel):
    merchant_id: uuid.UUID
    customer_id: str
    amount: Decimal = Field(gt=0)
    currency: str
    status: str = enums.PaymentStatus.CREATED.value
    external_payment_id: str | None = None
    payment_method: str | None = None
    payment_method_type: str | None = None


class PaymentRead(BaseModel):
    model_config = _ORM
    id: uuid.UUID
    display_id: str
    merchant_id: uuid.UUID
    customer_id: str
    external_payment_id: str | None
    amount: Decimal
    currency: str
    status: str
    payment_method: str | None


class PaymentEventAppend(BaseModel):
    event_type: str
    event_timestamp: datetime
    attempt_number: int | None = Field(default=None, ge=1)
    amount: Decimal | None = None
    currency: str | None = None
    provider_event_id: str | None = None
    metadata: dict | None = None


class PaymentEventRead(BaseModel):
    model_config = _ORM
    id: uuid.UUID
    payment_id: uuid.UUID
    event_type: str
    event_timestamp: datetime
    created_at: datetime
    attempt_number: int | None
    amount: Decimal | None
    provider_event_id: str | None


class RecoveryCaseRead(BaseModel):
    model_config = _ORM
    id: uuid.UUID
    display_id: str
    payment_id: uuid.UUID
    merchant_id: uuid.UUID
    status: str
    opened_at: datetime
    closed_at: datetime | None
    last_evaluated_at: datetime | None
    expires_at: datetime
    amount_at_risk: Decimal
    failure_category: str | None
