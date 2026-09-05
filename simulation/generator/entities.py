"""In-memory generation of merchants, customers and per-case payment specs.

These plain dataclasses are the *inputs* to both:
  * the observable feature snapshot (``simulation/features.py``), and
  * the hidden potential-outcome generator (``simulation/ground_truth/``).

Everything here is deterministic given ``SimConfig.seed``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from simulation.config import SimConfig
from simulation.rng import stream
from simulation.taxonomy import CATEGORY_MIX, CATEGORY_CODE

EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)

MERCHANT_SEGMENTS = ["saas_subscription", "ecommerce", "utility_bills", "edtech"]
CUSTOMER_SEGMENTS = ["new", "casual", "regular", "loyal"]
METHODS = ["UPI", "CARD", "NETBANKING", "WALLET"]


@dataclass(frozen=True)
class MerchantSpec:
    index: int
    display_name: str
    segment: str
    historical_recovery_rate: float
    avg_txn_amount: float
    monthly_volume: int


@dataclass(frozen=True)
class CustomerSpec:
    customer_id: str
    merchant_index: int
    segment: str
    tenure_days: int
    payment_frequency_per_month: float
    hist_success_rate: float
    hist_failure_rate: float
    prev_recovery_rate: float
    #: hidden latent "reliability" 0..1 — drives ground truth, NOT observable
    reliability: float


@dataclass(frozen=True)
class PaymentSpec:
    case_index: int
    merchant_index: int
    customer_id: str
    amount: float
    currency: str
    method: str
    failure_category: str
    failure_code: str
    created_at: datetime
    failed_at: datetime
    #: prior failed attempts on this payment before recovery starts (0 or 1)
    initial_attempts: int


def generate_merchants(cfg: SimConfig) -> list[MerchantSpec]:
    out: list[MerchantSpec] = []
    for i in range(cfg.n_merchants):
        r = stream(cfg.seed, "merchant", i)
        seg = MERCHANT_SEGMENTS[i % len(MERCHANT_SEGMENTS)]
        out.append(
            MerchantSpec(
                index=i,
                display_name=f"Sim {seg.title().replace('_', ' ')} {i + 1}",
                segment=seg,
                historical_recovery_rate=round(r.uniform(0.28, 0.55), 3),
                avg_txn_amount=round(r.uniform(400, 4000), 2),
                monthly_volume=r.randint(2_000, 60_000),
            )
        )
    return out


def generate_customers(cfg: SimConfig, merchants: list[MerchantSpec]) -> list[CustomerSpec]:
    out: list[CustomerSpec] = []
    for m in merchants:
        for j in range(cfg.customers_per_merchant):
            r = stream(cfg.seed, "customer", m.index, j)
            reliability = min(1.0, max(0.02, r.betavariate(2.2, 1.8)))
            seg = CUSTOMER_SEGMENTS[min(3, int(reliability * 4))]
            success = round(0.55 + 0.4 * reliability + r.uniform(-0.08, 0.08), 3)
            success = min(0.99, max(0.3, success))
            out.append(
                CustomerSpec(
                    customer_id=f"C-{m.index:02d}-{j:05d}",
                    merchant_index=m.index,
                    segment=seg,
                    tenure_days=r.randint(5, 1400),
                    payment_frequency_per_month=round(r.uniform(0.5, 12.0), 2),
                    hist_success_rate=success,
                    hist_failure_rate=round(1 - success, 3),
                    prev_recovery_rate=round(min(0.95, max(0.05, 0.4 * reliability + r.uniform(0, 0.35))), 3),
                    reliability=round(reliability, 4),
                )
            )
    return out


def _pick(r, weights: dict[str, float]) -> str:
    keys = list(weights)
    return r.choices(keys, weights=[weights[k] for k in keys], k=1)[0]


def generate_payment_specs(
    cfg: SimConfig, merchants: list[MerchantSpec], customers: list[CustomerSpec]
) -> list[PaymentSpec]:
    by_merchant: dict[int, list[CustomerSpec]] = {}
    for c in customers:
        by_merchant.setdefault(c.merchant_index, []).append(c)

    out: list[PaymentSpec] = []
    for k in range(cfg.n_cases):
        r = stream(cfg.seed, "payment", k)
        m = merchants[k % len(merchants)]
        cust = r.choice(by_merchant[m.index])
        # amount: log-normal-ish around the merchant average
        amount = round(max(20.0, r.lognormvariate(0, 0.6) * m.avg_txn_amount * 0.5), 2)
        cat = _pick(r, CATEGORY_MIX)
        created = EPOCH + timedelta(days=r.randint(0, 300), minutes=r.randint(0, 1440))
        failed = created + timedelta(minutes=r.randint(1, 30))
        out.append(
            PaymentSpec(
                case_index=k,
                merchant_index=m.index,
                customer_id=cust.customer_id,
                amount=amount,
                currency="INR",
                method=r.choice(METHODS),
                failure_category=cat,
                failure_code=CATEGORY_CODE[cat],
                created_at=created,
                failed_at=failed,
                initial_attempts=1 if r.random() < 0.15 else 0,
            )
        )
    return out
