# Design: TAC Payment Reminder Demo (2026-08-10)

> **Historical record of the approved design.** One decision did not survive
> contact with the platform: the handoff no longer runs through a Studio flow,
> because Studio's voice webhook rejects outbound-api calls. `/handoff` serves the
> transfer TwiML instead. See KNOWN-ISSUES #17, and HANDOFF.md for current state.

Teaching demo for hackathon teams: how the Twilio Agent Connect (TAC) Python
SDK connects an LLM to Twilio voice/SMS, and how tools are wired through TAC.
Explicitly sample code — in-memory state, no auth, no edge-case engineering.

## Flow

1. Landing page button triggers an outbound call via TAC `VoiceChannel`.
2. Welcome greeting (TwiML, pre-LLM) discloses it's an AI calling about
   payment info.
3. LLM loop (`on_message_ready`) runs an OpenAI Agents SDK agent with three
   tools:
   - `send_payment_link` — custom `@function_tool`; sends SMS via TAC
     `SMSChannel` with a self-hosted tracked link (`/pay/<id>`).
   - `search_renewal_faq` — TAC built-in `create_knowledge_tool` against
     Enterprise Knowledge.
   - `connect_to_human_agent` — TAC built-in `create_studio_handoff_tool`;
     voice path ends the ConversationRelay session and hands the live call to
     a one-widget Studio flow that dials Twilio Client `browser-agent`,
     ringing the landing page (Voice JS SDK).
4. Everything (call status, transcript, tool calls, SMS send, link clicks,
   handoff) streams to the landing page over SSE.

## Decisions

- Base on TAC repo examples (`outbound.py`, `handoff.py`, `voice_call_events.py`,
  `dashboard/`) per user request.
- Built-in TAC tools for handoff + knowledge (user choice); custom tool for
  the SMS link to show the custom-tool pattern.
- Self-hosted tracked link instead of Twilio Link Shortening (no messaging
  service/branded-domain setup).
- Full TAC config (Conversation Memory + Orchestrator via setup wizard), not
  relay-only — required by the built-in handoff tool.

## Files

`app.py` (TAC + agent + tools), `web.py` (UI routes/token/tracked link),
`events.py` (SSE hub), `static/index.html` (landing page + softphone),
`studio-flow.json`, `knowledge/renewal-faq.md`, `README.md`.
