# RecoverAI Documentation

This directory is the **canonical specification** for RecoverAI. If a prior
conversation, a comment, or this README's own prose conflicts with an
approved document below, the approved document wins. If you find a
conflict that isn't resolvable from existing docs, it must be flagged and
resolved before implementation proceeds — see `decisions/architecture-decisions.md`.

## How to navigate this

| If you want to know... | Go to |
|---|---|
| What problem we're solving and why | `product/problem-statement.md` |
| Who uses this and how | `product/users.md`, `product/use-cases.md` |
| What's actually in the MVP vs later | `product/mvp-scope.md` |
| The MVP intervention set (`RETRY`/`MESSAGE`/`NO_ACTION`) and future channels | `product/mvp-scope.md`, `integrations/messaging.md` |
| The overall system shape | `architecture/system-architecture.md` |
| How one payment failure flows through the system | `architecture/decision-flow.md` |
| How PII/financial data is protected | `architecture/privacy-architecture.md` |
| How money-moving actions are kept safe | `architecture/security-and-safety.md` |
| Database tables / entities | `data/database-schema.md`, `data/data-model.md` |
| Core data contract (Merchant/Payment/PaymentEvent/RecoveryCase), identity, payment status, event vocabulary, eligibility | `data/data-model.md` "Core data contract — Phase 1A.1" |
| Decision data contract (Prediction/DecisionRecord/PolicyEvaluation/Intervention/Outcome), execution status, EIRV persistence, decision audit questions | `data/data-model.md` "Decision data contract — Phase 1A.2" |
| Model/Policy/Experiment contract (ModelVersion lifecycle, Policy versioning, ExperimentAssignment CONTROL/TREATMENT) | `data/data-model.md` "Model, Policy & Experiment data contract — Phase 1A.3" |
| Training data contract (TrainingExample: DecisionRecord × action, no counterfactual labels, case-level splitting, dataset snapshot) | `data/data-model.md` "Training data contract — Phase 1A.4", `ml/labels.md` |
| What a "Recovery Case" is and its state machine | `data/data-model.md` |
| `DecisionRecord`, `ModelVersion`, `Policy`, `Experiment`, `ExperimentAssignment` | `data/data-model.md` (Phase 1A.2 / 1A.3 sections) |
| Prediction vs. Recommendation vs. Execution | `data/data-model.md`, `decision-engine/decision-engine.md` |
| How synthetic data is generated | `data/synthetic-data.md` |
| What features feed the models | `ml/features.md` |
| What the models predict and how | `ml/models.md` |
| Why incremental value ≠ raw recovery | `ml/uplift-modelling.md` |
| How a decision actually gets made | `decision-engine/decision-engine.md` |
| How money-safety rules are enforced | `decision-engine/policy-engine.md` |
| Razorpay/webhook integration details | `integrations/razorpay.md`, `integrations/webhooks.md` |
| Dashboard/UX plan | `frontend/dashboard.md` |
| How to run this locally | `development/setup.md` |
| Why we chose X over Y | `decisions/architecture-decisions.md` |

## Product identity

**RecoverAI** — an AI Revenue Recovery Decision Engine for merchants, built
for the Razorpay AI Buildathon, Track 3 (AI Revenue Recovery).

One-line pitch:

> When a payment fails, RecoverAI decides whether intervention is worth it,
> which intervention maximizes incremental recovered revenue, and when to
> stop — under merchant policy, privacy, and financial-safety constraints.

## Development phases

This is the authoritative phase list. Do not start a phase until the
previous one is reported complete and the next phase is explicitly
requested.

