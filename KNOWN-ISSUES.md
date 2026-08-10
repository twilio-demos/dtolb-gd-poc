# Known Issues

Issues 1–10 are from the 2026-08-10 code review (verified against the installed
TAC SDK source in `.venv`; issue 1 reproduced by execution). Issues 11–12 were
found the same day while provisioning the Twilio account.

**Status as of 2026-08-10.** Severity order within each section.
"Demo-breaker" = will visibly fail during the demo script.

| | Issues |
|---|---|
| ✅ Fixed & reviewed | #1, #2, #3, #4, #5, #6, #7, #9, #10 |
| ⚠️ Fixed locally, **needs re-publish to Twilio** | #8 |
| 📋 Open — a decision, not a bug | #11 (content), #12 (branding) |
| ⏳ Blocked on Twilio approval | #13 (A2P 10DLC → SMS leg) |

Every fix was adversarially reviewed by a second pass; #1–#10 each verified
against the installed SDK source rather than assumed. **None of it has been
exercised on a real call** — the demo needs a real `.env`, a public domain, and a
live phone, and `TWILIO_VOICE_PUBLIC_DOMAIN` is still unset (see
`zscaler_issues.md`). Treat "fixed" as "reviewed and compiles", not "demoed".

Judged against this project's bar: teaching/sample code, not production.
Production concerns (auth, persistence, multi-user, retries) are intentionally
out of scope and not listed.

## SDK bug (fix belongs upstream)

### 1. Knowledge tool crashes on every use — ✅ FIXED (workaround) 2026-08-10
`TACTool.to_openai_agents_sdk_tool()` JSON-encodes tool results with a bare
`json.dumps(result)` (`tac/tools/base.py` `on_invoke`), but the SDK's own
`create_knowledge_tool` returns `list[KnowledgeChunkResult]` Pydantic models —
not JSON-serializable. Caller asks an FAQ question → search succeeds →
`TypeError` → TAC logs it and the caller hears dead air.
**Demo workaround:** wrap the knowledge tool so it returns a string /
`[c.model_dump() for c in chunks]`. **Real fix:** upstream in
twilio-agent-connect-python (Pydantic-aware serialization in `on_invoke`).
File an issue on the repo — every consumer combining the built-in knowledge
tool with the OpenAI Agents converter hits this.

**Applied workaround** (`app.py`, `get_knowledge_tool`): the SDK tool is bound to
a local `search`, and a thin `search_renewal_faq(query)` wrapper returns
`[chunk.model_dump() for chunk in chunks]`. The wrapper is rebuilt with
`create_tool(...)` reusing `search.params_json_schema`, so the LLM-facing
parameter stays exactly `query` and the tool is still cached and still converted
via `.to_openai_agents_sdk_tool()`. **Delete the wrapper once this is fixed
upstream.**

<details><summary>Upstream bug report draft — NOT yet filed</summary>

**Title:** `to_openai_agents_sdk_tool()` can't serialize built-in knowledge tool
results (bare `json.dumps` on Pydantic models)

**Version:** twilio-agent-connect 2.2.0, openai-agents 0.19.4, Python 3.12

**Repro:** build a tool with `create_knowledge_tool(knowledge_client=...,
knowledge_base_id=..., name="search_faq", top_k=3)`, pass
`tool.to_openai_agents_sdk_tool()` to `Agent(tools=[...])`, then ask a question
that triggers it.

**Result:** `TypeError: Object of type KnowledgeChunkResult is not JSON
serializable`

**Cause:** `TACTool.to_openai_agents_sdk_tool().on_invoke`
(`tac/tools/base.py`) ends with `return json.dumps(result)`, while
`tac.tools.knowledge.search_knowledge` is annotated
`-> list[KnowledgeChunkResult]` and returns Pydantic models.

**Impact:** because TAC constructs `FunctionTool` directly, openai-agents
attaches no failure handler, so the exception escapes `Runner.run()`. On voice,
`VoiceChannel._handle_prompt` catches and logs it and sends no response — the
caller hears **silence**, with no user-visible error.

**Suggested fix:** make `on_invoke` Pydantic-aware, e.g.
`json.dumps(result, default=lambda o: o.model_dump(by_alias=True) if
isinstance(o, BaseModel) else str(o))`, or route through
`pydantic.TypeAdapter(Any).dump_json(result).decode()`. Optionally also pass a
`failure_error_function` to `FunctionTool` so tool errors reach the model instead
of killing the turn.

</details>

## Demo wiring (fix here)

### 2. Missing Studio flow SID silently mutes the whole agent — ✅ FIXED 2026-08-10
`create_studio_handoff_tool()` is called on every utterance (`app.py`,
`handle_message_ready`) and raises `ValueError` if
`TWILIO_STUDIO_HANDOFF_FLOW_SID` / orchestrator / memory-store config is
missing. The voice channel swallows the exception, so a half-finished `.env`
produces dead air on every turn — killing even the SMS + FAQ parts that don't
need Studio. Guard on `tac.config.studio_handoff_flow_sid` so missing config
degrades to "no handoff tool".
(SDK ergonomics feedback: swallowed callback exceptions become silence.)

