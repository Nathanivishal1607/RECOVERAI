# Messaging (WhatsApp / SMS / Email)

## 1. Purpose

Define the `MESSAGE` action's integration boundary — the **message
gateway** — and how it degrades gracefully to a simulated/logged send when
no real provider is configured, so the core decision engine (MVP) never
depends on this extension.

## 2. Context

`MESSAGE` is one of the three MVP actions (`RETRY`, `MESSAGE`,
`NO_ACTION` — see `product/mvp-scope.md`). It is an **abstract,
provider-agnostic intervention**: the decision engine only ever selects
`MESSAGE`, and a message gateway decides which concrete channel actually
delivers it.

```
Decision Engine → MESSAGE → Message Gateway → WhatsApp / SMS / Email
```

For the MVP the gateway is a **simulated message gateway** (no external
call). Real WhatsApp / SMS / Email delivery is a Phase 10 extension. This
document specifies both the MVP (simulated) and extension (real) behavior
under one interface so no decision-engine or policy code changes when a
real channel is added — only a feature flag and a client implementation.
`VOICE` is handled separately as its own action — see
`integrations/voice.md`.

## 3. Current decision

### Interface (stable across MVP and extension)

```python
class MessagingProvider(Protocol):
    def send(self, phone_or_channel_ref: str, message_text: str) -> SendResult: ...
```

### MVP implementation: `SimulatedMessagingProvider`

```
- Does not call any external API.
- Logs the would-be message (recipient reference, text, timestamp) to the
  Intervention record for full audit-trail parity with a real send.
- Returns a SendResult marked as simulated=True, always "delivered" for
  demo purposes (outcome — whether the customer then pays — is still
  governed by the synthetic simulator's ground truth, see
  data/synthetic-data.md, not by this provider).
```

### Extension (Phase 10): real WhatsApp / SMS / Email provider

```
- A feature flag (e.g. ENABLE_WHATSAPP_RECOVERY=true, see .env.example)
  swaps in a real client for that channel behind the same gateway.
- Message text is generated via the LLM layer (Phase 9), constructed only
  from privacy-filtered fields (architecture/privacy-architecture.md) —
  never the raw customer record.
- The policy engine's consent/contact-limit checks (decision-engine/policy-engine.md)
  are unchanged and still apply identically to real sends.
```

## 4. Alternatives considered

Considered making real WhatsApp/SMS integration part of the MVP to
strengthen the demo. Rejected per `product/mvp-scope.md` — the core
decision engine must be provably solid before any channel extension is
added; a simulated provider behind the same interface lets every part of
the decision/policy/audit pipeline be fully exercised and demoed without
this dependency.

## 5. Why this option

The `Protocol`-based interface means `backend/decision_engine` and
`backend/policies` never need to know whether a send was simulated or
real — they only ever see "MESSAGE action, allowed by policy, executed via
whatever provider is configured." This is the same pattern used for
`integrations/voice.md`.

## 6. Example

```
MVP (simulated):
  Intervention(type=MESSAGE, channel=SIMULATED,
               cost=SIMULATED_MESSAGE_COST (e.g. 0.5 — illustrative, not
               Razorpay pricing), simulated=True)
  logged_text: "Hi! Your ₹5,000 payment didn't go through... [LINK]"
  (no external call made)

Extension (real, Phase 10):
  Same Intervention shape, simulated=False, actual provider API called,
  message text drafted by LLM from privacy-filtered context.
```

## 7. Implementation implications

- `backend/integrations/messaging/` should contain `simulated_provider.py`
  (Phase 6, part of MVP) and `whatsapp_provider.py` / `sms_provider.py` /
  `email_provider.py` (Phase 10), all implementing the same interface
  behind the message gateway.
- The feature flag lives in config (`.env`), not scattered `if` checks
  through business logic — the decision engine and policy engine code
  should be identical regardless of which provider is active.

## 8. Open questions

- Which real provider (WhatsApp Business API directly, or a
  aggregator/BSP) to integrate in Phase 10 — deferred until that phase;
  not needed for MVP.

## 9. Visual

```
   decision engine → policy engine → ALLOWED (MESSAGE)
                                          │
                                          ▼
                              MessagingProvider interface
                             ┌──────────────┴──────────────┐
                             ▼                              ▼
                   SimulatedProvider (MVP)         RealProvider (Phase 10,
                   (logs, no external call)         behind feature flag)
```
