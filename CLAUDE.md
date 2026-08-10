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
heavily commented, composed from the TAC repo's own `getting_started/examples`
(`outbound.py`, `handoff.py`, `voice_call_events.py`, `dashboard/`).

## Hard constraints

- **Do NOT production-harden.** No auth, persistence, retries, multi-user
  handling, or race-condition engineering. In-memory dicts are deliberate.
- Optimize for readability over engineering excellence; keep the inline
  comments that mark each TAC integration point.
- Everything Twilio goes through the TAC SDK where possible.
- LLM runtime is the OpenAI Agents SDK (TAC tools convert via
  `.to_openai_agents_sdk_tool()`).

### Three things that look like hardening but are NOT — do not remove

The demo is deployed to a **public** domain, which changes the calculus for
exactly three things. Each is a handful of lines, each is env-gated so local
dev behaves as before, and each was added after a real observed failure:

| Thing | Where | Why it must stay |
|---|---|---|
| `TrustProxyHTTPS` middleware | `app.py` | Without it, **every** Twilio signature check 403s behind a TLS-terminating proxy — webhooks *and* the ConversationRelay websocket. KNOWN-ISSUES #15. |
| `DEMO_ALLOWED_NUMBERS` guard | `web.py` `trigger_call` | `POST /api/call` places real billed calls to any number with no auth. On a stable public URL that is an open robocall endpoint. |
| `uv.lock` tracked in git | repo root | Pins the TAC **git** dependency to one commit. Unpinned, the Pi builds against upstream `main` and the #1 workaround breaks (it calls `.model_dump()` on what would become plain dicts). |

## Layout

| File | Role |
|---|---|
| `app.py` | All TAC: channels, LLM loop, the 3 tools, outbound call, `TrustProxyHTTPS` |
| `web.py` | Landing-page routes: trigger call (+ allowlist guard), SSE, softphone token, tracked `/pay/<id>` |
| `events.py` | ~40-line in-memory SSE hub |
| `static/index.html` | Landing page + Twilio Voice JS softphone (`browser-agent`) |
| `studio-flow.json` | Importable flow: incoming call → Split on `HandoffData` → Connect Call To Client |
| `knowledge/renewal-faq.md` | Owl Shoes renewal FAQ — the Enterprise Knowledge content |
| `Dockerfile`, `.dockerignore` | twl deploy (linux/arm64, one `EXPOSE 8000`) |
| `KNOWN-ISSUES.md` | 15 findings, current status per issue |
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
`+18782832270` in Messaging Service `MGe7c2929facff307d2dab6a5d36b35f52`.

**Read `KNOWN-ISSUES.md` before changing code.** #1–#7, #9–#12, #15 are fixed;
each fix was adversarially reviewed and verified against the installed SDK
source. Three items remain open:

- **#8** — the handoff gate exists in `studio-flow.json` but the Twilio flow is
  still on **revision 1** (ungated). Publishing revision 2 activates it. Awaiting
  a human decision because a wrong `HandoffData` assumption breaks handoff
  entirely, and only a live call can prove it.
- **#13** — the A2P 10DLC campaign is `IN_PROGRESS`, so US SMS from the demo
  number is **blocked** with error 30034. The SMS leg cannot work until it
  verifies. Nothing to code.
- **#1 upstream** — a bug report for `twilio-agent-connect-python` is drafted in
  KNOWN-ISSUES #1 but **not filed**. Filing is public; ask first.

## Verification bar

- Done: `uv sync`, import smoke tests, SDK symbol checks, `py_compile`, a real
  containerized deploy, and live signature verification (signed `https://`
  webhook → 200, signed `wss://` upgrade → 101, forged signature → 403).
- **Never done: a full end-to-end call with a human on the line.** "Fixed"
  in KNOWN-ISSUES means reviewed and compiling, not demoed. Do not claim
  otherwise.
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
