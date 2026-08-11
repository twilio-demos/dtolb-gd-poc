# CLAUDE.md — TAC Payment Reminder Demo

## What this is

A **teaching demo** of the Twilio Agent Connect (TAC) Python SDK for a
hackathon audience: an AI agent places an outbound call reminding a customer
to update payment info. The customer can ask renewal FAQ questions (Enterprise
Knowledge tool), say thanks and get a tracked payment link by SMS (custom TAC
tool), or ask for a human — which rings a browser softphone on the landing
page via TAC's Studio handoff tool. The landing page live-streams the whole
call over SSE.

**Why:** show other teams *how TAC integrates with an LLM and how tools are
sent through the TAC SDK*. TAC is the entry point for all Twilio
communications. The code is intentionally "sample code" — readable and
composed from the TAC repo's own `getting_started/examples` (`outbound.py`,
`handoff.py`, `voice_call_events.py`, `dashboard/`).

## Hard constraints

- **Do NOT production-harden.** No auth, persistence, retries, multi-user
  handling, or race-condition engineering. In-memory dicts are deliberate.
- Optimize for readability over engineering excellence.
- **Comment style: senior, sparse, present-tense.** Comment only what the code
  cannot say itself — a non-obvious *why*, a constraint, an SDK gotcha. Do not
  narrate debugging history ("observed live…", "verified by replaying…"), do not
  restate the code, and do not scatter KNOWN-ISSUES numbers through the source;
  that archaeology belongs in KNOWN-ISSUES.md. The comments were pruned hard on
  2026-08-10 — don't grow them back.
- Everything Twilio goes through the TAC SDK where possible.
- LLM runtime is the OpenAI Agents SDK (TAC tools convert via
  `.to_openai_agents_sdk_tool()`).

### Three things that look like hardening but are NOT — do not remove

Each item below is a handful of lines, and **every one was added after a real
observed failure** — not defensively. Deleting any of them reintroduces a bug
that has already happened once:

| Thing | Where | Why it must stay |
|---|---|---|
| `TrustProxyHTTPS` middleware | `app.py` | Without it, **every** Twilio signature check 403s behind a TLS-terminating proxy — webhooks *and* the ConversationRelay websocket. KNOWN-ISSUES #15. |
| `action_url` → `/handoff` route | `app.py`, `web.py` | The built-in Studio handoff **cannot work on outbound calls** — Studio answers 400 for `Direction=outbound-api`. Removing this returns the demo to "an application error has occurred". KNOWN-ISSUES #17. |
| The narrow consent wording in `VOICE_INSTRUCTIONS` | `app.py` | Loose triggers ("agrees", "thanks you") made the agent text the payment link on the caller's first "yes". KNOWN-ISSUES #18. |
| `DEMO_ALLOWED_NUMBERS` guard | `web.py` `trigger_call` | `POST /api/call` places real billed calls to any number with no auth. On a stable public URL that is an open robocall endpoint. |
| `uv.lock` tracked in git | repo root | Pins the TAC **git** dependency to one commit. Unpinned, the Pi builds against upstream `main` and the #1 workaround breaks (it calls `.model_dump()` on what would become plain dicts). |

## Layout

| File | Role |
|---|---|
| `app.py` | All TAC: channels, LLM loop, the 3 tools, outbound call, `TrustProxyHTTPS` |
| `web.py` | Landing-page routes: trigger call (+ allowlist guard), SSE, softphone token, `/handoff` transfer TwiML, tracked `/pay/<id>` |
| `events.py` | ~40-line in-memory SSE hub |
| `static/index.html` | Landing page + Twilio Voice JS softphone (`browser-agent`) |
| `studio-flow.json` | Vestigial. Studio can't serve outbound calls (#17); kept only so the Flow SID resolves |
| `knowledge/renewal-faq.md` | Owl Shoes renewal FAQ — the Enterprise Knowledge content |
| `Dockerfile`, `.dockerignore` | twl deploy (linux/arm64, one `EXPOSE 8000`) |
| `KNOWN-ISSUES.md` | 18 findings, current status per issue |
| `zscaler_issues.md` | ngrok is blocked by corporate TLS interception; partial workaround |
| `docs/2026-08-10-tac-payment-reminder-design.md` | Approved design + decisions |
| `README.md` | Setup walkthrough (provisioning, both public-domain options) |

## Current state (2026-08-10)

**Deployed and live:** `https://gd-poc.twl.dtolb.com` (twl dev box). Landing
page, `/token`, the TAC webhook routes and the ConversationRelay websocket are
all verified working through the proxy with real Twilio signatures.

**Provisioned on Twilio** (IDs live in `.env`, which is gitignored): memory
store, conversation configuration, Enterprise Knowledge base (Owl Shoes FAQ,
verified 7/7 on the demo's questions), Studio handoff flow, and phone number
`+18782832270` in Messaging Service `MGe7c2929facff307d2dab6a5d36b35f52`. The
Studio flow exists but is no longer invoked (#17).

**Read `KNOWN-ISSUES.md` before changing code.** #1–#12 and #15–#18 are fixed.
Two items remain open, neither of them code:

- **#13** — the A2P 10DLC campaign is `IN_PROGRESS`, so US SMS from the demo
  number is **blocked** with error 30034. The SMS leg cannot work until it
  verifies. Nothing to code.
- **#1 / #17 upstream** — bug reports for `twilio-agent-connect-python` are
  drafted (serialization in #1; Studio handoff being unusable on outbound calls
  in #17) but **not filed**. Filing is public; ask first.

**Verified on a real call:** the outbound call, the LLM turns, the knowledge
tool, and the human handoff to the browser softphone all work end to end.

## Verification bar

- Done: `uv sync`, import smoke tests, SDK symbol checks, `py_compile`, a real
  containerized deploy, live signature verification (signed `https://` webhook →
  200, signed `wss://` upgrade → 101, forged signature → 403), and **a real
  end-to-end call including the human handoff**.
- The SMS leg is the one path **never exercised** — it is blocked by #13 until
  the 10DLC campaign verifies. Don't claim it works.
- Do **not** place test calls or send test SMS without asking — both cost money
  and ring a real phone.

## Gotchas that will waste your time

- **`load_dotenv(override=True)` is deliberate.** The shell exports a
  permissionless restricted `TWILIO_API_KEY` that otherwise shadows `.env` and
  makes every Twilio call 401 with a confusing authorization error.
- **`.gitignore` has `.env.*`** so timestamped credential backups can't be
  committed. Keep `!.env.example`.
- **ngrok does not work on this machine** — corporate TLS interception. See
  `zscaler_issues.md`. The twl deploy is the working public path.
- **After `twl deploy`, every route 404s then 502s for ~10-25s** while the proxy
  re-registers, even though the app logs a clean startup. Poll `GET /` for 200.
- `timeout` is not a stock macOS binary; use `curl -m N`.

## Reference

- A clone of the TAC SDK may live at `/tmp/tac-sdk-ref` (gone in a new session —
  re-clone `twilio/twilio-agent-connect-python` when verifying SDK behavior).
  The installed source is always at `.venv/lib/python3.12/site-packages/tac/`
  and is the authoritative thing to read; prefer it over guessing.