| Phase | Name | Status |
|---|---|---|
| 0 | Initialization (repo, docs, dev setup) | ✅ Complete |
| 0.5 | Specification Correction Pass (docs only) | ✅ Complete (see ADR-007) |
| 0.6 | Data-Contract Clarification Pass (docs only) | ✅ Complete (see ADR-008) |
| 1A.1 | Core Data Contract — `Merchant`, `Payment`, `PaymentEvent`, `RecoveryCase` (docs only) | ✅ Complete (see ADR-009) |
| 1A.2 | Decision Data Contract — `Prediction`, `DecisionRecord`, `PolicyEvaluation`, `Intervention`, `Outcome` (docs only) | ✅ Complete (see ADR-010) |
| 1A.3 | Model, Policy & Experiment Data Contract — `ModelVersion`, `Policy`, `Experiment`, `ExperimentAssignment` (docs only) | ✅ Complete (see ADR-011) |
| 1A.4 | Training Data Contract — `TrainingExample` (docs only) | ✅ Complete (see ADR-012) — **Phase 1A data contract complete** |
| 1B | Data Layer Implementation (SQLAlchemy models, Alembic migration, Pydantic schemas, DB repositories, tests) | ✅ Complete — **17 application tables** (18 in a migrated PostgreSQL DB incl. `alembic_version`) in `backend/models/`, migration `0001_initial_schema`, `backend/repositories/` + `backend/schemas/`, 54 tests (SQLite unit + PostgreSQL integration), runs via `docker compose` |
| 2 | Simulator & Synthetic Data — deterministic generator, hidden per-action ground truth (JSON sidecar), observable feature snapshots, multi-cycle decision generation through the Phase 1B repositories | ✅ Complete — `simulation/` (`config`, `rng`, `taxonomy`, `features`, `generator/`, `ground_truth/`, `scenarios/`, `evaluation/`, `cli`), one-command `python -m simulation.cli generate`, 31 new tests (83 passed / 2 skipped total), runs in `docker compose`. See `data/synthetic-data.md`. |
| 3 | ML Model, Training & Decision-Engine Integration | ✅ Complete — logistic-regression S-learner trained from `TrainingExample` (case-level split, no leakage) → `ModelVersion` (DRAFT→VALIDATED→PROMOTED) + joblib artifact; deterministic decision engine (`backend/decision_engine/`, `backend/policies/`) EIRV → recommendation → policy veto → `DecisionRecord` with 3 `Prediction`s. `python -m ml.cli train`. 119 tests / 2 skipped. See ADR-013. |
| 4 | Incremental / Uplift Modelling | ✅ Complete — S-learner (Phase 3) + **T-learner** (per-action logistic heads) + tree-S-learner + LightGBM-S-learner candidates, common `incremental()` interface (`P(a) − P(NO_ACTION)`, never stored in `Prediction`, never replaces EIRV). Reproducible multi-seed bake-off vs the simulator oracle (`python -m simulation.evaluation.phase4_compare`) on predictive + **decision-quality** (EIRV regret, action agreement) metrics. **Selected: T-learner** (agreement 0.73, EIRV regret 54 vs S-learner 0.56 / 95). Feeds the unchanged Decision Engine via the existing `ModelVersion` + `ml.inference` path. 145 tests / 2 skipped. See ADR-014. |
| 5 | Decision Engine (value + optimizer + policy) — end-to-end MVP: recovery flow service, decision audit view, HTTP API, offline evaluation, demo scenarios | ✅ Complete — `backend/services/recovery_flow.py` wires PaymentEvent → eligibility → RecoveryCase → `DecisionEngine` → Intervention → mock execute → Outcome → `TrainingExample`; `backend/schemas/audit.py` + `backend/api/routes/recovery.py` expose the full decision chain (`POST /payments`, `/payments/{id}/evaluate`, `GET /decisions/{id}`, `/cases/{id}`, `/decisions/{id}/execute`, `/decisions/{id}/outcome`, `/cases/{id}/reevaluate`); `simulation/scenarios/demo_cases.py` + `scripts/demo.py` run 5 deterministic scenarios (RETRY / MESSAGE / NO_ACTION best, policy block, re-evaluation); `simulation/evaluation/engine_eval.py` scores RecoverAI vs a naive retry-once baseline on the held-out test split against the oracle. **164 tests / 2 skipped.** |
| 6 | Backend (FastAPI, Postgres, Redis, case lifecycle) | Done in Phase 5 (FastAPI routes + case lifecycle via the flow service) + the Phase 11 read API below; Redis / async workers / webhook ingress still to come |
| 7 | Learning Loop (retraining + validation + promotion) | Not started |
| 8 | Razorpay Integration (webhooks, verified APIs) | Not started |
| 9 | LLM Layer (investigation/explanation/messaging) | Explanation slice ✅ (NVIDIA NIM, default `openai/gpt-oss-20b`, `GET /api/recovery-cases/{id}/explanation`) — see `architecture/decision-flow.md`; investigation/messaging drafting not started |
| 10 | Recovery Channels (WhatsApp / SMS / Email message channels + voice extension) | Not started |
| 11 | Dashboard | ✅ Complete — Next.js frontend (dashboard, recovery-cases list, case-detail audit trail) + read-only `GET /api/dashboard`, `/api/recovery-cases`, `/api/recovery-cases/{id}` API; idempotent demo-data seed wired into `docker compose up`. **173 tests / 2 skipped.** |
| 12 | End-to-End Demo | Not started as a separate phase — see the "Hackathon demo walkthrough" in `README.md`, which already covers this using Phase 11's frontend |
| 13 | Hackathon Polish | Not started |

