# Handoff — TAC Payment Reminder Demo

Last updated 2026-08-10.

A teaching demo of the [Twilio Agent Connect (TAC) Python SDK](https://github.com/twilio/twilio-agent-connect-python)
for hackathon teams: an AI agent phones a customer about updating their payment
method, and the customer can ask FAQ questions, request a payment link by SMS, or
ask for a human and get transferred to a browser softphone. A landing page
streams the whole call live.

**Read [`README.md`](README.md) to set it up and run it.** This document covers
where the project stands, what works, and what will bite you.

## Status

| Path | State |
|---|---|
| Outbound call + AI conversation | ✅ Working, verified on real calls |
| Renewal FAQ (Enterprise Knowledge) | ✅ Working, 7/7 on the demo's questions |
| Human handoff → browser softphone | ✅ Working, verified end to end |
| Payment link by SMS | ✅ Working, one delivered message (2026-08-12) |
| Landing page + live SSE feed | ✅ Working |

Every leg has now run against live Twilio. The SMS leg was the last holdout: the
sending number sat in a Messaging Service whose 10DLC campaign never verified, so
Twilio rejected US-bound SMS with **30034 ("Message from an Unregistered
Number")**. Moving the number into an already-approved campaign fixed it —
KNOWN-ISSUES #13 has the specifics and the two traps.

**Check SMS delivery in the Messages log, not the live feed.** The feed publishes
`sms_sent` when TAC accepts the message, which is before Twilio decides to reject
it; all three 30034 failures looked like successes on the dashboard.

## Architecture

```
landing page ──POST /api/call──▶ app.py ──TAC VoiceChannel──▶ 📞 customer
     ▲                            │
     │ SSE /events                │ ConversationRelay websocket (TAC handles it)
     │                            ▼
     └──────── events.py ◀── handle_message_ready() ──▶ OpenAI Agents SDK
                                                          ├─ send_payment_link (custom TAC tool)
     browser softphone ◀── /handoff TwiML ◀── handoff ────┤─ search_renewal_faq (TAC knowledge tool)
     (Voice JS SDK)                                       └─ connect_to_human_agent (TAC handoff tool)
```

| File | Role |
|---|---|
| `app.py` | All TAC wiring: channels, the LLM loop, the three tools, the outbound call |
| `web.py` | Landing page routes, SSE, softphone token, `/handoff` transfer TwiML, tracked `/pay/<id>` |
| `events.py` | ~40-line in-memory SSE hub |
| `static/index.html` | Landing page + Twilio Voice JS softphone (client `browser-agent`) |
| `knowledge/renewal-faq.md` | Source content for the Enterprise Knowledge base |
| `Dockerfile` | Container build for the `twl` dev box (linux/arm64) |
| `studio-flow.json` | **Vestigial** — see "Handoff doesn't use Studio" below |

## Twilio resources this needs

Created once per account and referenced from `.env` (see `.env.example`):

- A **memory store** and a **conversation configuration** (Conversation
  Orchestrator). Create both with two API calls — README step 1 has them.
- An **Enterprise Knowledge base** loaded from `knowledge/renewal-faq.md`.
- A **Studio flow** — only because `create_studio_handoff_tool` refuses to build
  without a Flow SID. The flow itself is never invoked.
- A **voice + SMS phone number**, in the sender pool of a Messaging Service whose
  A2P 10DLC campaign is approved.

## Things that will bite you

Each of these cost real debugging time. Full detail in
[`KNOWN-ISSUES.md`](KNOWN-ISSUES.md).

**Handoff doesn't use Studio, and can't.** TAC's built-in Studio handoff points
`<Connect action>` at `webhooks.twilio.com/…/Flows/{sid}?Trigger=incomingCall`,
and that endpoint returns **HTTP 400 for `Direction=outbound-api`** calls. Since
this demo dials out, the Studio path can never work — the caller hears "an
application error has occurred". We set `default_twiml_options.action_url` (which
outranks the Flow SID) to our own `/handoff` route and dial the client directly.
If you build an *inbound* TAC demo, the Studio path works fine.

**Behind a TLS-terminating proxy, set `TRUST_PROXY_HTTPS=1`.** Twilio signs the
full request URL and TAC validates that signature using `X-Forwarded-Proto`. A
proxy that terminates TLS and forwards plain HTTP makes every Twilio callback —
and the ConversationRelay websocket — fail with 403. Leave it unset under ngrok.

**Set `DEMO_ALLOWED_NUMBERS` on any public deployment.** `POST /api/call` places
real billed calls to whatever number it's given and has no auth. On a stable
public URL that's an open robocall endpoint. Comma-separated E.164; unset means
any number.

**Keep only one landing page open.** Every page registers as the same Twilio
Client identity (`browser-agent`), so a handoff with two pages open rings an
ambiguous target.

**Voice JS SDK 2.x is npm-only.** There is no `sdk.twilio.com` path for it —
a plausible-looking one returns 403 from S3 and the page reports "Twilio is not
defined". `static/index.html` loads the npm package from a CDN, pinned.

**Tool-trigger wording matters.** The prompt originally said "if the customer
agrees… send the link", and the greeting ends with "is now an okay time?" — so
the caller's first "yes" fired the tool before any conversation happened.
Describe triggers so the model can't satisfy them accidentally.

**`uv.lock` is committed on purpose.** It pins the TAC git dependency to one
commit. Unpinned, a rebuild resolves upstream `main` and the knowledge-tool
workaround in `app.py` breaks.

## Open items

1. **Two upstream bug reports, drafted but not filed** — in `KNOWN-ISSUES.md`
   #1 (Pydantic serialization in `to_openai_agents_sdk_tool()`) and #17 (Studio
   handoff unusable on outbound calls). Both are worth sending to
   `twilio/twilio-agent-connect-python`.
2. **Brand consistency** — the demo is themed "Owl Shoes" throughout (prompt,
   greeting, FAQ, payment page). Rebranding means changing all of them together.

## Conventions

- **Don't production-harden this.** No auth, persistence, retries, or
  multi-user handling. In-memory dicts are deliberate. Three exceptions exist and
  are listed in `CLAUDE.md` — each was added after an observed failure.
- **Comments are sparse on purpose.** Explain the non-obvious *why*; leave
  history in `KNOWN-ISSUES.md`.
- **Never run the demo end to end without asking.** It places real calls and
  sends real SMS, both billed, to a real phone.
