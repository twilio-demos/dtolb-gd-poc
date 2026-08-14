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
- LLM runtime is Gemini on Vertex AI via `google-genai`. TAC ships no Gemini
  adapter, so `llm.py` is the bridge: `TACTool.params_json_schema` becomes a
  `FunctionDeclaration`, and calls dispatch through `await tool(**args)`.

### Five things that look like hardening but are NOT — do not remove

Each item below is a handful of lines, and **every one was added after a real
observed failure** — not defensively. Deleting any of them reintroduces a bug
that has already happened once. The `uv.lock` row is the one exception: the
failure it was added for is gone with the OpenAI path, and the pin now guards a
hypothetical.

| Thing | Where | Why it must stay |
|---|---|---|
| `TrustProxyHTTPS` middleware | `app.py` | Without it, **every** Twilio signature check 403s behind a TLS-terminating proxy — webhooks *and* the ConversationRelay websocket. KNOWN-ISSUES #15. |
| `action_url` → `/handoff` route | `app.py`, `web.py` | The built-in Studio handoff **cannot work on outbound calls** — Studio answers 400 for `Direction=outbound-api`. Removing this returns the demo to "an application error has occurred". KNOWN-ISSUES #17. |
| The narrow consent wording in `VOICE_INSTRUCTIONS` | `app.py` | Loose triggers ("agrees", "thanks you") made the agent text the payment link on the caller's first "yes". KNOWN-ISSUES #18. |
| `DEMO_ALLOWED_NUMBERS` guard | `web.py` `trigger_call` | `POST /api/call` places real billed calls to any number with no auth. On a stable public URL that is an open robocall endpoint. |
| `uv.lock` tracked in git | repo root | Pins the TAC **git** dependency to one commit. Unpinned, the Pi builds against upstream `main`, so the SDK surface `app.py` and `llm.py` are written against can move under a rebuild. |

## Layout

| File | Role |
|---|---|
| `app.py` | All TAC: channels, prompts, the 3 tools, outbound call, `TrustProxyHTTPS` |
| `llm.py` | All Gemini: lazy Vertex client, `TACTool`→`FunctionDeclaration`, the tool loop |
| `web.py` | Landing-page routes: trigger call (+ allowlist guard), SSE, softphone token, `/handoff` transfer TwiML, tracked `/pay/<id>` |
| `events.py` | ~40-line in-memory SSE hub |
| `static/index.html` | Landing page + Twilio Voice JS softphone (`browser-agent`) |
| `studio-flow.json` | Vestigial. Studio can't serve outbound calls (#17); kept only so the Flow SID resolves |
| `knowledge/renewal-faq.md` | Owl Shoes renewal FAQ — the Enterprise Knowledge content |
| `Dockerfile`, `.dockerignore` | twl deploy (linux/arm64, one `EXPOSE 8000`) |
| `HANDOFF.md` | Status, architecture, and the traps — the doc to hand a new person |
| `KNOWN-ISSUES.md` | 19 findings, current status per issue |
| `docs/COMPLEXITY-NOTES.md` | To-do list: functions whose docstring outgrew one sentence for non-vendor reasons |
| `zscaler_issues.md` | ngrok vs. corporate TLS interception; partial workaround |
| `docs/2026-08-10-tac-payment-reminder-design.md` | Approved design + decisions |
| `README.md` | Setup walkthrough (provisioning, Vertex/ADC, ngrok; the twl box flagged as author-only) |

## Current state (2026-08-14)

**Deployed and live:** `https://gd-poc.twl.dtolb.com` (twl dev box). Landing
page, `/token`, the TAC webhook routes and the ConversationRelay websocket are
all verified working through the proxy with real Twilio signatures.

**Also runs locally** against an ngrok domain in `.env`, verified the same way
(signed webhook → 200, signed `wss://` → 101, forged → 403). Both can run at
once, but keep only **one landing page** open — #14. See the ngrok gotcha below;
that path rests on a revocable IT bypass.

