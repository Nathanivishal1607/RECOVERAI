# Environment Configuration

## 1. Purpose

Document every environment variable in `.env.example`, what it's for, and
which ones are safe defaults vs. must-be-set-before-anything-works.

## 2. Context

`.env.example` (repo root) is the template; this document is its narrative
companion, kept in sync whenever a variable is added or changed.

## 3. Current decision — variable reference

| Variable | Required? | Notes |
|---|---|---|
| `APP_ENV`, `APP_DEBUG` | Defaults fine | `development` / `true` locally |
| `APP_SECRET_KEY` | **Must change** | Used for any session/signing needs |
| `BACKEND_HOST`, `BACKEND_PORT` | Defaults fine | |
| `NEXT_PUBLIC_API_BASE_URL` | Defaults fine locally | Must match backend's actual reachable URL |
| `POSTGRES_*`, `DATABASE_URL` | **Must set password** | Keep `DATABASE_URL` consistent with the individual `POSTGRES_*` values |
| `REDIS_*` | Defaults fine locally | |
| `CELERY_*` | Defaults fine locally | Separate Redis DB indices from the main cache use |
| `OPENAI_API_KEY` | **Required** once Phase 9 (LLM layer) is implemented | Never commit a real key; see privacy-architecture.md for what's allowed to be sent using it |
| `OPENAI_MODEL` | Default fine | Pin a specific model for reproducibility |
| `NVIDIA_NIM_API_KEY` | Optional | The Phase 12A-12C decision-**explanation** provider (NVIDIA NIM). Unset = the app runs normally; `GET /api/recovery-cases/{id}/explanation` returns `available: false` instead of erroring. Never a decision maker — see decision-flow.md. Never commit a real key. |
| `NVIDIA_NIM_BASE_URL`, `NVIDIA_NIM_MODEL` | Defaults fine | OpenAI-compatible NIM chat-completions endpoint + model id (default `openai/gpt-oss-20b`, Phase 12C — see decision-flow.md for why) |
| `LLM_REQUEST_TIMEOUT_SECONDS` | Default fine | Caps how long an explanation request can block before failing soft |
| `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET` | **Required** once Phase 8 is implemented | Test-mode credentials only during development |
| `WHATSAPP_PROVIDER_API_KEY`, `SMS_PROVIDER_API_KEY`, `SARVAM_API_KEY` | Optional | Only needed if Phase 10 extensions are enabled |
| `ENABLE_VOICE_RECOVERY`, `ENABLE_WHATSAPP_RECOVERY` | Default `false` | Flip only once the corresponding Phase 10 provider is implemented and configured |
| `ENABLE_LLM_EXPLANATIONS` | Default `true` | System must still function with this `false` (LLM is never required for a decision — see decision-flow.md) |
| `MODEL_REGISTRY_PATH`, `DEFAULT_RECOVERY_MODEL_VERSION` | Defaults fine | Points at `ml/models/artifacts/` |

## 4. Alternatives considered

Considered a single flat list with no required/optional distinction.
Rejected — a new contributor's first question is always "what do I
actually need to set to get this running," and that answer changes by
phase (e.g. Razorpay credentials are irrelevant until Phase 8).

## 5. Why this option

Organizing by "required now vs. required later vs. optional" matches how
the project is actually built — phase by phase — rather than presenting a
flat wall of configuration as if everything is needed from day one.

## 6. Example

Minimal `.env` to run Phases 2-7 (no Razorpay/OpenAI needed yet):

```
APP_ENV=development
POSTGRES_PASSWORD=devpassword
DATABASE_URL=postgresql://recoverai:devpassword@localhost:5432/recoverai
REDIS_URL=redis://localhost:6379/0
```

## 7. Implementation implications

- Any new environment variable introduced in a later phase must be added
  to both `.env.example` and this document in the same change — this is a
  standing rule, not a one-time Phase 0 task.

## 8. Open questions

None currently.

## 9. Visual

```
Phase 0-7:  APP_*, POSTGRES_*, REDIS_*, CELERY_*        (no external services)
Phase 8:    + RAZORPAY_*
Phase 9:    + OPENAI_*
Phase 10:   + WHATSAPP_*/SMS_*/SARVAM_* (only if enabled)
```