### Phase 1A — Data Contract Finalization (documentation/design)

A checkpoint before the database schema becomes code.

**1A.1 is complete** — the core contract for `Merchant`, `Payment`,
`PaymentEvent`, `RecoveryCase` (identity strategy, lean payment-status +
provider mapping, `PaymentEvent` vocabulary + append-only rule,
recovery-eligibility gate, "at most one active case per payment", state
machine core fields). See `data/data-model.md` "Core data contract" and
ADR-009.

**1A.2 is complete** — the decision-lifecycle contract for `Prediction`,
`DecisionRecord`, `PolicyEvaluation`, `Intervention`, `Outcome`
(per-action predictions bound to exact `ModelVersion`; Prediction ≠ EIRV ≠
Recommendation ≠ Final action ≠ Intervention; `PolicyEvaluation` as a
distinct per-candidate record; `NO_ACTION` never creates an `Intervention`;
`execution_status` ≠ `Outcome` ≠ `RecoveryCase.status`; multiple immutable
`DecisionRecord`s per case; EIRV inputs/outputs persisted for
reproducibility). See `data/data-model.md` "Decision data contract" and
ADR-010.

**1A.3 is complete** — the model/policy/experiment contract for
`ModelVersion` (immutable except lifecycle `status`
DRAFT/VALIDATED/PROMOTED/RETIRED/REJECTED, one `PROMOTED` per model role,
reproducibility metadata), `Policy` (immutable per version, per merchant;
structured data vs the fixed Policy Engine), and `Experiment` /
`ExperimentAssignment` (assigned once at the `RecoveryCase` level,
immutable; `CONTROL`/`TREATMENT` never force an action or bypass
policy/eligibility; supersedes `recovery_case.experiment_arm`). See
`data/data-model.md` "Model, Policy & Experiment data contract" and
ADR-011.

**1A.4 is complete** — the training data contract for `TrainingExample`:
one row per `DecisionRecord × candidate action`; a `Prediction` is not an
observed outcome, so an `outcome_label` is written **only** for the
actually-observed action (no counterfactual labels); `NO_ACTION` cycles
produce valid rows without an `Intervention`; repeated cycles each
contribute rows; `RecoveryCase` is the grouping unit for **case-level**
train/val/test splitting; features are frozen as of the `DecisionRecord`
(no leakage); the training set for a model is a reproducible dataset
snapshot referenced by `ModelVersion`. See `data/data-model.md` "Training
data contract" and ADR-012.

**Phase 1A (the data contract) is complete.**

### Phase 1B — Data Layer Implementation (✅ COMPLETE)