**Provisioned on Twilio** (IDs live in `.env`, which is gitignored): memory
store, conversation configuration, Enterprise Knowledge base (Owl Shoes FAQ,
verified 7/7 on the demo's questions), Studio handoff flow, and phone number
`+1XXXXXXXXXX` in Messaging Service `MG…(your Messaging Service)`. The
Studio flow exists but is no longer invoked (#17).

**Read `KNOWN-ISSUES.md` before changing code.** #2–#13, #15, #16 and #18 are
fixed; #17 is worked around.
One item remains, and it isn't code:

- **#1 / #17 upstream** — bug reports for `twilio-agent-connect-python` are
  drafted (serialization in #1; Studio handoff being unusable on outbound calls
  in #17) but **not filed**. Filing is public; ask first.

**#19 is a scoping note, not a task.** The twl deploy can't reach Vertex, which
is fine — it is this machine's scratch deploy, never a sharing path. Run the
Gemini path locally against ngrok.

**Verified end to end on real traffic — on the OpenAI runtime:** the outbound
call, the LLM turns, the knowledge tool, the human handoff to the browser
softphone, and — as of 2026-08-12 — the **SMS payment link, delivered** (#13).
The runtime is now Gemini on Vertex, and no Gemini turn has run against a live
call or against real Vertex at all.

## Verification bar

- Done: `uv sync`, import smoke tests, SDK symbol checks, `py_compile`, a real
  containerized deploy, live signature verification (signed `https://` webhook →
  200, signed `wss://` upgrade → 101, forged signature → 403), **a real
  end-to-end call including the human handoff**, and **a delivered SMS** (#13).
- The Gemini runtime is **unexercised against a real model** — no Vertex call has
  been made from this machine (no `gcloud`, no ADC), so verification stops at
  imports, tool schemas and declaration shapes. The voice, SMS and handoff legs
  were last exercised on the OpenAI build.
- Check delivery in the Messages log rather than trusting the live feed — the feed
  publishes `sms_sent` when TAC accepts the message, which is *before* Twilio
  decides to reject it. All three 30034 failures in #13 looked like successes on
  the dashboard.
- Do **not** place test calls or send test SMS without asking — both cost money
  and ring a real phone.

## Gotchas that will waste your time

- **`load_dotenv(override=True)` is deliberate.** The shell exports a
  permissionless restricted `TWILIO_API_KEY` that otherwise shadows `.env` and
  makes every Twilio call 401 with a confusing authorization error.
- **`llm.py` builds its Vertex client lazily.** `app.py` calls `load_dotenv()`
  after its import block, so reading `GOOGLE_CLOUD_PROJECT` at module scope
  there would see an empty env. Vertex auth is ADC — `gcloud auth
  application-default login`, no key in `.env`.
- **Missing Vertex config fails quietly and misleadingly.** It raises out of
  `_get_client()` into `run_turn`'s broad `except`, and the
  `welcome_greeting` is plain TwiML that needs no LLM — so the call answers and
  sounds completely normal, then *every* turn speaks "Sorry, I'm having trouble
  with that right now." with one line on stdout. It presents as a broken model;
  it is an unset or empty `GOOGLE_CLOUD_PROJECT`, or missing ADC. Check stdout
  for `LLM turn failed:` first.
- **This branch is local/ngrok only.** `twl` injects env vars and ADC is a file,
  so the deployed container has no Vertex credential, by choice.
- **`.gitignore` has `.env.*`** so timestamped credential backups can't be
  committed. Keep `!.env.example`.
- **ngrok works here only on a temporary IT bypass** (granted 2026-08-12).
  Corporate TLS interception otherwise breaks it, and the grant can be revoked
  without notice — if ngrok starts failing with an x509 error, suspect that
  first. See `zscaler_issues.md`. There is no fallback if it lapses — the twl dev
  box is this machine's own scratch deploy, not something a colleague reproduces.
- **After `twl deploy`, every route 404s then 502s for ~10-25s** while the proxy
  re-registers, even though the app logs a clean startup. Poll `GET /` for 200.
- `timeout` is not a stock macOS binary; use `curl -m N`.

## Reference

- A clone of the TAC SDK may live at `/tmp/tac-sdk-ref` (gone in a new session —
  re-clone `twilio/twilio-agent-connect-python` when verifying SDK behavior).
  The installed source is always at `.venv/lib/python3.12/site-packages/tac/`
  and is the authoritative thing to read; prefer it over guessing.
