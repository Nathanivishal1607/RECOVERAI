# Synthetic Data & Simulator (Phase 2)

> **Disclaimer.** Synthetic simulator behavior is for hackathon
> demonstration and evaluation. It is not a representation of Razorpay's
> proprietary production data, pricing, or production recovery behavior.

## 1. Purpose

RecoverAI is an incremental‑value revenue‑recovery decision engine. To
build and evaluate it credibly without access to real large‑scale
transaction data, Phase 2 ships a **fast, deterministic simulation
environment** that:

- generates realistic merchants / customers / payments / failures;
- produces **hidden ground truth** — a causal recovery probability for
  **every** MVP action (`RETRY`, `MESSAGE`, `NO_ACTION`) on every
  recovery opportunity;
- reveals only the outcome of the action actually chosen, exactly as a
  real system would observe it;
- persists everything else through the **existing Phase 1B repositories
  and tables** — no new database models.

EIRV (Expected Incremental Recovery Value) remains the economic objective
and is computed by the decision engine, **not** by the simulator and
**not** by ML. The simulator only supplies observed `(features, action,
outcome)` data plus a hidden oracle for evaluation.

## 2. One‑command generation

```bash
# default: development size (~1,000 cases), seed 42, writes to settings.database_url
python -m simulation.cli generate

# sizes: small (100) | development (1,000) | training (10,000) | any integer
python -m simulation.cli generate --size training
python -m simulation.cli generate --size 250 --seed 7

# throwaway SQLite file, schema created first
python -m simulation.cli generate --database-url sqlite:///sim.db --reset

# scenario presets (see §11)
python -m simulation.cli generate --scenario multi_cycle
python -m simulation.cli scenarios          # list presets
```

Inside Docker:

```bash
docker compose exec backend python -m simulation.cli generate --size small
```

Hidden ground truth is written to
`simulation/ground_truth/runs/<run_id>.json` — **never** into the
application database.

## 3. Directory layout

```
simulation/
├── config.py              SimConfig (sizes, costs, rates, seed)
├── rng.py                 per-stream deterministic RNG (sha256(seed|key))
├── taxonomy.py            simulator failure categories + codes
├── features.py            observable feature snapshot builder + leakage guard
├── cli.py                 `python -m simulation.cli`
├── generator/
│   ├── entities.py        merchant / customer / payment specs (in-memory)
│   ├── naive_prior.py     blunt observable-only probability prior (placeholder "model")
│   ├── policy.py          CONTROL (retry-once) and TREATMENT (heuristic) policies
│   └── runner.py          persists a run through the Phase 1B repositories
├── ground_truth/
│   ├── potential_outcomes.py   hidden P(recovery | action) rules + oracle EIRV
│   ├── store.py                JSON sidecar writer/loader (NOT a DB model)
│   └── runs/                   generated <run_id>.json files (git-ignored)
├── scenarios/library.py   named SimConfig presets
└── evaluation/oracle.py   the ONLY sanctioned reader of ground truth
```

**Dependency rule:** `simulation/ground_truth/` and
`simulation/evaluation/` are never imported by `backend/` or `ml/`. The
production‑like decision pipeline cannot reach hidden truth.

## 4. Entities

| Entity | How it is generated | Persisted as |
|---|---|---|
| **Merchant** | `n_merchants` (default 3). Segment ∈ {`saas_subscription`, `ecommerce`, `utility_bills`, `edtech`}. Hidden `historical_recovery_rate` ∈ [0.28, 0.55], `avg_txn_amount` ∈ [₹400, ₹4000]. | `merchant` |
| **Customer** | `customers_per_merchant` (default 400). Hidden latent **`reliability` ~ Beta(2.2, 1.8)** clamped to [0.02, 1.0] drives ground truth and is **never observable**. Observable: tenure, payment frequency, historical success/failure rate, previous recovery rate, segment. | `customer` |
| **Payment** | One per case (`n_cases`). Amount ≈ log‑normal around the merchant average; currency `INR`; method ∈ {`UPI`, `CARD`, `NETBANKING`, `WALLET`}. Every simulated payment is created then **failed**; 15 % have one prior failed attempt. | `payment` + `payment_event` |
| **PaymentEvent** | Uses the **real internal vocabulary only**: `PAYMENT_CREATED`, `PAYMENT_FAILED`, `RETRY_ATTEMPTED`, `PAYMENT_SUCCEEDED`. `attempt_number` starts at 1 on `PAYMENT_FAILED` and increments on each `RETRY_ATTEMPTED`; `PAYMENT_CREATED` carries none. Append‑only. | `payment_event` |
| **RecoveryCase** | Opened for each failed payment whose amount ≥ ₹20 (recovery‑eligibility gate; smaller failures are counted as `cases_ineligible`). One active case per payment. | `recovery_case` (+ status history) |
| **ExperimentAssignment** | ~60 % of cases are assigned to an experiment; within those, ~50/50 `CONTROL` / `TREATMENT`. **Case‑level, one immutable arm per case**; cycles inherit it. Never forces an action. | `experiment_assignment` |
| **DecisionRecord / Prediction / Intervention / Outcome / TrainingExample** | Produced by the decision‑cycle loop (§8) through `DecisionCycleRepository` and `TrainingExampleRepository`. | respective Phase 1B tables |