The finalized Phase 1A contract is realized in code:

- **`backend/models/`** — SQLAlchemy 2.0 ORM for all **17 application
  tables**: the 14 finalized Phase 1A entities — `merchant`, `payment`,
  `payment_event`, `recovery_case`, `model_prediction` (`Prediction`),
  `decision_record`, `policy_evaluation`, `intervention`, `outcome`,
  `policy`, `model_version`, `experiment`, `experiment_assignment`,
  `training_example` — plus 3 supporting tables: `customer` (FK target,
  from the Phase 0 sketch), `recovery_case_status_history` (the
  append-only status audit trail required by ADR-009), and
  `display_id_sequence` (backs `display_id` generation). A migrated
  PostgreSQL database also contains Alembic's own `alembic_version`
  bookkeeping table (18 total). `enums.py` holds the controlled
  vocabularies.
- **`backend/database/`** — lazy engine/session, portable `GUID`/JSON
  types (native on PostgreSQL, SQLite-friendly for tests).
- **`backend/repositories/`** — use-case-shaped data access; enforces the
  rules the DB can't (append-only `PaymentEvent`, RecoveryCase state
  machine, `ModelVersion` lifecycle, NO_ACTION↛Intervention, label only on
  observed action).
- **`backend/schemas/`** — Pydantic read/create models, ORM-free.
- **`backend/alembic/`** — migration `0001_initial_schema`; the backend
  container runs `alembic upgrade head` on start-up, then uvicorn.
- **Tests:** 52 unit (in-memory SQLite) + 2 PostgreSQL integration =
  **54 passing**, locally and inside Docker. `docker compose up` →
  Postgres → migration → backend → `/health` (`db: ok`).

Contract deviations: **none.** Physical implementation choices (SQL types,
partial-index invariants, one-row-per-`(cycle, action)` `training_example`,
dropped `experiment_arm` column) are documented in
`data/database-schema.md` §3b.

### Phase 3 — ML Model, Training & Decision-Engine Integration (✅ COMPLETE)

The MVP learning loop is implemented end-to-end (see ADR-013):

- **`ml/features/`** — `sim-feature-schema-v1` vectorizer shared by
  training and inference: the 18 decision-time snapshot fields + a
  one-hot `candidate_action` (the S-learner treatment feature). Runs a
  leakage guard; unknown categoricals → all-zero one-hot.
- **`ml/data/`** — builds `(X, y, groups)` from persisted
  `TrainingExample` rows, **observed & labelled rows only** (no
  manufactured counterfactuals); **case-level** train/val/test split by
  `recovery_case_id`; deterministic `tds-<n>-<hash>` dataset-snapshot id.
- **`ml/models/`** — `RecoveryModel`: `StandardScaler → LogisticRegression`
  (`liblinear`, fixed seed) over `[features ⊕ one-hot(action)]`.
  `predict(features, action) → P(recovery)`. Saved as a joblib artifact
  under `ml/models/artifacts/` (git-ignored) + a sha256 checksum.
- **`ml/training/`** — `train_recovery_model(db)`: split → fit → write
  artifact → evaluate → `ModelVersionRepository.create(status=DRAFT)`
  with `training_dataset_snapshot_id`, `feature_schema_id`,
  `artifact_ref`/`artifact_checksum`, `training_config`, `random_seed`,
  `evaluation_summary`. No new table; **no `model_version_id` on
  `DecisionRecord`** — `Prediction → exact ModelVersion` unchanged.
- **`ml/inference/`** — loads a model **from its immutable `ModelVersion`**
  (verifies the checksum), exposes `predict_all_actions(snapshot)`.
  `load_promoted(db)` returns the one `PROMOTED` model for the role.
- **`ml/evaluation/`** — observational metrics on the held-out split
  (ROC-AUC, log loss, Brier, coarse ECE) + per-action mean-probability
  separation. Imports **no** simulator hidden truth.
