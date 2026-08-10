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

## Layout

| File | Role |
|---|---|
| `app.py` | All TAC: channels, LLM loop, the 3 tools, outbound call |
| `web.py` | Landing-page routes: trigger call, SSE, softphone token, tracked `/pay/<id>` |
| `events.py` | ~40-line in-memory SSE hub |
| `static/index.html` | Landing page + Twilio Voice JS softphone (`browser-agent`) |
| `studio-flow.json` | Importable flow: incoming call → Connect Call To → Client |
| `knowledge/renewal-faq.md` | Content for the Enterprise Knowledge base |
| `docs/2026-08-10-tac-payment-reminder-design.md` | Approved design + decisions |
| `README.md` | Full setup walkthrough (wizard, ngrok, Studio, knowledge base) |

## Known issues — READ BEFORE CHANGING CODE

**`KNOWN-ISSUES.md`** has 10 reviewed-and-verified findings, **none fixed
yet** (as of 2026-08-10). Highlights:

- #1 is an **upstream SDK bug** (knowledge tool results aren't
  JSON-serializable by `to_openai_agents_sdk_tool()`) — demo needs a wrapper
  workaround + an issue filed on twilio-agent-connect-python.
- #2 and #3 are demo-breakers in `app.py` (handoff-tool factory raising on
  every utterance when `.env` is incomplete; shared-singleton tool injection
  teaching a non-isolated pattern).
- Fixes for #2–#10 belong in this repo; keep them demo-simple.

## Reference

- A clone of the TAC SDK lives at `/tmp/tac-sdk-ref` (may be gone in a new
  session — re-clone `twilio/twilio-agent-connect-python` when verifying SDK
  behavior; the installed source is also in `.venv/lib/**/tac/`).
- Verification so far: `uv sync` + import smoke tests + SDK symbol checks.
  **Never run end-to-end** — needs a real `.env` (Twilio creds, ngrok,
  Studio flow, knowledge base) per the README.
