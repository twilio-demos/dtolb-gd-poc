# Known Issues

Findings from a code review and from getting the demo running on real calls
(2026-08-10). Issues 1–10 came from reviewing the code against the installed TAC
SDK source; 11–18 came from provisioning, deploying, and calling a real phone.

| | Issues |
|---|---|
| 📤 Open — upstream reports drafted, not filed | #1, #17 |
| ℹ️ Won't fix — documented workaround | #14 |
| ✅ Fixed | #2–#13, #15, #16, #18 |

**"Fixed" means reviewed and exercised**, not formally tested — there are no
automated tests. Every demo path has now run against live Twilio: the outbound
call, the AI conversation, the FAQ lookup, the human handoff, and the SMS payment
link (#13, delivered 2026-08-12).

Judged against this project's bar: teaching/sample code, not production.
Production concerns (auth, persistence, multi-user, retries) are intentionally out
of scope.

---

## Open

### 1. Knowledge tool results aren't JSON-serializable
**Upstream SDK bug. Worked around here; report drafted below, not filed.**

`TACTool.to_openai_agents_sdk_tool()`'s `on_invoke` encodes results with a bare
`json.dumps()` (`tac/tools/base.py`), but `create_knowledge_tool` returns
`list[KnowledgeChunkResult]` Pydantic models. The search succeeds, encoding raises
`TypeError`, the voice channel only logs it — and the caller hears dead air.

**Applied workaround** (`app.py`, `get_knowledge_tool`): the SDK tool is bound to a
local `search`, and a thin wrapper returns `[chunk.model_dump() for chunk in
chunks]`, rebuilt with `create_tool(...)` reusing `search.params_json_schema` so
the LLM-facing parameter stays exactly `query`. **Delete the wrapper once this is
fixed upstream.**

<details><summary>Upstream bug report draft</summary>

**Title:** `to_openai_agents_sdk_tool()` can't serialize built-in knowledge tool
results (bare `json.dumps` on Pydantic models)

**Version:** twilio-agent-connect 2.2.0, openai-agents 0.19.4, Python 3.12

**Repro:** build a tool with `create_knowledge_tool(...)`, pass
`tool.to_openai_agents_sdk_tool()` to `Agent(tools=[...])`, ask a question that
triggers it.

**Result:** `TypeError: Object of type KnowledgeChunkResult is not JSON serializable`

**Cause:** `on_invoke` ends with `return json.dumps(result)` while
`tac.tools.knowledge.search_knowledge` is annotated `-> list[KnowledgeChunkResult]`.

**Impact:** TAC constructs `FunctionTool` directly, so openai-agents attaches no
failure handler and the exception escapes `Runner.run()`. On voice,
`VoiceChannel._handle_prompt` catches and logs it and sends no response — the
caller hears **silence**, with no user-visible error.

**Suggested fix:** make `on_invoke` Pydantic-aware, e.g. `json.dumps(result,
default=lambda o: o.model_dump(by_alias=True) if isinstance(o, BaseModel) else
str(o))`, or `pydantic.TypeAdapter(Any).dump_json(result).decode()`. Optionally
pass a `failure_error_function` to `FunctionTool` so tool errors reach the model
instead of killing the turn.

</details>

### 17. Studio rejects outbound-api calls
**Platform limitation. Worked around here; worth reporting upstream.**

Symptom: the caller heard **"an application error has occurred"** and the transfer
never happened.

Twilio's Debugger showed error **11200 — Got HTTP 400** from the Studio flow
webhook, and `GET /v2/Flows/{sid}/Executions` returned **zero executions** — so
Studio rejected the request before any widget ran. The flow was `valid: true`,
published, correctly structured, and `HandoffData` arrived well-formed. All red
herrings.

Root cause, isolated by replaying the webhook with a valid signature and changing
exactly one variable:

| `Direction` | Studio response |
|---|---|
| `outbound-api` | **400** (body: bare `<Response/>`) |
| `inbound` | **200** + valid `<Dial>` TwiML |

`tac.tools.handoff.studio_voice_handoff_url()` points `<Connect action>` at
`?Trigger=incomingCall`, so **the built-in Studio voice handoff cannot work for a
call placed via `calls.create`.** No flow revision fixes it. An *inbound* TAC demo
is unaffected.

**Fix:** `VoiceChannel._resolve_action_url` checks
`default_twiml_options.action_url` **before** `studio_handoff_flow_sid`, so
`app.py` sets `action_url` to the `/handoff` route in `web.py`, which dials
`<Client>browser-agent</Client>` itself (signature-validated with the SDK's own
`build_http_signature_dependency`). TAC's handoff tool still does the real work:
the goodbye line, ending the relay session, attaching `HandoffData`.

`callerId` is our own `TWILIO_PHONE_NUMBER`. Studio's console-generated
`{{contact.channel.address}}` resolves to the *other party* — fine inbound, wrong
outbound.

**Debugging notes for this class of failure:** Studio answers 4xx with a bare
`<Response/>` and no error message, so the Debugger alert looks empty. Zero
executions proves the rejection happened before any widget, which rules out widget
config. Replaying the webhook yourself separates the cases: unsigned → 401, signed
but outbound → 400.

---

## Won't fix

### 14. Two deployments collide on the softphone identity
Running locally and on a hosted URL at the same time works for outbound calls —
TAC builds the ConversationRelay `wss://` URL and the SMS link **per call** from
that instance's `TWILIO_VOICE_PUBLIC_DOMAIN`.

But every landing page registers as Twilio Client `browser-agent`
(`web.py: BROWSER_AGENT_IDENTITY`), so with two pages open a handoff rings an
ambiguous target. **Workaround: keep one landing page open.** Making the identity
configurable isn't enough on its own — anything dialing a literal client name has
to agree with it.

---

## Fixed — with lessons worth keeping

### 13. US SMS needed an approved A2P 10DLC campaign
**Fixed 2026-08-12 and verified by a delivered message.**

The number sat in Messaging Service `MGe7c29…` (GD-Hackathon), whose campaign was
stuck `IN_PROGRESS` with `campaign_id=None`. Twilio **rejects** US-bound SMS from
an unregistered number with error **30034** — it fails hard, not silently, so the
"text me the link" leg simply never worked.

The account already had a second service, `MG2646…` (Dtolb-Test), on the **same
brand** `BN95cbd…` with a **VERIFIED** LOW_VOLUME campaign (`campaign_id=CIZLL0D`).
The fix was to move the number into that pool. Two traps:

- A number belongs to **one** Messaging Service, so remove before adding —
  add-first returns error **21712**.
- Registration binds to the **number** via the sender pool, not to an API
  parameter, so nothing in code changes. TAC always sends
  `From: config.phone_number` and has no messaging-service parameter on the
  outbound path (`InitiateMessagingConversationOptions` exposes only `to`,
  `message`, `metadata`).

Evidence — identical code and number, only the campaign association differed:

| When | Result |
|---|---|
| Aug 10 23:20, 23:23; Aug 11 01:00 | `undelivered`, **30034** |
| Aug 12 15:12 (`SMd07cc182…`) | **`delivered`**, `error_code=None` |

GD-Hackathon is now an empty, unused service, and the Dtolb-Test pool holds two
numbers. Reverse by moving the number back.

Had approval never landed, the documented escape hatch was a sender type outside
A2P 10DLC — a **toll-free** number with TF verification, a one-line `.env` change
since TAC only reads `TWILIO_PHONE_NUMBER`.

### 11. Knowledge retrieval: two chunking traps
We briefly pointed the knowledge base at a vendor help-center URL as a **Web**
source. Twilio's crawler reported `COMPLETED` with no error but indexed **1 chunk
/ 579 chars** of pure navigation text — no article bodies, no linked pages
followed, at `crawlDepth: 3`. That's the signature of a JS-rendered help center:
the crawler gets the static shell. It was actively harmful while present — as the
only chunk it matched *every* query at score 0.8.

The KB now holds a single Text source built from `knowledge/renewal-faq.md`,
verified **7/7** on the demo's question set. Two lessons, both learned the hard
way:

- **Never index a preamble or disclaimer.** A leading "Load this document into…"
  note becomes its own chunk and *wins* the semantic search. One upload answered
  "When does my subscription renew?" with a disclaimer at score 1.0 — the agent
  would have read it aloud to the caller.
- **Make each Q&A self-contained.** With `## heading` + body, the chunker splits
  the question from its answer and "what happens if my payment fails" returns the
  *update payment method* chunk. Restating the question inside each answer fixed
  every mismatch.

For real vendor content, try `crawlDepth: 10`, a sitemap URL, or a specific
article URL rather than an SPA hub.

### 15. A TLS-terminating proxy broke every Twilio signature check
Only reproducible when deployed. The live log showed four consecutive
`POST /twilio/call-events/status -> 403 Forbidden`.

Twilio signs the full request URL, and TAC validates that signature on `/twiml`,
the relay action callback, the call-event callbacks **and the `/ws` upgrade** — so
this broke the entire voice path, not just status events.

Diagnosed by signing candidate URLs and seeing which the deployed app accepted: it
accepted `http://…` and rejected `https://…`. The proxy terminated TLS and
forwarded plain HTTP, so `X-Forwarded-Proto` arrived as `http`.

`uvicorn --proxy-headers` **cannot** fix this: `_build_url`
(`tac/server/signature_validation.py`) reads the `X-Forwarded-Proto` header
directly and prefers it over the ASGI scheme, so the header itself must change.

**Fix:** `TrustProxyHTTPS` in `app.py`, a pure-ASGI middleware gated on
`TRUST_PROXY_HTTPS=1`. Pure ASGI because `@app.middleware("http")` never sees
`websocket` scopes. Leave it unset under ngrok, which forwards the header
correctly. Verified live: signed `https://` webhook → 200, signed `wss://` → 101
Connected, forged signature → 403.

### 16. Voice JS SDK 2.x isn't on `sdk.twilio.com`
The landing page reported **"softphone error: Twilio is not defined"**, so the
handoff had nothing to ring. (The message came from #9's error handler doing its
job — without it this would have been silent.)

Root cause was the `<script src>`, not the JavaScript.
`sdk.twilio.com/js/voice/releases/<any version>/twilio.min.js` returns **403
`AccessDenied` from `AmazonS3`** — S3's answer for a missing key. Only the retired
**Client 1.x** lives on that CDN, under `/js/client/`. Twilio's Voice JS SDK docs
offer **npm only**; unlike the Video SDK they publish no CDN URL, so that path
never existed. It looks right because Video uses a similar path.

**Fix:** load `@twilio/voice-sdk` from a CDN, pinned to an exact version. No JS
changes were needed — the UMD bundle sets `globalThis.Twilio.Device`.

**Gotcha while verifying:** the browser served a cached page and kept showing the
old error after the fix deployed, even though `curl` showed the new tag.
Hard-refresh before concluding a front-end fix didn't work.

### 18. The agent sent the payment link on the first "yes"
Confirmed on a live call: the agent fired `send_payment_link` immediately instead
of having a conversation. Not a plumbing fault — the OpenAI key was valid and the
message-ready callback was registered.

The prompt said *"If the customer **agrees**, thanks you, or asks for the link, use
the send_payment_link tool"*, while the welcome greeting ends with *"Is now an okay
time?"* — so the caller's first word is "yes", which satisfies "agrees" and fires
the tool on turn one. "thanks you" was equally loose; people say thanks constantly.

**Fix:** the prompt now states that a plain "yes"/"sure"/"okay" in reply to "is now
a good time" is **not** permission to send a text, that being thanked isn't either,
that the agent must *offer* the link and wait, and that it sends at most once per
call. Also tightened: never read a URL aloud, never guess at policy, and end
politely on a bad time without sending.

**Lesson:** describe tool triggers in terms the model cannot satisfy by accident.
"Agrees" and "thanks you" occur in almost every polite exchange; "has asked for the
link, or agreed to receive a text" is narrow enough to be safe.

### 5. SMS replies inherited the voice prompt
An inbound SMS reply arrives on a new `conversation_id` with empty history, so a
single shared prompt told a texter they were "on an outbound phone call" and
invited a second link on "thanks!".

**Fix:** `handle_message_ready` branches on `context.channel` for both the prompt
(`VOICE_INSTRUCTIONS` / `SMS_INSTRUCTIONS`) and the tool set. The payment-link and
handoff tools are voice-only; the knowledge tool stays available on both.

### 8. Any relay-session death rang the browser
Twilio requests the action URL whenever **any** ConversationRelay session ends, not
only on a handoff — so an unguarded transfer path rings the browser on a dropped
websocket.

**Fixed as part of #17:** `/handoff` dials only when the request carries
`HandoffData`, and returns `<Hangup/>` otherwise.

---

## Fixed — routine

| # | Issue | Fix |
|---|---|---|
| 2 | A missing Studio flow SID made `create_studio_handoff_tool` raise on every utterance; the voice channel swallows it, so the whole agent went silent | Guard on the flow SID and degrade to "no handoff tool" (`get_handoff_tool`) |
| 3 | `configure_injection()` mutates a module-level tool in place and returns `self`, so a shared instance leaks one caller's session into another's turn | `create_send_payment_link_tool()` builds a fresh tool per message |
| 4 | A failed "Call me" was invisible: the server publishes SSE only after Twilio accepts the call | `static/index.html` checks `res.ok` and renders an error line |
| 6 | `studio-flow.json` lacked `caller_id`, which console-built widgets export | Added. Since moot — Studio is no longer in the handoff path (#17) |
| 7 | `TACFastAPIServer` registers its routes in the constructor, which only ran under `__main__`, so `uvicorn app:app` served the page with no `/twiml` or websocket | Constructed at module scope; only `start()` stays under the guard |
| 9 | The softphone token expires after ~1h with no listeners, so the header still claimed "ready" while the Device was unregistered | `tokenWillExpire` refresh plus `error` / `unregistered` handlers |
| 10 | `memory_response` was always `None` (both channels use `memory_mode="never"`) while a hand-rolled history dict did the real work | Renamed `_memory_response` with a note on enabling TAC memory |
| 12 | The agent's persona and the knowledge base named different companies, so renewal answers quoted another company's policy | Knowledge base rebuilt from `knowledge/renewal-faq.md`; both are consistent |