A placeholder `ModelVersion` (`model_role="recovery_prediction"`,
status `VALIDATED`) is created per run so `Prediction` rows have a valid
FK. The "predictions" come from `naive_prior.py`, a deliberately blunt
observable‑only prior — a stand‑in until Phase 3 trains a real model.

## 5. Observable feature snapshot

`FEATURE_SCHEMA_ID = "sim-feature-schema-v1"` — the **only** feature
representation the decision/model pipeline may see. It contains
information available **at decision time** and nothing else. 18 features
across customer / payment / merchant:

```
customer : cust_tenure_days, cust_hist_success_rate, cust_hist_failure_rate,
           cust_prev_recovery_rate, cust_payment_freq_per_month, cust_segment
payment  : amount, currency, payment_method, failure_category, failure_code,
           attempt_number, minutes_since_last_attempt, hour_of_day, day_of_week
merchant : merchant_segment, merchant_hist_recovery_rate, merchant_avg_txn_amount
```

`build_feature_snapshot()` calls `assert_no_leakage()`, which raises if
any key matches a hidden‑data token (`potential`, `ground_truth`,
`reliability`, `outcome`, `recovered`, `recovery_amount`, `p_retry`,
`p_message`, `p_no_action`, `true_`). The same snapshot is stored on each
per‑action `Prediction` and copied onto `TrainingExample`.

## 6. Failure taxonomy — **simulator categories**

These are simulator categories, not a Razorpay taxonomy.

| Category | Mix | Simulated code | Meaning |
|---|---|---|---|
| `TEMPORARY` | 0.34 | `SIM_GATEWAY_TIMEOUT` | transient gateway/network issue; often self‑resolves or a retry works |
| `CUSTOMER_ACTION_REQUIRED` | 0.26 | `SIM_AUTH_REQUIRED` | needs the customer to do something (re‑auth, approve); a nudge helps |
| `PAYMENT_METHOD_ISSUE` | 0.18 | `SIM_INSTRUMENT_DECLINED` | instrument problem (expired card, bad method); a message to switch method helps |
| `LIMIT_EXCEEDED` | 0.12 | `SIM_LIMIT_EXCEEDED` | bank/velocity limit; may clear on its own within the window |
| `UNKNOWN` | 0.10 | `SIM_UNKNOWN` | no clear signal; low base recovery for every action |

## 7. Hidden potential outcomes (ground truth)

For **every** recovery opportunity the generator produces a hidden
probability for **all three** actions:

```
P(recovery | features, RETRY)
P(recovery | features, MESSAGE)
P(recovery | features, NO_ACTION)
```

**Deterministic rules, not ML.** Per‑category base rates are modulated
by interpretable factors:

- customer `reliability` (reliable customers self‑recover → lifts
  `NO_ACTION` most);
- amount friction (larger amounts slightly harder; `MESSAGE` least
  affected);
- attempt penalty (each prior failed attempt erodes `RETRY` most);
- merchant `historical_recovery_rate`.

**Regimes** then guarantee each action can strictly win on EIRV:

| `regime` | Trigger (rough) | Steers optimum toward |
|---|---|---|
| `no_action` | reliable customer (`reliability > 0.70`), small amount, `TEMPORARY`/`LIMIT_EXCEEDED` | `NO_ACTION` (intervening only adds cost) |
| `retry` | `TEMPORARY` + lower reliability | `RETRY` |
| `message` | `CUSTOMER_ACTION_REQUIRED` / `PAYMENT_METHOD_ISSUE` | `MESSAGE` |
| `mixed` | everything else | depends on the numbers |

Finally, per‑action Gaussian noise (`ground_truth_noise_sd = 0.06`) is
added and probabilities are clamped to **[0.01, 0.96]**. Every RNG draw
is seeded from `sha256(seed | "ground_truth" | case_index | attempt)`,
so the truth is reproducible.

**Oracle EIRV** (evaluation only):

```
EIRV(NO_ACTION) = 0
EIRV(a)         = (P(recovery|a) − P(recovery|NO_ACTION)) · amount − SIMULATED_<a>_COST
oracle_best_action = argmax over {RETRY, MESSAGE, NO_ACTION}
```

