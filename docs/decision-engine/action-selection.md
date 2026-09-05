# Action Selection

## 1. Purpose

Define how candidate actions (each now with an EIRV, per
`value-calculation.md`) are ranked and how "do nothing" competes fairly
against active interventions — implementing the explicit principle that
doing nothing is a valid, sometimes optimal, decision.

## 2. Context

Product discussion specifically flagged that a good financial agent must be
able to say "don't touch this customer" and that this should be a
first-class, visible decision, not a fallback/error state.

## 3. Current decision

```python
def select_action(eirv_by_action: dict[str, float],
                   min_eirv_threshold: float = 0) -> list[str]:
    # Returns actions ranked best-first, including NO_ACTION, for the
    # policy engine to walk through in order.
    ranked = sorted(eirv_by_action.items(), key=lambda kv: kv[1], reverse=True)
    return [action for action, value in ranked if value >= min_eirv_threshold] \
           or ["NO_ACTION"]
```

Key properties:

```
- NO_ACTION (EIRV = 0 by definition) naturally outranks any action whose
  EIRV is negative — no special-casing needed, it falls out of the sort.
- min_eirv_threshold (merchant-configurable, see value-calculation.md open
  questions) lets a merchant require a minimum expected gain before ANY
  intervention is attempted, even if technically positive.
- The function always returns a non-empty ranked list (NO_ACTION guarantees
  this), so the policy engine's veto loop (decision-engine.md) always has
  a final fallback to reach.
```

### This produces the Recommendation, not the Execution

`ranked[0]` is the **recommended action** — "given predictions and
economics, what should we do." It is not necessarily what gets executed:
the policy engine (`policy-engine.md`) walks the list and the first
*allowed* entry becomes the **final action**. Both `recommended_action`
and `final_action` are stored on the `DecisionRecord` (see
`decision-engine/decision-engine.md`, `data/data-model.md`) — they can
differ, and the audit trail must show it.

## 4. Alternatives considered

| Alternative | Why rejected |
|---|---|
| Always attempt the top-EIRV action, treat NO_ACTION as only a fallback when everything else is blocked | Contradicts the explicit principle that NO_ACTION can be the *best* choice on its own economic merits, not just a last resort when policy blocks everything else. |
| Pick an action probabilistically (e.g. softmax over EIRVs) rather than deterministically picking the best | Adds unneeded randomness to a financial decision with no corresponding benefit at MVP scale; exploration-style randomness is a deliberate v2 direction (contextual bandits, see `ml/uplift-modelling.md`) requiring its own safety analysis, not something to fold in by default here. |

## 5. Why this option

Treating NO_ACTION as a normal candidate with a well-defined EIRV of zero
means the ranking logic needs no special cases — "do nothing" wins exactly
when it should, by the same arithmetic as every other action, which is both
simpler to implement and easier to defend as unbiased.

## 6. Example

```
EIRV: RETRY = 1130, MESSAGE = 1930, NO_ACTION = 0
Ranked: [MESSAGE, RETRY, NO_ACTION]  → policy engine tries MESSAGE first

EIRV: RETRY = -40, MESSAGE = -15, NO_ACTION = 0
Ranked: [NO_ACTION, MESSAGE, RETRY]  → policy engine tries NO_ACTION first
        (and since NO_ACTION always passes policy, this is the final decision)
```

## 7. Implementation implications

- `backend/decision_engine/optimizer.py` (Phase 5) implements exactly this
  function; the policy engine loop (`policy-engine.md`) consumes its output
  list in order.
- The dashboard (Phase 11) should render "chosen: NO_ACTION (best expected
  outcome)" distinctly from "chosen: NO_ACTION (all other actions were
  blocked by policy)" — these are different reasons and both are useful to
  a merchant reviewing a case (see UC-5 in `product/use-cases.md`).

## 8. Open questions

- Default value for `min_eirv_threshold` — likely 0 for MVP (any positive
  expected incremental value is worth attempting), with the option for
  merchants to raise it, to be tuned once realistic EIRV magnitudes are
  visible from the simulator.

## 9. Visual

```
{RETRY: v1, MESSAGE: v2, NO_ACTION: 0}
              │
              ▼
     sort descending, filter by threshold
              │
              ▼
   [best, second-best, ..., NO_ACTION]  ──► policy-engine.md veto loop
```