- **`simulation/evaluation/model_report.py`** — the sanctioned
  ground-truth reader: compares the decision engine's argmax-EIRV action
  vs the oracle's best action (evaluation only; coarse cycle-1 proxy
  snapshot — see ADR-013 limitations).
- **`backend/decision_engine/`** — `value_engine` (EIRV, ADR-003
  formula; `NO_ACTION` EIRV ≡ 0), `optimizer` (rank by EIRV, `NO_ACTION`
  always retained), `orchestrator` (`DecisionEngine.run_cycle`): model
  probs → 3 `Prediction`s → EIRV → recommendation → **policy veto loop**
  → `DecisionRecord` (recommended & final stored separately,
  `value_context` per action, one `PolicyEvaluation` per candidate
  checked) → `Intervention` **only** for `RETRY`/`MESSAGE`.
- **`backend/policies/engine.py`** — deterministic `check_policy(action,
  policy, ctx)`; `NO_ACTION` unconditionally `ALLOWED` (guarantees loop
  termination); hard binary checks (retry cap, contact cap, channel,
  amount limits, risk flag); **zero imports** from `decision_engine`/`ml`.
- **CLI:** `python -m ml.cli train [--promote] [--version V] [--seed N]`.
- **Tests:** +36 (dataset eligibility & case-level split & no-leakage,
  model training, three-action inference, `ModelVersion` creation &
  lifecycle & one-`PROMOTED`-per-role, artifact persistence/loading &
  checksum, inference determinism, EIRV & ranking, policy veto,
  `NO_ACTION`↛`Intervention`, immutable historical `DecisionRecord`,
  hidden-ground-truth isolation, full end-to-end learning loop). Suite:
  **119 passed / 2 skipped**.

Contract deviations: **none.** Intentionally deferred (ADR-013): the
retrain-scheduler / promotion-gating automation (Phase 7) and a
production-side feature builder replacing the simulator snapshot.

### Phase 4 — Incremental / Uplift Modelling (✅ COMPLETE)

Extends the ML layer to per-action *incremental* recovery estimation
(see ADR-014). No new persistence entity, no data-model change, no
Decision-Engine authority change.

- **`ml/models/uplift.py`** — a common `IncrementalModel` interface
  (`predict_all_actions(snapshot)` + `incremental(snapshot)` =
  `P(recovery|a) − P(recovery|NO_ACTION)`) over four candidates:
  `s_learner` (the Phase 3 `RecoveryModel`), **`t_learner`** (one
  `StandardScaler→LogisticRegression` head per action, each trained
  ONLY on that action's observed rows; actions with no/single-class
  rows fall back to a base rate), `tree_s_learner` (shallow
  `DecisionTreeClassifier` S-learner — the clean "tree candidate", no
  custom causal framework), `lgbm_s_learner` (deterministic
  action-conditioned LightGBM, if `lightgbm` is installed).
  `incremental()` is **derived**, never written to
  `Prediction.recovery_probability` and never a substitute for EIRV.
- **`ml/models/artifact.py`** — kind-tagged joblib artifacts + sha256, so
  any candidate loads through the existing `ml.inference` /
  `ModelVersion` path (v1 Phase-3 artifacts still load).
- **`ml/training/uplift.py`** — `train_uplift_model(db, kind=...)`: same
  case-level split, deterministic dataset-snapshot id (now a **content**
  hash, reproducible across simulator runs), one immutable artifact +
  checksum on a `DRAFT` `ModelVersion` (role `recovery_prediction`).
  `python -m ml.cli train --kind t_learner --promote`.
- **`ml/evaluation/compare.py`** — observational metrics (Brier, ROC-AUC,
  ECE, per-action separation) on the held-out split. **No** simulator
  hidden truth (imports neither `simulation.ground_truth` nor
  `simulation.evaluation`).
