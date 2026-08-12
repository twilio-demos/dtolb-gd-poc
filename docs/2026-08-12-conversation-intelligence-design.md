# Design: Live Conversation Intelligence (2026-08-12)

Stream **live sentiment** onto the landing page using Twilio Conversation
Intelligence v3. The call summary is deliberately deferred — see *Out of scope*.

Status: **approved, not implemented.** No code has been written.

## Premise (verified before designing)

Four facts established against the live account, because the first three
guesses about them were wrong:

| Fact | Evidence |
|---|---|
| CI v3 reads **Orchestrator conversations, not recordings** | v3 ingests Conversation Orchestrator communications; no recording, no consent change, no audio path |
| TAC **does** capture voice utterances as Communications | `conv_conversation_01kzpzmjf3fj9rhtxsrkstfz3k` holds 49 communications — customer *and* Ava turns — from the Aug 10–12 calls |
| The demo has **one eternal conversation** | That conversation has been `ACTIVE` since 2026-08-10T23:20:31Z. `conversationGroupingType: GROUP_BY_PROFILE` with `channelSettings: {}` means no `statusTimeouts`, so it never closes |
| The v3 `/Conversations` view **hides** conversations with no Intelligence attached | Querying it returned only Flight-Sandbox conversations, which briefly looked like "TAC captures nothing". The Orchestrator view at `conversations.twilio.com/v2/Conversations?channelId=<CallSid>` is authoritative |

Two consequences drive the whole design:

- **Sentiment accumulates across a conversation.** On one eternal conversation
  that means every past call averages into one permanent `mixed` reading.
- **`CONVERSATION_END` can never fire** while status is stuck `ACTIVE`. A summary
  would not be late; it would never arrive.

Both are fixed by scoping conversations per call with short `statusTimeouts`.

## Flow

```
utterance → ConversationRelay → TAC → Orchestrator stores a Communication
  → Intelligence rule (COMMUNICATION trigger, Sentiment operator)
  → POST /ci-webhook                       (Twilio-signed)
  → OperatorResultEvent (TAC model) → keep only the Sentiment operator
  → events.publish("sentiment", …)
  → SSE → sentiment card on the landing page
```

## Decisions

| Decision | Why |
|---|---|
| **Sentiment now, summary later** | Sentiment streams on the `COMMUNICATION` trigger during the call. Summary needs `CONVERSATION_END`, whose timing is timeout-driven and undemoable until `statusTimeouts` are proven |
| **Clone the conversation configuration; don't edit it** | A bad `PUT` on the live config could silently break capture for the *entire* demo, not just CI. Rollback becomes one `.env` line |
| **Our own `/ci-webhook`, not TAC's `cintel_webhook_path`** | TAC's built-in route hands the payload to `OperatorResultProcessor`, which only writes summaries into Conversation Memory and never returns the text. Nothing to stream. We still use TAC for signature validation and payload models |
| **Dedicated sentiment card, not a feed row** | Readable across a room; a re-evaluated-every-turn value scrolling away in the feed is noise |
| **Webhook push, not REST polling** | Lower latency, no background task |

## Twilio-side resources

Both are created by `provision_ci.py` (new), which prints the IDs to put in
`.env`. JSON only — v3 rejects form encoding with HTTP 415 / error 20422.

**1. Intelligence Configuration** — `POST https://intelligence.twilio.com/v3/ControlPlane/Configurations`

One rule:

- `operators`: Sentiment, `intelligence_operator_01kcrvw16kfa88qvgrfmr7y151`
  (confirmed live in this account — the existing `Flight-Sandbox-Intelligence`
  config already references it)
- `triggers`: `[{ "on": "COMMUNICATION" }]`
- `actions`: `[{ "type": "WEBHOOK", "method": "POST", "url": "https://<TWILIO_VOICE_PUBLIC_DOMAIN>/ci-webhook" }]`
- no `context` block — sentiment needs neither Memory nor Knowledge injection

**2. Cloned conversation configuration** — `POST https://conversations.twilio.com/v2/ControlPlane/Configurations`

Copy of `conv_configuration_01kzpv26z8f81snvwnvhgjaqxf` with two additions:

| Field | Value | Note |
|---|---|---|
| `memoryStoreId` | `mem_store_01kzpv1mm0f46rx6nkwgqkqvpz` | **Same store** — profiles and voice→SMS identity must carry over |
| `conversationGroupingType` | `GROUP_BY_PROFILE` | Unchanged. This is what unifies the voice call and the SMS reply |
| `intelligenceConfigurationIds` | `[<new CI config id>]` | The link that makes operators run |
| `channelSettings.VOICE.statusTimeouts` | `{ "inactive": 2, "closed": 3 }` | Minutes. The one number to tune |
| `channelSettings.SMS.statusTimeouts` | `{ "inactive": 2, "closed": 3 }` | Keeps the SMS leg symmetric |

