# User Flows

## 1. Purpose

Describe the merchant-operator's actual click-through journeys through the
dashboard, complementing the screen specifications in `dashboard.md`.

## 2. Context

Derived from `product/users.md`'s "day in the life" scenario and the use
cases in `product/use-cases.md`.

## 3. Current decision — flows

### Flow A — Morning review

```
1. Merchant logs in → lands on Command Center (dashboard.md Screen 1)
2. Scans headline numbers (revenue at risk / recovered / incremental)
3. Scrolls recent cases list, notices an "Unrecovered" case
4. Clicks into it → Case Detail (Screen 2) → sees NO_ACTION was chosen
   because predicted EIRV for every action was negative (customer already
   contacted twice this week, low amount)
5. Satisfied the system made a defensible call → closes case detail
```

### Flow B — Policy configuration

```
1. Merchant navigates to Policy settings
2. Adjusts max_customer_contacts from 2 to 1 (wants less customer friction)
3. Saves → policy takes effect for all NEW cases going forward
   (existing OPEN cases keep evaluating against the policy version active
   when they were opened — for audit consistency, per
   data/database-schema.md's policy versioning)
```

### Flow C — Evaluator / judge walkthrough

```
1. Loads Baseline vs. RecoverAI comparison (Screen 3)
2. Sees incremental revenue headline number
3. Clicks 2-3 individual cases to verify reasoning is real, not fabricated
4. Views Model Learning view (Screen 4) to see the learning loop in action
```

## 4. Alternatives considered

Considered a wizard-style guided-setup flow for first-time merchant
onboarding (connect Razorpay, configure policy, etc.). Deferred — this is
real product value but not needed to prove the core technical claims for a
hackathon submission; noted as a natural post-hackathon addition, not
built now.

## 5. Why this option

These three flows exercise every screen in `dashboard.md` and every
explainability guarantee in `architecture/security-and-safety.md`, using
the exact personas defined in `product/users.md`.

## 6. Example

See flows A-C above — each is already a concrete example.

## 7. Implementation implications

- Flow B implies `Policy` changes must be versioned/timestamped (not
  in-place mutated) so that Flow A/C's case-detail view can correctly show
  which policy version applied to a given past case — already reflected in
  `data/database-schema.md`'s `policy` table design (each policy row has a
  `created_at`; superseding a policy means inserting a new row and
  deactivating the old one, not overwriting it).

## 8. Open questions

None beyond those already listed in `dashboard.md`.

## 9. Visual

```
Flow A: Command Center → Case list → Case Detail (explains NO_ACTION)
Flow B: Policy settings → edit → save → applies to future cases only
Flow C: Comparison view → Case Detail (spot-check) → Model Learning view
```
