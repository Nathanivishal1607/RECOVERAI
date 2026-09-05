# Testing

## 1. Purpose

Define what must be tested, and to what depth, per component — proportional
to how much of the product's credibility rests on that component being
correct.

## 2. Context

The decision engine and value calculation are the components whose
correctness the entire pitch depends on; they warrant the highest testing
rigor. Exploratory ML notebooks warrant essentially none. This document
sets that gradient explicitly instead of applying one blanket rule.

## 3. Current decision

| Component | Required tests | Location |
|---|---|---|
| `decision_engine/value_engine.py` (EIRV) | Exhaustive unit tests: normal case, negative EIRV, zero baseline, action worse than baseline | `tests/decision_engine/` |
| `decision_engine/optimizer.py` (ranking) | Unit tests: normal ranking, all-negative case (NO_ACTION wins), threshold filtering | `tests/decision_engine/` |
| `policies/engine.py` | Unit test per rule (retry limit, contact limit, consent, amount limit, risk flag) in isolation, plus the guaranteed-NO_ACTION-passes test | `tests/backend/` |
| `decision_engine/` stopping rules | Unit tests: each stopping condition (max retries, max contacts, no positive EIRV, opt-out, policy restriction, recovery complete, expiry) resolves to NO_ACTION and is logged | `tests/decision_engine/` |
| Data layer (Phase 1B) — ✅ IMPLEMENTED | 52 unit tests (`tests/backend/test_data_layer.py`, `test_health_and_schemas.py`) on in-memory SQLite: identity/UUID, PaymentEvent append-only + vocab, RecoveryCase state machine + one-active-per-payment, DecisionRecord cycles + immutability, per-action Predictions bound to exact ModelVersion, PolicyEvaluation, Intervention (RETRY/MESSAGE only, no `SUCCEEDED`), Outcome vs execution-status vs case-status, Experiment case-level immutable assignment, ModelVersion lifecycle (REJECTED↛PROMOTED, one PROMOTED/role), Policy version immutability, TrainingExample (row per DecisionRecord×action, label only on observed action, NO_ACTION w/o Intervention, failed-execution not clean, no future data in features, case-level split, deterministic snapshot id). Plus `tests/integration/test_full_chain.py` (Merchant→…→TrainingExample) and `tests/integration/test_postgres_chain.py` (opt-in, real PostgreSQL incl. DB-level partial-unique enforcement). | `tests/backend/`, `tests/integration/` |
| `backend/api` webhook handling | Signature verification (valid/invalid/tampered), idempotency (duplicate delivery) | `tests/integration/` |
| `ml/models`, `ml/evaluation` | Calibration metric computation correctness; train/predict shape consistency | `tests/ml/` |
| Simulator & synthetic data (Phase 2) — ✅ IMPLEMENTED | 31 tests in `tests/simulation/` on in-memory SQLite: seed determinism (identical entity/decision fingerprint across two runs), dataset size honoured, valid entities, `PaymentEvent` vocabulary + `attempt_number` monotonicity, hidden potential outcomes for all three actions, different scenarios make different actions oracle-optimal, ground truth absent from feature snapshots / DB tables / persisted `Prediction` snapshots, ground-truth sidecar lives outside the DB, only the selected action gets an observed label, delayed outcomes exist, `NOT_RECOVERED` ⇒ amount 0, multiple immutable decision cycles, `NO_ACTION` ⇒ no `Intervention`, rejected/failed execution ⇒ no clean label, data populates every Phase 1B table, `TrainingExample` derivation idempotent + contract-valid; static check that `backend/` and `ml/` never import `simulation.ground_truth` / `simulation.evaluation`. | `tests/simulation/` |
| `simulation/generator` | Generated data respects declared distributions (e.g. failure category proportions within tolerance); ground truth is reproducible given a fixed seed | `tests/simulation/` |
| Dependency rules (`policies` never imports `decision_engine`/`ml`; `ground_truth` never imported outside `evaluation`) | Static import-check test | `tests/backend/test_dependency_rules.py`, `tests/simulation/test_dependency_rules.py` |
| Frontend | Manual verification against the running dev server for MVP (per top-level instructions: UI changes should be tested in a browser); component tests optional given hackathon time constraints | N/A |

## 4. Alternatives considered

Considered requiring the same testing rigor (e.g. property-based testing,
full coverage targets) across every component uniformly. Rejected as not a
good use of hackathon time — the value of a test is proportional to the
cost of that component being wrong; a typo in a dashboard label is not in
the same risk class as an error in the EIRV formula.

## 5. Why this option

Risk-proportional testing focuses effort exactly where the "financial
decision-support system, not a demo" identity (top-level instructions,
section 29) requires it, without pretending a hackathon team can achieve
exhaustive coverage everywhere.

## 6. Example

```python
def test_no_action_always_passes_policy():
    result = check_policy(case=any_case, action="NO_ACTION",
                           policy=any_policy, contact_history=any_history)
    assert result.allowed is True
```

## 7. Implementation implications

- `pytest` is the test runner (Python side); test directories already exist
  from Phase 0 (`tests/backend`, `tests/ml`, `tests/decision_engine`,
  `tests/simulation`, `tests/integration`) — tests land alongside the code
  they cover, per phase.

## 8. Open questions

- Whether to add frontend component tests (e.g. Vitest/Testing Library) —
  deferred; current lean is manual browser verification is sufficient given
  hackathon timelines, revisit if time permits in Phase 13 polish.

## 9. Visual

```
High-stakes, high-rigor:   decision_engine/, policies/
Medium rigor:               ml/ (metrics correctness), simulation/ (generator)
Integration-level:          webhooks (security-critical: signature, idempotency)
Manual verification:        frontend (per top-level UI-testing guidance)
```