Omit `captureRules`. The demo's outbound calls hydrate passively via the
`conversationConfiguration` attribute in ConversationRelay TwiML, not via capture
rules. If the API rejects `channelSettings` without them, add outbound-only rules
mirroring the demo's own direction and read the created configuration back to
confirm the accepted shape.

## Code changes

| File | Change |
|---|---|
| `web.py` | `POST /ci-webhook`, signature-validated via `build_http_signature_dependency` exactly like `/handoff`. Parse with TAC's `OperatorResultEvent`, keep the Sentiment operator, publish to the hub |
| `static/index.html` | Sentiment card plus one SSE branch on `type === "sentiment"` |
| `provision_ci.py` *(new)* | Creates both resources above, prints IDs |
| `.env.example` | Document `TWILIO_INTELLIGENCE_CONFIGURATION_ID` |
| `app.py` | **No change.** The Intelligence link is entirely Twilio-side |

### SSE event contract

`events.publish("sentiment", f"Sentiment: {label}", label=label)`

`events.publish` already stamps `time`, which the card uses for "updated".
`label` is the lowercase operator label: `positive`, `negative`, `neutral`, or
`mixed`.

**The page must route this event to the card and stop — it must not also append a
feed row.** The existing handler renders every event into the feed, so without an
early return the demo gets a duplicate `Sentiment: positive` line on every turn,
which is exactly the noise the card decision rejected. The human-readable `text`
field exists only so the event is legible when curling `/events` directly.

The route path must not collide with TAC's own registered routes (`/webhook`,
`/twiml`, `/ws`, `/conversation-relay-callback`). `/ci-webhook` is clear. Do not
also set `TACServerConfig.cintel_webhook_path` to this path — TAC would register
its own handler on it and conflict.

### Card states

| `label` | Display |
|---|---|
| *(none yet)* | `—`, muted |
| `positive` | green |
| `neutral` | grey |
| `negative` | red |
| `mixed` | amber |

Caption reads **"this call's mood so far"** — the value is conversation-level and
cumulative, not per-utterance, even after per-call scoping.

## Error handling

| Case | Behaviour |
|---|---|
| Forged/absent signature | 403, from the TAC dependency |
| An operator we don't track | Ignore, return 200 |
| Payload shape drift | Catch `ValidationError`, log, return 200 |
| No result yet | Card shows `—` |
| Page reload | Card resets — the hub is in-memory by design |

Never return 5xx to Twilio for something we chose not to handle; it only earns
retries.

## Traps

1. **Never `PUT` a CI configuration.** It creates an *inactive* version with no
   activation API, and operators silently stop producing results. DELETE, then
   POST.
2. **The webhook URL is baked into the rule.** Every new ngrok domain requires
   re-provisioning. This is what `provision_ci.py` is for, and it is a real
   argument for demoing on the stable twl domain instead.
3. **`statusTimeouts` is the tuning knob.** Too long and back-to-back demo calls
   merge into one conversation, reviving the accumulation problem. Too short and
   a caller who goes quiet mid-call gets split across two conversations.
4. **The trigger fires on Ava's turns too.** Agent utterances are Communications,
   so `COMMUNICATION` roughly doubles operator executions versus intuition. Start
   at every communication for demo responsiveness; `triggers[].parameters.count`
   (1–20) is the cost throttle if needed.
5. **CI v3 is not PCI compliant.** The demo only ever *sends a link* and never
   collects card data. Keep it that way — do not let card numbers reach a
   transcript.

## Out of scope

- **Call summary.** Deferred by choice. The same `/ci-webhook` absorbs it later by
  matching the Summary operator and adding a second rule on `CONVERSATION_END` —
  no re-architecture. The documented Summary operator ID
  (`intelligence_operator_01kcv35pnkeysaf6z6cqtbpegn`) is **not yet verified in
  this account**; confirm it before relying on it.
- Persistence, historical charting, Insights/aggregation queries.
- Recording or any audio path.
- Custom operators. The Twilio-authored Sentiment operator is enough.

## Verification

Free, no telephony:

1. Signed `POST /ci-webhook` → 200; forged signature → 403. Same harness already
   used for `/handoff` and the ConversationRelay socket.
2. After switching `.env` and restarting, place *no* call yet — confirm the app
   boots and the new configuration ID resolves.
3. `GET /v2/Conversations?channelId=<CallSid>` returns a **new** conversation per
   call, and it reaches `CLOSED` after the timeout.
4. `GET /v3/OperatorResults?intelligenceConfigurationId=<id>` confirms results
   exist independently of our route.

Costs money and rings a real phone, so **ask first**: one live call to see the
card move. Per repo policy, no test call or SMS without explicit approval.

## Rollback

| Step | Action |
|---|---|
| Revert the app | Point `TWILIO_CONVERSATION_CONFIGURATION_ID` back at `conv_configuration_01kzpv26z8f81snvwnvhgjaqxf`, restart |
| Remove CI | DELETE the Intelligence Configuration |
| Cleanup | The cloned conversation configuration can be left; it is inert once unreferenced |

The original configuration is never modified, so rollback cannot break the
working demo.
