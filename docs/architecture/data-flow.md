# Data Flow

## 1. Purpose

Trace how data physically moves through the system, distinct from the
decision *logic* (covered in `decision-flow.md`) — this document is about
where data is read from and written to at each step.

## 2. Context

Useful for anyone implementing a specific component who needs to know "what
do I read, and what do I have to persist before handing off to the next
stage."

## 3. Current decision

```
1. INGESTION
   Source: Razorpay webhook (Phase 8) OR synthetic event feed (Phase 2-7)
   Normalise: inbound signal → PaymentEvent (5-value vocabulary; see data/events.md)
   Write:  PaymentEvent row (append-only), Payment row (upsert; status from event)
   NOTE:   no RecoveryCase yet.

1b. RECOVERY ELIGIBILITY   (only on a PAYMENT_FAILED event)
   Read:   Payment, Merchant (ACTIVE?), existing active RecoveryCase?, recovery window, Policy
   Decide: eligible? (a deterministic gate — NOT EIRV)
   Write:  RecoveryCase row (status=OPEN) IF eligible; otherwise log "ineligible", no case
   Write:  (if an experiment is running) ExperimentAssignment row — ONE per RecoveryCase,
           immutable, arm = CONTROL | TREATMENT (see data/data-model.md)

2. FEATURE EXTRACTION
   Read:   Payment, Customer, Merchant, prior PaymentEvents/Outcomes for this customer
   Write:  feature snapshot (persisted as part of ModelPrediction, not a separate mutable table —
           see data/database-schema.md for why snapshots are immutable)

3. PREDICTION
   Read:   feature snapshot; RecoveryCase.ExperimentAssignment.arm selects which
           ModelVersion/strategy is used (e.g. TREATMENT may use a VALIDATED
           candidate instead of the PROMOTED default) — the candidate action set
           (RETRY/MESSAGE/NO_ACTION) is unchanged in either arm
   Write:  ModelPrediction row (baseline_probability, per-action probabilities,
           model_version → ModelVersion)
   RecoveryCase.status → ANALYZING

4. INCREMENTAL VALUE + OPTIMIZATION (recommendation)
   Read:   ModelPrediction, action cost config, merchant policy config
   Write:  nothing persisted yet — pure computation; produces
           recommended_action + EIRV per candidate

5. POLICY CHECK
   Read:   Merchant Policy row (+ its version), Customer contact-history
   Write:  PolicyEvaluation row per candidate tried
           (action, policy_id, policy_version, result, reason_code, reason, evaluated_at)

6. DECISION RECORD  (one per cycle; immutable; cycle_number ordinal)
   Write:  Prediction rows — one per candidate action (RETRY/MESSAGE/NO_ACTION),
           each with recovery_probability + exact model_version_id + feature_snapshot
   Write:  DecisionRecord row — cycle_number, decision_timestamp,
           payment_amount_at_decision, value context {cost_used, eirv_value} per action,
           recommended_action, final_action (stored SEPARATELY), decision_reason,
           policy_version_ref
   RecoveryCase.status → ACTION_SELECTED  (or → STOPPED if a stopping rule fired)

7. ACTION EXECUTION
   Read:   DecisionRecord.final_action + parameters
   Write:  IF final_action ∈ {RETRY, MESSAGE}: Intervention row (action,
           channel, execution_status ∈ {REQUESTED, ACCEPTED, REJECTED, FAILED},
           requested_at, resolved_at, cost_incurred, provider_ref)
           IF final_action = NO_ACTION: no Intervention row — this step is a
           no-op (no provider call); the case still advances to confirm the
           decision is settled.
   External call: RETRY → Razorpay API (Phase 8); MESSAGE → message gateway
           (simulated in MVP, real WhatsApp/SMS/Email in Phase 10); VOICE → post-MVP
   RecoveryCase.status → ACTION_EXECUTED → WAITING_FOR_OUTCOME
           (this transition occurs for EVERY final_action, including
           NO_ACTION — an Intervention row is not a precondition for it)

8. OUTCOME  (per decision cycle; delayed outcomes OK)
   Source: a later PaymentEvent (PAYMENT_SUCCEEDED / PAYMENT_FAILED again /
           PAYMENT_CANCELLED) from webhook or simulator (Phase 2-7)
   Write:  Outcome row (decision_record_id, intervention_id (nullable),
           result ∈ {RECOVERED, NOT_RECOVERED}, recovery_amount, observed_at)
           — observed_at may lag Intervention.resolved_at
   RecoveryCase.status → RECOVERED, or → ANALYZING (re-evaluate → new DecisionRecord),
                         or → STOPPED / EXPIRED   (all "closed")

9. LEARNING
   Read:   all closed RecoveryCases + their ModelPrediction + DecisionRecord
           + Intervention + Outcome rows
   Derive: TrainingExample rows — one per (DecisionRecord × candidate action);
           outcome_label ONLY on the observed_action row (a Prediction is NOT
           an observed outcome — no counterfactual labels); NO_ACTION rows need
           no Intervention; feature_snapshot frozen as of the DecisionRecord
   Split:  train/val/test BY recovery_case_id (never per row)
   Write:  training dataset SNAPSHOT (reproducible identity), new model
           artifact + ModelVersion record (status DRAFT; see ml/learning-loop.md).
           ModelVersion.training_dataset_snapshot_id = the exact row set used.

10. EXPERIMENTATION / AUDIT
   Read:   all of the above, joined (incl. DecisionRecord, ExperimentAssignment,
           ModelVersion)
   Write:  nothing new — this is a read/aggregation layer for the dashboard,
           for baseline-vs-RecoverAI comparison (UC-3), and for CONTROL vs
           TREATMENT comparison (see data/data-model.md, ml/evaluation.md)
```

