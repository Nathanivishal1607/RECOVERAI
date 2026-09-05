# Coding Standards

## 1. Purpose

Set baseline conventions so code contributed across phases (and potentially
across contributors) stays consistent, readable, and matches the quality
bar in the build instructions (modular, typed where practical, testable,
documented, secure by default).

## 2. Context

No code exists yet (Phase 0). This document is written ahead of
implementation so Phase 1 onward has a fixed standard to follow rather than
each phase inventing its own style.

## 3. Current decision

### Python (backend, ml, simulation)

```
- Follow PEP 8; format with `black`, lint with `ruff` (both added as dev
  dependencies once Phase 1 creates backend/ml requirements files).
- Type hints on all function signatures in backend/ and ml/inference/
  (ml/experiments/ is exempt — exploratory code).
- Pydantic models for all API request/response schemas (backend/schemas/).
- No bare `except:` — catch specific exceptions.
- No secrets, ever, in code — always via environment variables
  (see .env.example, architecture/privacy-architecture.md).
- Docstrings only where the WHY isn't obvious from the code (matching the
  project-wide "avoid comments explaining WHAT" principle) — e.g. explain
  why EIRV subtracts baseline probability, not what a subtraction does.
```

### TypeScript / Next.js (frontend)

```
- Strict TypeScript (`strict: true` in tsconfig).
- Functional components, hooks-based state.
- API calls centralized in frontend/lib/ (typed client, not scattered fetch calls).
- Tailwind + shadcn/ui for styling — no ad-hoc CSS files unless a shadcn
  component genuinely can't express the needed layout.
```

### General

```
- Modular over monolithic: a file handling more than one clear
  responsibility should be split (matches architecture/component-architecture.md's
  package boundaries).
- No premature abstraction — three similar lines beat a speculative
  helper built for hypothetical future cases (matches top-level project
  instructions).
- Every module that makes a financial or policy decision must be
  independently unit-testable without spinning up the full stack.
```

## 4. Alternatives considered

Considered deferring all style decisions until code exists ("we'll figure
it out as we go"). Rejected — Phase 0's job is exactly to prevent this kind
of drift; fixing conventions now costs nothing and avoids inconsistent
early-phase code that later phases would need to clean up.

## 5. Why this option

These are widely adopted, low-friction defaults (black/ruff, strict
TypeScript, Pydantic) that require no bespoke tooling decisions and
directly support the project's testability and auditability requirements.

## 6. Example

```python
# Good — typed, single responsibility, WHY-comment only where non-obvious
def compute_eirv(baseline_probability: float, action_probability: float,
                  amount: float, action_cost: float) -> float:
    # Subtract baseline so a customer who'd have paid anyway isn't
    # credited to this action — see docs/ml/uplift-modelling.md.
    incremental_probability = action_probability - baseline_probability
    return incremental_probability * amount - action_cost
```

## 7. Implementation implications

- CI/lint configuration (`pyproject.toml` for ruff/black,
  `.eslintrc`/`tsconfig.json` for frontend) will be added when Phase 1/6/11
  introduce the relevant code, not invented speculatively in Phase 0.

## 8. Open questions

None currently.

## 9. Visual

Not applicable — this document is prose/convention, not a diagram-driven
one.