### 3. Shared tool singleton — teaches the wrong pattern — ✅ FIXED 2026-08-10
`send_payment_link.configure_injection(session=...)` mutates the module-level
tool in place (SDK updates `_injected_args` and returns `self`); the "tools
are (re)bound per message" comment implies per-call isolation that doesn't
exist. Interleaved SMS + voice turns can use the wrong session; copied into a
multi-caller app this texts the wrong customer. Fix: make it a factory that
builds a fresh tool per message — mirroring the SDK's own
`create_studio_handoff_tool` pattern.
(SDK ergonomics feedback: mutate-and-return-`self` invites this mistake.)

### 4. Failed "Call me" is invisible in the UI — ✅ FIXED 2026-08-10
`static/index.html` never checks `res.ok`; the server publishes SSE only after
a successful Twilio call. Trial account + unverified number (the README's own
#1 gotcha) or a blank phone field → 500 → button greys, feed stays empty.
Fix: check `res.ok`, render an error line into the feed.

### 5. SMS replies get the voice prompt with fresh history — ✅ FIXED 2026-08-10
An inbound SMS reply lands on a new `conversation_id` with empty history while
the single `SYSTEM_INSTRUCTIONS` prompt said "you are on an outbound phone
call" and "send the link when the customer thanks you" — replying "thanks!" to the SMS plausibly
sends a second link. `context.channel` is available and unused. Fix: branch
instructions/tools on channel, or document the limitation in the comment.

### 6. `studio-flow.json` missing `caller_id` — ✅ FIXED 2026-08-10
Console-built Connect Call To widgets export
`"caller_id": "{{contact.channel.address}}"`; importing without it may fail on
Publish or dial the client without caller ID. Fix: add the property.

### 7. `uvicorn app:app` serves a half-wired app — ✅ FIXED 2026-08-10
`TACFastAPIServer` registers all TAC webhook/WebSocket routes in its
**constructor**, which only runs under `if __name__ == "__main__"`. Running
`uvicorn app:app --reload` boots the landing page but Twilio's TwiML fetch
404s. Fix: construct the server at module scope; keep only `server.start()`
under the guard.

### 8. Any relay-session death rings the browser — ⚠️ FIXED LOCALLY, NEEDS RE-PUBLISH
With the flow SID set, TAC points every call's `<Connect action>` at the
Studio flow, and our flow dials `browser-agent` unconditionally. A websocket
drop / repeated callback crash on a live call rings "Customer wants a human!"
unprompted. Fix: gate the flow on handoff data (or set an explicit
`action_url` for non-handoff cleanup).

**⚠️ DRIFT: the local file is ahead of what's live on Twilio.**
`studio-flow.json` now contains the gate — a `split-based-on` widget between the
Trigger and the dial, testing `{{trigger.call.HandoffData}}` for
`conversationId`, with `noMatch` deliberately dead-ending. Verified against the
SDK: `VoiceChannel._resolve_action_url` (`tac/channels/voice/channel.py:393-397`)
returns the Studio handoff URL for **every** call once the flow SID is set, and
`HandoffData` is present only when TAC sent the WS `end` message
(`channel.py:1144-1149`) — so it is the only runtime discriminator available.

But flow `FW3ffc6d00f903d291b16cbd134cc474f5` is **still on revision 1**
(ungated). Publishing revision 2 activates the fix; the SID does not change, so
`.env` needs no edit.

Before publishing, know the failure mode: if `{{trigger.call.HandoffData}}`
arrives empty in the Studio execution, *every* call takes `noMatch` and handoff
breaks completely — trading an over-eager ring for no ring at all. This cannot be
verified without a live call. Check the trigger parameters in a Studio execution
log first. If Studio rejects the `contains` condition on import, the fallback is
`"type": "regex", "value": "conversationId"`.

### 9. Softphone token expires after ~1 hour, silently — ✅ FIXED 2026-08-10
One token fetch (ttl 3600s), no `error` / `tokenWillExpire` listeners. Header
still says "softphone ready" while the Device is unregistered → handoff rings
nothing, Studio times out, caller dropped. Fix: `device.on("tokenWillExpire")`
→ re-fetch `/token` → `device.updateToken()`, plus `device.on("error")`.

### 10. `memory_response` is dead ceremony — ✅ FIXED 2026-08-10
Both channels sit at the default `memory_mode="never"`, so `memory_response`
is always `None`, yet the callback signature carries it while a hand-rolled
global history dict does the real work. Fix: either set
`memory_mode="always"` and compose the prompt with `MemoryPromptBuilder`
(also gives SMS cross-channel context), or drop the parameter ceremony.

## Content / account wiring (found 2026-08-10 during provisioning)

### 11. Norton help-center crawl yields only nav chrome — KB is synthetic
Twilio Enterprise Knowledge **can** crawl
`https://support.norton.com/lifelock/en/us/home/current/help-center` — the
source reached `status: COMPLETED` with no error — but at `crawlDepth: 3` it
indexed **1 chunk / 579 chars**, consisting entirely of menu labels
("Help Center Search … Top FAQ … Identity assistance"). No article bodies were
captured, and no linked articles were followed. That signature is consistent
with a JS-rendered help center: the crawler receives the static shell and the
real content never appears in the HTML. Not diagnosed further on purpose —
confirming it would mean scraping the site ourselves, which defeats the point of
using the managed crawler.

Worse than useless while present: as the only chunk it matched *every* query at
score 0.8, so the knowledge tool fed identity-theft menu text to the LLM for
every renewal question.

**Current state:** that web source was deleted. The KB is now loaded from
`knowledge/norton-lifelock-faq.md` — **10 synthetic FAQs**, Text source, 8
chunks, verified 6/6 on the demo's question set. Real Norton policy it is not;
do not quote its numbers as authoritative.

Two retrieval lessons learned the hard way, both worth keeping if the content is
ever rewritten:
- **Never put a disclaimer in the indexed body.** The first upload had a
  markdown blockquote warning at the top; it became its own chunk and won
  "When does my subscription renew?" at score 1.0 — i.e. Ava would have read
  "⚠️ Synthetic content, not real Norton policy" aloud to the caller. The
  warning now lives in an HTML comment that is stripped before upload, with an
  assertion that it never leaks.
- **Make each Q&A self-contained.** With `## heading` + body, the chunker split
  headings from answers and "what happens if my payment fails" returned the
  *update payment method* chunk. Restating the question inside each answer
  paragraph fixed all mismatches.

**To revisit:** retry with `crawlDepth: 10`, a sitemap URL, or a specific
article URL rather than the SPA hub.

### 12. Brand mismatch: Ava says "Owl Shoes", the KB says "Norton LifeLock"
`VOICE_INSTRUCTIONS` (`app.py`, renamed from `SYSTEM_INSTRUCTIONS` by the #5 fix)
makes the agent "Ava, an AI assistant for
**Owl Shoes**", and the call greeting introduces her that way, but the knowledge
base is now Norton LifeLock membership content. Ask about renewals and Ava
answers with another company's billing policy — on a live call. `web.py`'s
payment page and the README are also Owl Shoes branded, so this was left alone
rather than silently rebranded.

Fix, pick one: replace `Owl Shoes` with `Norton LifeLock` in **all four places**
the #5 fix left it — `VOICE_INSTRUCTIONS`, `SMS_INSTRUCTIONS`, the
`welcome_greeting` in `VoiceChannelConfig`, and the `search_renewal_faq` tool
description — plus the `/pay/{id}` page in `web.py` and the README for a coherent Norton
demo; or rewrite `knowledge/norton-lifelock-faq.md` as brand-neutral
"your membership" content that fits either persona.

### 13. SMS is blocked until the A2P 10DLC campaign is approved — demo-breaker (timing)
`+18782832270` is in the sender pool of Messaging Service
`MGe7c2929facff307d2dab6a5d36b35f52` ("GD-Hackathon"). Brand
`BN95cbd2c896ec7995970526412a1ae486` is **APPROVED** (Standard), but campaign
`QE2c6890da8086d771620e9b13fadeba0b` is **`IN_PROGRESS`** (use case
`LOW_VOLUME`, no errors logged).

Until that campaign reaches verified, Twilio **blocks** US-bound SMS from this
number with **error 30034 — "Message from an Unregistered Number."** It fails
hard, not soft, so the "text me the link" leg of the demo will not work. Re-check
campaign status the morning of the demo.

**No code change is needed for the Messaging Service.** TAC sends
`From: config.phone_number` and has no messaging-service parameter on the
outbound path — `InitiateMessagingConversationOptions` exposes only `to`,
`message`, `metadata`, and `tac/channels/sms.py:81` passes
`from_address=self.tac.config.phone_number`. That is fine: A2P registration binds
to the *number* through the service's sender pool, not to the API parameter you
send with (see Twilio errors 60704 / 30034). One number in the pool is also the
right shape — with several you cannot choose which one gets registered.

Two caveats worth knowing:
- The campaign description says *"I only send messages to myself for purposes of
  testing code I write."* Texting an audience member is outside that declared
  scope, and `LOW_VOLUME` carries throughput limits.
- If approval has not landed by demo time, the documented pre-registration path
  is a sender type outside A2P 10DLC — a **toll-free number** (with TF
  verification) is the realistic swap, and it is a one-line `.env` change since
  TAC only reads `TWILIO_PHONE_NUMBER`.

The service has `use_inbound_webhook_on_number: true`, so inbound replies still
route to the number's own webhook — which is what TAC wires up. No conflict with
the #5 SMS-reply handling.