## 4. Alternatives considered

Considered making feature snapshots mutable/recomputable on demand instead
of persisted. Rejected — see `data/database-schema.md` ADR note: without a
frozen snapshot, "why did the model decide this six months ago" becomes
unanswerable if upstream customer/merchant aggregates have since changed.
Auditability requires the snapshot to be immutable.

## 5. Why this option

Mirrors the RecoveryCase status lifecycle 1:1, so any engineer can locate
"what data exists at this point" by looking at the case's current status.
Keeps a single source of truth (PostgreSQL) rather than splitting
state across a cache and a database in ways that could disagree.

## 6. Example

See `architecture/decision-flow.md` for the same trace annotated with
decision logic rather than data reads/writes.

## 7. Implementation implications

- `ModelPrediction.feature_snapshot` should be stored as JSON (Postgres
  `jsonb`) rather than requiring a schema migration every time a feature is
  added — see `data/database-schema.md`.
- Redis is used only for transient state (idempotency keys, queue,
  rate/contact-limit counters that get flushed into Postgres) — it is never
  the durable source of truth for case state.

## 8. Open questions

- Whether contact-limit counters (step 5) should live only in Postgres
  (queryable, durable) or be cached in Redis for speed with Postgres as
  fallback — likely Postgres-only for MVP given expected data volume; Redis
  caching is a Phase 6+ optimization only if needed.

## 9. Visual

```
Webhook/Sim → normalise → [PaymentEvent (append-only), Payment (upsert)]
     → recovery eligibility gate → [RecoveryCase(OPEN)]  (only if eligible)
     → [ModelPrediction (+ModelVersion)] → RecoveryCase(ANALYZING)
     → recommendation + policy result
     → [DecisionRecord: recommended vs final] → RecoveryCase(ACTION_SELECTED | STOPPED)
     → [Intervention, only if final_action ∈ {RETRY,MESSAGE}; none for NO_ACTION]
     → RecoveryCase(ACTION_EXECUTED → WAITING_FOR_OUTCOME)
     → [Outcome] → RecoveryCase(RECOVERED | re-evaluate | STOPPED | EXPIRED)  ["closed"]
     → [TrainingExample per (DecisionRecord × action); label only on observed action]
     → case-level split → training dataset SNAPSHOT → new ModelVersion (DRAFT)
```