A `development` run (seed 42) yields an oracle best‑action mix of roughly
`RETRY 36 % / MESSAGE 55 % / NO_ACTION 9 %` — all three are common.

## 8. Observable vs hidden data

| Available to the decision/model pipeline | Hidden — simulator/evaluation only |
|---|---|
| `feature_snapshot` (§5) | customer latent `reliability` |
| `Prediction` rows (from `naive_prior`) | `P(recovery | action)` for all 3 actions |
| the **chosen** action's `Outcome` (recovered? amount? when?) | the counterfactual outcomes of the other two actions |
| `Intervention.execution_status` | `regime`, `oracle_best_action`, oracle EIRV |
| `TrainingExample` (label only on the observed, clean action) | per‑cycle realised recovery for un‑chosen actions |
| everything in the 17 Phase 1B tables | `simulation/ground_truth/runs/<run_id>.json` |

Ground truth is **not** written to `Payment`, `PaymentEvent`,
`RecoveryCase`, `Prediction`, `feature_snapshot`, `TrainingExample`, or
`DecisionRecord`. Tests assert no table/column is named after it and no
persisted snapshot contains a leakage token.

## 9. Outcome generation

Per decision cycle:

1. `effective_action` is an **outcome-sampling switch only** — it never
   changes `final_action`, `execution_status`, or `observed_action`. It
   equals the chosen action when the exposure is clean (`NO_ACTION`, or
   `Intervention.execution_status == ACCEPTED`); when a `RETRY`/`MESSAGE`
   execution is `REJECTED`/`FAILED` it falls back to `NO_ACTION` **for
   the probability draw only**, because the world evolved as if untreated.
   `final_action` stays `RETRY`/`MESSAGE`, the `Intervention` still exists
   with its `REJECTED`/`FAILED` status, and the cycle's `TrainingExample`
   still records `observed_action = RETRY`/`MESSAGE` but with
   `is_observed = false` and **no** `outcome_label` — a failed execution
   is not a clean observed treatment, and it is never relabelled as a
   `NO_ACTION` treatment. The three concepts stay distinct:

   | `final_action` | `execution_status` | `observed_action` | clean observed treatment? |
   |---|---|---|---|
   | `RETRY` | `ACCEPTED` | `RETRY` | yes |
   | `MESSAGE` | `ACCEPTED` | `MESSAGE` | yes |
   | `NO_ACTION` | — (no Intervention) | `NO_ACTION` | yes |
   | `RETRY`/`MESSAGE` | `REJECTED` / `FAILED` | `RETRY`/`MESSAGE` | no — no observed label |

2. `recovered = rng() < P(recovery | effective_action)`.
3. **Timing:** with probability `delayed_outcome_fraction` (0.45) the
   outcome is observed hours later, up to the case's `expires_at`;
   otherwise 2–55 minutes later. `Outcome.observed_at` therefore lags the
   decision, and tests assert delayed outcomes exist.
4. `recovery_amount` = full payment amount when `RECOVERED`, `0` when
   `NOT_RECOVERED`.
5. Terminal case status: `RECOVERED`, `EXPIRED` (window exhausted after
   `max_cycles` without recovery), or `STOPPED` (policy chose to stop).

## 10. Decision cycles & contracts

The runner drives each case through the real `RecoveryCase` state
machine and honours every Phase 1B invariant:

- **Multiple cycles** per case, each a **new immutable `DecisionRecord`**
  (`cycle_number` 1..n, unique per case). Historical cycles are never
  rewritten.
- **`NO_ACTION` creates no `Intervention`.** `Intervention.action` ∈
  {`RETRY`, `MESSAGE`} only.
- **Execution status ≠ recovery outcome.** `execution_status` ∈
  {`REQUESTED`, `ACCEPTED`, `REJECTED`, `FAILED`} (no `SUCCEEDED`);
  simulated rates: reject 0.05, fail 0.04, else accepted.
- **`Outcome` attaches to the `DecisionRecord`** (and to the
  `Intervention` when there was one).
- `ExperimentAssignment` is case‑level and inherited by every cycle;
  `CONTROL` uses a retry‑once baseline policy, `TREATMENT` an
  observable‑only heuristic. Neither overrides eligibility/policy, and
  `NO_ACTION` stays a candidate in both.
- `TrainingExample`: one row per `(DecisionRecord × candidate action)`;
  the label is set **only** on the action actually observed under a clean
  exposure — no counterfactual labels.

## 11. Scenario presets

`simulation/scenarios/library.py`:

