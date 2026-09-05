# Uplift / Incremental Modelling

## 1. Purpose

Define exactly how RecoverAI estimates the **incremental** effect of an
intervention — the single most important, and most differentiating,
technical piece of the product — and answer the two questions raised
directly during product planning: *why this formula and not another*, and
*where does the recovery probability actually come from*.

## 2. Context

The core principle (`docs/README.md` #1, and instructions section 17): the
system must distinguish `P(recovery | intervention)` from
`P(recovery | no intervention)`, and must never claim credit for a recovery
that would have happened anyway. This document is where that distinction
becomes a concrete, implementable method — and where several formulas
proposed during planning are compared honestly instead of arbitrarily
picking the first one discussed.

## 3. Current decision

### The headline metric: Expected Incremental Recovery Value (EIRV)

```
EIRV(a | x) = [ P(recover | x, do(a)) − P(recover | x, do(none)) ] × amount
              − cost(a)
```

This was chosen over the simpler "Expected Recovery Value" formula
(`P(recover|a) × amount − cost(a)`) that was the initial planning heuristic,
for one concrete reason: the simpler formula gives full credit to an action
even when the customer was likely to pay anyway, which directly contradicts
the "do not claim credit for recovery that would have happened regardless"
principle. EIRV is the corrected version.

### Why this formula and not others (answering the "why only this formula" question)

Several formulations were considered during planning; here is the honest
comparison:

| Formula | What it captures | Verdict for RecoverAI |
|---|---|---|
| Expected Recovery Value: `P(a)×amount − cost` | Raw expected payoff of an action | Rejected as primary — ignores what would have happened without acting; only appropriate for the very first "should we act at all" gut check, superseded by EIRV. |
| **EIRV (incremental)**: `[P(a) − P(none)]×amount − cost` | True causal contribution of the action | **Chosen** — directly implements the incremental-only principle; simple enough to compute from the model in `ml/models.md` (which already produces both `P(a)` and `P(none)` from the same model). |
| Multi-objective score with friction/risk penalty terms | Incorporates customer experience and risk, not just revenue | Kept as a documented v2 direction (`decision-engine/value-calculation.md` open questions) — not needed for MVP because the *policy engine* already hard-constrains friction/risk (max contacts, consent, amount limits) rather than trading it off softly against revenue. Softly trading off safety against revenue was explicitly rejected (see `architecture/security-and-safety.md`). |
| Constrained optimization (maximize EIRV subject to hard policy constraints) | Same as EIRV, but makes constraints explicit rather than penalty terms | This is actually what RecoverAI does — EIRV computes the *ranking*, the policy engine enforces the *constraints* as hard filters, not soft penalties. So "multi-objective" and "constrained" alternatives are subsumed into the existing EIRV + policy-engine split rather than needing a fancier formula. |
| Contextual bandit (online learning of best action per context) | Learns and adapts online, can explore | Documented as a genuine v2 direction (see `ml/learning-loop.md` open questions) — not used for MVP because we don't have live traffic to safely explore against; the batch retrain/validate/promote loop is the safe MVP substitute. |
| Reinforcement learning (state-action-reward-next state) | Full sequential decision optimization | Explicitly rejected even as a future direction for actions that touch real payments — insufficient real interaction data, and "learning by experimenting on live financial transactions" is not something to do casually (see instructions and planning transcript). Off-policy evaluation on logged/simulated data is the safe substitute, already covered by our simulator design. |

So: EIRV is not "the only formula that could work" — it's the simplest
formula in the family that (a) actually encodes the incremental-only
principle, and (b) is testable against the simulator's hidden ground truth
without requiring infrastructure (live experiments, bandits, RL) this
project cannot safely stand up in hackathon time.

### Where the probability actually comes from (answering the second question)

This was a specific, important gap in early planning ("GPT says 82%" is
explicitly rejected). The real answer:

```
1. ml/models.md's recovery model produces P(recover | features, action)
   for action ∈ {NO_ACTION, RETRY, MESSAGE}, from the SAME trained,
   calibrated (ml/probability-calibration.md) model — not from an LLM.

2. Training data for that model comes from observed (features, action,
   outcome) triples accumulated from closed RecoveryCases (ml/labels.md).

3. For MVP, since real historical data doesn't yet exist, this training
   data comes from the synthetic simulator (data/synthetic-data.md), which
   has been explicitly designed with hidden per-customer/action ground-
   truth effects for exactly this purpose — so the trained model's outputs
   can be checked against something true, not just self-consistent.

4. This is an **S-learner-style shared outcome model with the
   intervention/action represented as a treatment feature**: one model,
   conditioned on action, rather than a more complex dedicated
   uplift-modelling library (e.g. full EconML machinery) for MVP.

   Terminology, stated precisely so it isn't misused downstream:
   - An **S-learner** ("single") uses one shared model that takes the
     treatment/action as an input feature — which is exactly what is
     described above.
   - A **T-learner** ("two") generally fits separate models for the
     treated and control groups and differences their predictions.
   - RecoverAI is **not permanently committing to S-learning**. It is the
     simplest defensible MVP starting point. Phase 4 experimentally
     compares appropriate approaches — S-learner, T-learner, and other
     suitable uplift/treatment-effect methods (e.g. uplift trees, EconML)
     — and the final choice is made on evaluation (uplift/Qini quality,
     calibration, decision quality against simulator ground truth), not on
     terminology or perceived sophistication.

   The intent is to avoid introducing unnecessary causal-ML complexity into
   the MVP while keeping an honest, documented upgrade path.
```

### Evaluation against ground truth

Because the simulator conceals true `natural_recovery_probability` and true
`{retry,message}_effect` per customer, `simulation/evaluation/` can directly
check:

```
|predicted P(recover|none) − true natural_recovery_probability|
|predicted incremental effect − true intervention effect|
```

This is the actual proof (not just a demo claim) that the uplift approach
works — see `data/synthetic-data.md` section 6.

### Training data is observational, not counterfactual (Phase 1A.4 / ADR-012)

The `TrainingExample` set the model actually learns from is **observational**:
for each decision cycle we observed the outcome of the **one** action
actually taken (`observed_action`). The other candidate actions in that
cycle keep their model `Prediction`s but carry **no** outcome label — we
never write a manufactured "what MESSAGE would have done" label. The
S-learner learns `P(recover | features, action)` from these real
`(features, observed_action, outcome)` rows; uplift is *derived* by
differencing the model's predictions across actions, not read from labels.
Only the **simulator's hidden ground truth** provides true potential
outcomes under every action, and that is used for evaluation only.

### Simulator ground truth vs. experiment CONTROL/TREATMENT (different things)

Two mechanisms can answer "how much extra recovery did we cause" — keep
them separate:

```
Simulator                         Experiment
  ↓                                 ↓
Hidden ground truth               Treatment / Control assignment
(natural recovery prob,           (ExperimentAssignment: CONTROL = baseline,
 true per-action effects)          TREATMENT = decision-policy intervention)
  ↓                                 ↓
Evaluation reads the truth        Compare observed outcomes across groups
directly (offline only)           with NO hidden knowledge
  ↓                                 ↓
"do our estimates converge        "incremental-effect estimate from
 to the true effect?"              observational data, as a real deployment would"
```

The simulator's hidden parameters are never available to a real
deployment; `ExperimentAssignment` (`data/data-model.md`) is. The MVP
leans on the simulator for its headline numbers and keeps
`ExperimentAssignment` as the minimal observational hook. See
`ml/evaluation.md`.

Phase 1A.3 / ADR-011 fixes the contract: `ExperimentAssignment` is
assigned once per `RecoveryCase` (immutable, `CONTROL` or `TREATMENT`),
"treatment" may vary the `ModelVersion` or strategy but never forces an
action or bypasses policy/eligibility, and `NO_ACTION` stays a candidate
in both arms. A `TREATMENT` arm may use a `VALIDATED` (not `PROMOTED`)
`ModelVersion` by reference. Primary evaluation is **offline** over
historical/synthetic data; the RL rejection in section 3 stands — no
unconstrained live exploration.

## 4. Alternatives considered

See the formula comparison table in section 3 — that table *is* the
alternatives analysis for this document, kept in one place rather than
duplicated.

## 5. Why this option

EIRV plus an S-learner-style shared outcome model (action as a treatment
feature) is the smallest, most testable design that (a) satisfies the
incremental-only principle, (b) can be validated against concealed ground
truth before any real money is at stake, and (c) leaves an honest,
documented upgrade path (T-learner, uplift trees, EconML, bandits) if MVP
results show it's warranted — rather than over-building causal inference
machinery before knowing it's needed.

## 6. Example

```
Customer: natural recovery 30% (hidden truth, unknown to model)
Model predicts (from observed data): P(none)=0.28, P(message)=0.61
EIRV(message) = (0.61 - 0.28) × ₹10,000 - ₹20 (illustrative simulated
                message cost, not real provider pricing) ≈ ₹3,280

Ground-truth check (evaluation only): true effect was +0.32 → model's
implied effect (0.61-0.28=0.33) is very close → uplift estimation is
working well for this segment.
```

## 7. Implementation implications

**Phase 4 status (ADR-014): IMPLEMENTED.** Incremental probability is
`incremental(action) = P(recovery|features,action) −
P(recovery|features,NO_ACTION)`, computed at inference time by
`ml/models/uplift.py` (`IncrementalModel.incremental()` /
`ml.inference.RecoveryInference.incremental()`). It is **derived, never
stored** in `Prediction.recovery_probability`, and **never** replaces
EIRV — the Decision Engine still computes EIRV from the three per-action
`Prediction`s via the fixed formula in section 3. Four learners were
built and compared: `s_learner` (Phase 3), `t_learner` (per-action
logistic heads), `tree_s_learner` (shallow sklearn decision tree — the
clean "tree candidate", not an EconML causal tree), `lgbm_s_learner`
(deterministic LightGBM). **Selected: `t_learner`** on decision quality
(EIRV regret 54 / action agreement 0.73 vs S-learner 95 / 0.56), promoted
through the unchanged `ModelVersion` lifecycle. Reproducible bake-off:
`python -m simulation.evaluation.phase4_compare` (seeds 42/7/123, 1500
cases). Full results + rationale in ADR-014 and `docs/README.md` "Phase 4".

- `backend/decision_engine/value_engine.py` implements exactly the EIRV
  formula from section 3 (Phase 3) — no alternate formula was introduced.
- `Prediction` (per-action model probability) ≠ EIRV (economic value from
  the decision engine). The model emits per-action `Prediction`s; the
  engine computes EIRV from them. For audit, the per-action probabilities
  (via `Prediction`), `amount`, per-action `cost_used`, and per-action
  `eirv_value` are all persisted on the `DecisionRecord` so a historical
  decision is explainable without re-running today's model/policy/config
  (Phase 1A.2 / ADR-010, `data/data-model.md` "EIRV persistence").
- The ground-truth comparison is implemented as
  `simulation/evaluation/uplift_report.py` +
  `simulation/evaluation/phase4_compare.py` (the sanctioned hidden-truth
  readers). `ml/evaluation/compare.py` holds the observational-only
  metrics and imports no simulator truth.

## 8. Open questions

- **Resolved in Phase 4 (ADR-014):** the S-learner does **not** separate
  actions well enough — with the fixed EIRV formula it collapses to
  always-MESSAGE (the modal oracle action). The **T-learner** (per-action
  logistic heads) is materially better on decision quality and is the
  selected model. A shallow decision-tree S-learner and a LightGBM
  S-learner were also evaluated and are worse (tree collapses to
  NO_ACTION; LightGBM is near-degenerate). A true uplift tree / EconML
  comparison and Qini/AUUC curves remain future work, out of hackathon
  scope.
- Cost model for `MESSAGE`/`RETRY` (`cost(a)` in the formula) is currently
  an **illustrative simulated constant** (configurable — see
  `decision-engine/value-calculation.md` and `data/synthetic-data.md`), not
  Razorpay pricing or a claim about actual Razorpay costs; a more realistic
  cost model (e.g. actual provider pricing once Phase 10 wires up real
  channels) is a documented future refinement.
- Whether a contextual-bandit-based exploration layer is worth adding post-
  MVP once enough logged data exists to do off-policy evaluation safely —
  noted as a genuine v2 idea, not committed to.

## 9. Visual

```
                 SAME MODEL (ml/models.md)
                          │
        ┌─────────────────┼─────────────────┐
        ▼                 ▼                 ▼
   P(recover|none)   P(recover|retry)  P(recover|message)
        │                 │                 │
        └─────────────────┼─────────────────┘
                          ▼
              EIRV(a) = [P(a)-P(none)]×amount - cost(a)
                          │
                          ▼
              ranked actions → decision-engine/action-selection.md
                          │
        (offline, evaluation only) ──► compared against
                                        simulation/ground_truth hidden truth
```
