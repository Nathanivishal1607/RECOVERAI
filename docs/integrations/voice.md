# Voice (Sarvam / Indian-language recovery)

## 1. Purpose

Define the optional voice recovery channel, and be explicit that it is a
differentiator layered on top of a working core engine — never a
dependency the core product needs to function.

## 2. Context

Voice recovery (via Sarvam, for Indian-language/Hinglish interaction) was
identified during product planning as a genuine, defensible differentiator
given existing team experience — and Razorpay's own Track 3 brief lists
"Hinglish voice recovery" as an example direction. It was deliberately
ranked as an extension, not the core product, to avoid the risk of
voice-specific failure modes (STT errors, latency, consent handling)
undermining the core decision engine's demo.

## 3. Current decision

### Where voice fits

`VOICE` is **not part of the MVP intervention set** (`RETRY`, `MESSAGE`,
`NO_ACTION` — see `product/mvp-scope.md`). It is a **post-MVP extension**
and must never be required for the core engine to function.

When the extension is enabled, `VOICE` becomes an additional candidate
action (a distinct action, not a `MESSAGE` channel), available only if:

```
  - ENABLE_VOICE_RECOVERY=true (see .env.example), AND
  - the merchant's policy includes VOICE in allowed_interventions, AND
  - the customer has consent_voice=true on file (hard policy requirement,
    see decision-engine/policy-engine.md — no exceptions)
```

### Flow (when enabled)

```
Decision engine selects VOICE (highest EIRV, policy-allowed)
        │
        ▼
LLM drafts call script from privacy-filtered context (Phase 9)
        │
        ▼
Sarvam: text → target Indian language/Hinglish → speech
        │
        ▼
Voice interaction with customer (STT for response, intent detection)
        │
        ▼
Outcome recorded exactly like any other intervention (data/database-schema.md)
```

### Graceful degradation requirement

If voice is disabled or unavailable (provider error, no consent), the
decision engine must fall back to the next-best allowed action
(RETRY/MESSAGE/NO_ACTION) via the exact same policy-veto loop described in
`decision-engine/decision-engine.md` — no special-case code path.

## 4. Alternatives considered

Considered making voice the flagship/primary interface (a "voice recovery
bot"). Rejected — product discussion was explicit that this would reduce
RecoverAI to "a voice chatbot," losing the actual product (the decision
engine) behind an interface. Voice is valuable specifically *because* it
sits behind the same decision engine as every other action, not instead of
it.

## 5. Why this option

Treating voice as just another `candidate_action` (like RETRY/MESSAGE) means
zero special-casing is needed in the decision engine or policy engine — it
naturally competes on EIRV and is naturally subject to the same consent/
contact-limit rules, which is both simpler and safer than a bespoke voice
pipeline bolted on separately.

## 6. Example

```
Case: ₹4,999, UPI failure, customer's preferred_language = "hi",
      consent_voice = true
Predicted P(recover | voice) = 0.72 (highest among candidates)
EIRV(voice) = highest → policy check: consent ✓, contact limit ✓ → ALLOWED
→ Sarvam call: "Namaste! Aapka ₹4,999 ka payment complete nahi ho paaya..."
→ outcome recorded like any other intervention
```

## 7. Implementation implications

- `backend/integrations/voice/sarvam_provider.py` (Phase 10) implements the
  same kind of provider interface described in `integrations/messaging.md`.
- Voice must never be implemented in a way that requires the core decision
  engine (Phases 3-7) to already know about it — adding it later should be
  additive (new action type + new policy rule + new provider), not a
  refactor of existing code.

## 8. Open questions

- Whether the hackathon demo includes a live voice call or a recorded/
  simulated voice interaction — to be decided during Phase 12 (End-to-End
  Demo) planning based on how much time remains after the core engine and
  Phase 8/9 are solid.

## 9. Visual

```
   decision engine treats VOICE exactly like RETRY/MESSAGE —
   ranked by EIRV, gated by policy (consent required), with the
   same fallback-to-next-best-action guarantee if blocked/unavailable.
```