- **`simulation/evaluation/uplift_report.py`** + **`phase4_compare.py`**
  — the sanctioned ground-truth readers: per-case predicted vs oracle
  *incremental* probability (MAE/RMSE), model EIRV-argmax action vs
  oracle best action (agreement), and per-case **EIRV regret**
  (`oracle_best_EIRV − chosen_EIRV`, scored under hidden truth, using
  the fixed ADR-003 formula). Reproducible: fixed seeds (42, 7, 123),
  1500 cases, 250 customers/merchant, 70/15/15 case-level split. Artifact:
  `simulation/evaluation/artifacts/phase4_comparison_multiseed_1500.json`.

**Result (mean ± stdev over seeds 42/7/123):**

| Model | Brier | ROC-AUC | ECE | Incremental MAE | Action Agreement | Mean EIRV Regret |
|---|---:|---:|---:|---:|---:|---:|
| **t_learner** | **0.174±0.002** | **0.804±0.006** | **0.049±0.001** | **0.193±0.033** | **0.734±0.029** | **54.4±39.9** |
| s_learner | 0.184±0.006 | 0.779±0.008 | 0.059±0.015 | 0.239±0.035 | 0.564±0.033 | 94.7±38.4 |
| lgbm_s_learner | 0.183±0.004 | 0.782±0.006 | 0.068±0.013 | 0.240±0.055 | 0.598±0.020 | 113.0±70.0 |
| tree_s_learner | 0.189±0.004 | 0.770±0.003 | 0.063±0.017 | 0.352±0.006 | 0.096±0.023 | 404.3±154.7 |

**Selected: `t_learner`** — best on every metric and the only candidate
with a realistic action mix (RETRY ~33% / MESSAGE ~55% / NO_ACTION ~11%).
`s_learner` and `tree_s_learner` are **degenerate** (collapse to one
action — MESSAGE and NO_ACTION respectively); `lgbm_s_learner` is
near-degenerate (>90% MESSAGE on 2/3 seeds). Selection is
decision-quality-primary (EIRV regret, then action agreement), not
ROC-AUC. The T-learner is trained + promoted via the unchanged
`ModelVersion` lifecycle and feeds the unchanged Decision Engine.

Contract deviations: **none.** Deferred: automated promotion gating on the
full metrics bundle (Phase 7); a production feature builder (Phase 6/8);
Qini/AUUC curves and a real EconML uplift-tree (out of hackathon scope —
the tree candidate is a shallow sklearn tree).

### Phase 5 — End-to-End MVP (✅ COMPLETE)

Turns the working pieces (Phase 1B persistence, Phase 2 simulator, Phase
3/4 decision engine + ML) into a demonstrable end-to-end product. **No new
persistence entity, no data-model change, no Phase 1A/1B contract change.**