| Name | What it changes |
|---|---|
| `default` | baseline `SimConfig` |
| `multi_cycle` | `max_cycles = 4` |
| `flaky_execution` | `exec_reject_rate = 0.15`, `exec_fail_rate = 0.12` |
| `full_experiment` | `experiment_fraction = 1.0` |
| `delayed_outcomes` | `delayed_outcome_fraction = 0.9` |

## 12. Simulator costs — **not Razorpay pricing**

Configurable via environment / `SimConfig`; used **only** by the
simulator's EIRV arithmetic and the oracle:

| Setting | Default | Notes |
|---|---|---|
| `SIMULATED_RETRY_COST` | `2.0` | simulation parameter only |
| `SIMULATED_MESSAGE_COST` | `3.0` | simulation parameter only |
| (`NO_ACTION`) | `0.0` | fixed |

These are **simulation parameters only. They are NOT Razorpay pricing**
and not a claim about actual Razorpay costs.

## 13. Determinism

- Default `seed = 42`.
- Every stochastic draw uses `simulation/rng.py::stream(seed, *key)` =
  `random.Random(int.from_bytes(sha256(seed | key)[:8]))`, so streams are
  independent and order‑insensitive.
- **The same configuration with the same seed produces equivalent
  synthetic data** — asserted by `tests/simulation/test_generation.py`
  (identical entity/decision fingerprint across two runs).

## 14. Dataset sizes & performance

| Size | Cases | Typical wall time |
|---|---|---|
| `small` | 100 | ~2 s (SQLite) / ~7 s (Postgres in Docker) |
| `development` (**default**) | 1,000 | ~25–30 s (SQLite) |
| `training` | 10,000 | a few minutes |

Generation is a one‑time offline step; the cost is SQLAlchemy
unit‑of‑work flushing, not the simulation maths.

## 15. Limitations & assumptions

- Ground‑truth rules are **interpretable heuristics with added noise**,
  not a learned or causal‑inference model. They are designed so the
  evaluation question ("does the uplift model recover the true ranking?")
  is answerable — not to match real recovery rates.
- Time‑of‑day / weekday are features but carry no causal effect in the
  current rules; intervention fatigue is modelled only as an `attempt`
  penalty.
- Delayed‑outcome timing is uniform within the remaining window, not a
  fitted hazard curve.
- The `naive_prior` "model" is a placeholder; real model training is
  Phase 3.
- Customer↔payment linkage is random within a merchant; no basket,
  seasonality, or churn dynamics.
- Costs, failure categories, and probabilities are **synthetic
  assumptions**, restated: not Razorpay pricing, taxonomy, or behaviour.

## 16. Tests

`tests/simulation/` (31 tests):

- **generation** — seed determinism, dataset size honoured, valid
  merchants/customers/payments, `PaymentEvent` vocabulary,
  `attempt_number` monotonicity, `--no-predictions` flag.
- **ground truth** — all three actions get probabilities; different
  scenarios make different actions optimal (all of `RETRY` / `MESSAGE` /
  `NO_ACTION` reachable); ground truth absent from feature snapshots, DB
  tables, and persisted `Prediction` snapshots; sidecar lives outside the
  database.
- **outcomes** — only the selected action produces an observed label;
  delayed outcomes exist; `NOT_RECOVERED` ⇒ amount 0; recovered amount =
  payment amount.
- **decision cycles** — multiple immutable cycles; `NO_ACTION` ⇒ no
  `Intervention`; rejected/failed execution ⇒ no clean label; interventions
  are `RETRY`/`MESSAGE` only.
- **contract compatibility** — data populates every Phase 1B table;
  `TrainingExample` derivation is idempotent and contract‑valid;
  experiment assignment is case‑level, single‑arm.
- **dependency rules** — no module under `backend/` or `ml/` imports
  `simulation.ground_truth` or `simulation.evaluation`.

Run: `python -m pytest tests/ -q` → **83 passed, 2 skipped** (the two
skips are the opt‑in PostgreSQL integration tests).

## 17. Generation → evaluation flow

```
SimConfig(seed) ─► entities (hidden reliability, merchant params)
                └► per case:
                     ground_truth.potential_outcomes  ─────────────┐  (hidden)
                     feature_snapshot (decision-time only)         │
                     decision cycles: Prediction → policy → final  │
                       action → Intervention? → Outcome (sampled   │
                       from hidden P) → TrainingExample            │
                                                                   ▼
   observed tables (17)  ──►  ml / decision pipeline      evaluation/oracle.py
                                                          (best_action_distribution,
                                                           realised_recovery_rate,
                                                           realised_incremental_value)
```

The oracle is the only component that opens
`simulation/ground_truth/runs/<run_id>.json`; nothing in `backend/`
imports it.
