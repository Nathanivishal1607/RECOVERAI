# Value Calculation

## 1. Purpose

Pin down the exact arithmetic used to turn model predictions into a ₹-value
per candidate action — the EIRV formula introduced in `ml/uplift-modelling.md` —
as implementation-ready pseudocode.

## 2. Context

This is a thin, deterministic arithmetic layer (no ML happens here) sitting
between `ml/inference` and `decision-engine/action-selection.md`. Its
correctness is easy to verify by hand, which is intentional — it should be
the least mysterious part of the whole pipeline. The EIRV-per-action it
produces is stored on the `DecisionRecord` (`data/data-model.md`) as the
economic basis for the recommendation.

## 3. Current decision

```python
def compute_eirv(baseline_probability: float,
                  action_probability: float,
                  amount: float,
                  action_cost: float) -> float:
    incremental_probability = action_probability - baseline_probability
    expected_incremental_revenue = incremental_probability * amount
    return expected_incremental_revenue - action_cost

# NO_ACTION is always defined as EIRV = 0 (the reference point, not computed
# via the formula above, since action_probability == baseline_probability
# and cost == 0 by definition).
```

### Cost model (MVP placeholder)

```
cost(NO_ACTION) = 0
cost(RETRY)     = SIMULATED_RETRY_COST    (e.g. ₹2)
cost(MESSAGE)   = SIMULATED_MESSAGE_COST  (e.g. ₹0.50-₹5 depending on
                  channel)
```

> **The example numbers above are illustrative simulation assumptions
> only. They are NOT Razorpay pricing and NOT a claim about actual
> Razorpay costs.** They exist so the EIRV arithmetic has something
> concrete to work with while no real messaging/voice provider is wired up
> (that is Phase 10). The simulator uses configurable cost knobs
> (`SIMULATED_RETRY_COST`, `SIMULATED_MESSAGE_COST`) that can be tuned
> later — see `data/synthetic-data.md`.

These are configurable constants (also intended to become configurable per
merchant in the `Policy` table's future extension), not hardcoded
forever — flagged as an open question below.

### What must be persisted for audit (Phase 1A.2 / ADR-010)

The EIRV formula is fixed (ADR-003), but a historical financial decision
must stay explainable **without re-running today's model, policy, or cost
config**. For every `DecisionRecord`, persist:

| Value | How preserved |
|---|---|
| per-action `recovery_probability` (incl. the `NO_ACTION` baseline) | the related `Prediction` rows (each bound to its exact `ModelVersion`) |
| `payment_amount` used in the calc | directly on the `DecisionRecord` (`payment_amount_at_decision`) |
| per-action `cost_used` at decision time | directly (value context) — config can change, so capture it |
| per-action `eirv_value` | directly (value context); also independently re-derivable from the three above via the fixed formula, so it is both stored and checkable |
| `recommended_action` + why it won | directly (`recommended_action` + `decision_reason`) |

This lets an auditor read one `DecisionRecord` (+ its `Prediction`s) and
answer *"why did RETRY win?"* — and recompute EIRV to verify — with no
dependency on current state. Nothing beyond these few numbers is
duplicated; feature detail lives once in each `Prediction`'s feature
snapshot. See `data/data-model.md` "EIRV persistence".

## 4. Alternatives considered

See `ml/uplift-modelling.md` section 3 for the full comparison of candidate
formulas (Expected Recovery Value, multi-objective scoring, constrained
optimization, bandits, RL) — this document only concerns itself with
implementing the one formula (EIRV) that was chosen there. Re-litigating
the formula choice belongs in that document, not here.

## 5. Why this option

A pure function with no side effects, no ML inside it, and inputs/outputs
that are all plain numbers is the easiest possible thing to unit test
exhaustively — appropriate given how much downstream trust (the whole
optimizer and pitch narrative) rests on this arithmetic being exactly
right.

## 6. Example

```
baseline_probability = 0.28, action_probability = 0.67 (MESSAGE)
amount = 5000, action_cost = 2   (illustrative simulated cost, not
                                  Razorpay pricing)

incremental_probability = 0.67 - 0.28 = 0.39
expected_incremental_revenue = 0.39 * 5000 = 1950
EIRV = 1950 - 2 = 1948
```

## 7. Implementation implications

- `backend/decision_engine/value_engine.py` (Phase 5) implements exactly
  this function, with unit tests covering: negative EIRV (action expected
  to lose value), zero baseline, action_probability < baseline_probability
  (an action that would actually hurt recovery odds — must be able to
  produce a negative EIRV and correctly be ranked below NO_ACTION).

## 8. Open questions

- When real messaging/voice providers are wired up (Phase 10), replace the
  placeholder cost constants with actual provider pricing, and consider
  whether cost should vary by message length/language for voice.
- Whether merchants should be able to configure a "minimum EIRV threshold"
  below which the system always chooses NO_ACTION even if technically
  positive (avoids acting on trivially small expected gains) — likely yes,
  as a `Policy` field, deferred to Phase 5/6 implementation.

## 9. Visual

```
baseline_probability, action_probability, amount, cost
                    │
                    ▼
      EIRV = (action_p - baseline_p) × amount − cost
                    │
                    ▼
        one number per candidate action ──► action-selection.md
```