- **`backend/services/recovery_flow.py`** — one orchestrating service:
  `ingest_failed_payment` (append-only `PaymentEvent`s) → `check_eligibility`
  (amount floor + status gate) → open/reuse `RecoveryCase` → `ANALYZING`
  → `build_case_feature_snapshot` (a `sim-feature-schema-v1` snapshot
  assembled from the Customer/Payment/Merchant rows + the PaymentEvent
  stream; leakage-guarded; the ~10-line guard is *copied*, so `backend/`
  still imports nothing from `simulation/`) → `build_policy_context` (prior
  RETRY/contact counts from the case's own history) → `DecisionEngine.run_cycle`
  → advance the case state machine → `execute_decision` (mock execution —
  no real provider; `force_status` for demos) → `record_outcome` (attached
  to the right cycle; RECOVERED also appends `PAYMENT_SUCCEEDED` + marks
  the payment) → `TrainingExample` derivation on terminal, labellable
  cases. `reevaluate` opens cycle N+1 leaving cycle N immutable. Stopping
  rules: first-cycle NO_ACTION → `STOPPED`; window elapsed → `EXPIRED`.
- **`backend/schemas/audit.py`** — `DecisionAuditRead` / `CaseAuditRead`
  assemble one decision cycle (and a case's full history) into a single
  read model answering every audit question: actions considered, per-action
  `P(recovery)` + derived incremental probability + EIRV, the economic
  recommendation, every `PolicyEvaluation` + reason code, whether the
  recommendation was blocked, the final authorized action, the
  Intervention + `execution_status`, the Outcome, the exact `ModelVersion`
  (*derived* from the cycle's Predictions — ADR-010), the cycle number,
  and a summary of previous cycles.
- **`backend/api/routes/recovery.py`** (mounted in `backend/api/main.py`)
  — `POST /payments`, `POST /payments/{id}/evaluate`, `GET /cases/{id}`,
  `GET /decisions/{id}`, `POST /decisions/{id}/execute`,
  `POST /decisions/{id}/outcome`, `POST /cases/{id}/reevaluate`. All via
  the service + existing repositories/schemas. 409 if no model is
  PROMOTED.
- **`simulation/scenarios/demo_cases.py`** + **`scripts/demo.py`** — five
  deterministic scenarios, each printing its full decision-audit chain:
  **A** RETRY is best · **B** MESSAGE is best · **C** NO_ACTION is best
  (case STOPPED on cycle 1) · **D** RETRY recommended but policy blocks it
  (`CHANNEL_DISABLED`) → `final_action` NO_ACTION · **E** a case evaluated
  twice — cycle-1 `DecisionRecord` byte-identical after re-evaluation,
  cycle 2 is a new record.
- **`simulation/evaluation/engine_eval.py`** — a sanctioned ground-truth
  reader: trains the T-learner, then on the **held-out case-level test
  split** (Phase 4 methodology) scores RecoverAI (model → EIRV → policy)
  vs a naive retry-once baseline, both under the hidden oracle.
  Reproducible: `python -m simulation.evaluation.engine_eval --seed 42`
  → `simulation/evaluation/artifacts/phase5_engine_eval_42_1500.json`.

**Result (seed 42, 1500 cases, 250 customers/merchant, 217-case test split):**

| metric | naive retry-once | RecoverAI |
|---|---:|---:|
| recovery rate | 0.376 | **0.554** |
| mean realised EIRV | 243.3 | **517.1** |
| total realised EIRV | 52,803 | **112,217** |
| action agreement w/ oracle | 0.350 | **0.728** |
| mean EIRV regret | 383.3 | **109.5** |
| NO_ACTION frequency | 0% | 21% |
| policy blocks | 0 | 0 |

Oracle EIRV ceiling for the split is 135,969 — RecoverAI captures **83%**
of the achievable incremental value, the naive baseline **39%**. Values
are generated by the harness from the simulator (fixed seed), not
fabricated.

Contract deviations: **none.** Deferred to later phases: Redis / async
workers / a retrain scheduler (Phase 7), Razorpay webhook ingress
(Phase 8), the LLM explanation layer (Phase 9), the dashboard (Phase 11).

## Core non-negotiable principles

These recur across almost every document in this folder, so they're stated
once here and referenced elsewhere:

1. **Optimize for incremental revenue, not raw recovery.** A payment that
   would have recovered anyway is not a win attributable to us. See
   `ml/uplift-modelling.md`.
2. **The LLM never moves money.** It investigates, explains, and drafts
   messages. Every financial action passes through the deterministic policy
   engine. See `architecture/decision-flow.md` and `decision-engine/policy-engine.md`.
3. **Privacy by minimization.** The LLM and any external service receive the
   minimum fields needed for the task at hand — never raw PII/card data
   unless a specific, documented task requires it. See `architecture/privacy-architecture.md`.
4. **"Do nothing" is a valid, sometimes optimal, decision.** See `decision-engine/action-selection.md`.
5. **Learn in controlled batches, not per-transaction.** Every outcome is
   recorded immediately; model weights update on a validated retrain/promote
   cycle. See `ml/learning-loop.md`.
6. **Synthetic data is clearly labelled as synthetic**, always, everywhere —
   code, docs, and the demo narrative. See `data/synthetic-data.md`.
