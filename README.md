# TAC Payment Reminder Demo

A small, readable demo of the [Twilio Agent Connect (TAC) Python SDK](https://github.com/twilio/twilio-agent-connect-python):
an AI agent **calls a customer** to remind them to update their payment
information, while a landing page watches the whole call live.

**On the call, the customer can:**

| Customer says… | What happens | TAC integration point |
|---|---|---|
| "Thanks, text me the link" | Agent texts a **tracked payment link**; the click streams to the dashboard | custom `@function_tool` → `sms_channel.initiate_outbound_conversation` |
| "When does my plan renew?" | Agent answers from the **knowledge base** | built-in `create_knowledge_tool` (Enterprise Knowledge) |
| "Get me a human" | The **landing page rings** (browser softphone) and you take over the call | built-in `create_studio_handoff_tool` → Studio flow → `<Client>browser-agent` |

> ⚠️ Demo code for a hackathon audience. In-memory state, no auth, no retries,
> single user. Read it, steal from it — don't ship it.

## How it works

```
landing page ──POST /api/call──▶ app.py ──TAC VoiceChannel──▶ 📞 customer
     ▲                            │
     │ SSE /events                │ ConversationRelay websocket (TAC handles it)
     │                            ▼
     └──────── events.py ◀── handle_message_ready() ──▶ OpenAI Agents SDK
                                                          │  ├─ send_payment_link (custom tool)
     browser softphone ◀── Studio flow ◀── handoff ───────┘  ├─ search_renewal_faq (TAC knowledge tool)
     (Voice JS SDK)                                          └─ connect_to_human_agent (TAC handoff tool)
```

- **`app.py`** — everything TAC: channels, the LLM loop, the three tools, outbound call.
- **`web.py`** — landing-page routes: trigger call, SSE stream, softphone token, tracked `/pay/<id>` link.
- **`events.py`** — 40-line SSE hub.
- **`static/index.html`** — the landing page (vanilla JS + Twilio Voice JS SDK).

The voice handoff is the neat part: the LLM calls `connect_to_human_agent`,
TAC finishes speaking the goodbye sentence, ends the ConversationRelay
session, and Twilio hands the **still-live call** to your Studio flow — which
dials the `browser-agent` client, ringing the landing page.

## Setup (one time, ~15 minutes)

Prereqs: Python 3.10+, [uv](https://docs.astral.sh/uv/), [ngrok](https://ngrok.com),
a Twilio account with a voice+SMS-capable phone number, an OpenAI API key.

### 1. TAC services (Conversation Memory + Configuration)

The TAC repo ships a setup wizard that creates everything and prints your env values:

```bash
git clone https://github.com/twilio/twilio-agent-connect-python.git
cd twilio-agent-connect-python
make setup     # opens http://localhost:8080, follow the wizard
```

Copy the generated values into this project's `.env` (next step).

### 2. Environment

```bash
cp .env.example .env   # then fill it in
```

### 3. ngrok

TAC needs a public domain for Twilio's webhooks and the ConversationRelay websocket:

```bash
ngrok http 8000
```

Put the hostname (e.g. `abc123.ngrok.app` — no `https://`) in `.env` as
`TWILIO_VOICE_PUBLIC_DOMAIN`. It's also the domain in the SMS payment link.

### 4. Studio flow (rings the browser on handoff)

1. Twilio Console → **Studio** → **Create new Flow** → name it, choose **Import from JSON**.
2. Paste the contents of [`studio-flow.json`](studio-flow.json) and publish.
3. Put the Flow SID (`FW…`) in `.env` as `TWILIO_STUDIO_HANDOFF_FLOW_SID`.

The flow is two widgets: incoming call → **Split Based On `{{trigger.call.HandoffData}}`**
→ **Connect Call To → Client `browser-agent`**.

The Split is load-bearing. Once `TWILIO_STUDIO_HANDOFF_FLOW_SID` is set, TAC points
*every* call's `<Connect action>` at this flow — not just handoffs — so a dropped
websocket would otherwise ring the browser as if the customer had asked for a
human. Only a real handoff carries `HandoffData`, so the Split gates on it and the
`noMatch` branch deliberately dead-ends (the relay session is already gone).

### 5. Knowledge base (renewal FAQ)

1. Twilio Console → **Enterprise Knowledge** → create a knowledge base
   (name: *Owl Shoes Renewal FAQ*).
2. Upload [`knowledge/norton-lifelock-faq.md`](knowledge/norton-lifelock-faq.md)
   as a **Text** source and let it index. Strip the leading HTML comment first —
   an indexed disclaimer becomes its own chunk and wins the search (KNOWN-ISSUES
   #11). `knowledge/renewal-faq.md` is the older Owl Shoes version, kept for
   reference.
3. Put the knowledge base ID (`know_knowledgebase_…`) in `.env` as
   `TWILIO_KNOWLEDGE_BASE_ID`.

## Run it

```bash
uv sync
uv run python app.py
```

Open **http://localhost:8000**, wait for "softphone ready", enter your mobile
number, click **Call me**, and answer the phone.

### Suggested demo script

1. Answer the call — the greeting discloses it's an AI and why it's calling.
2. Ask: *"When does my subscription renew?"* → watch the knowledge tool fire in the feed.
3. Say: *"Great, text me the link."* → SMS arrives; tap it → **link click appears in the feed**.
4. Call again and say: *"I want to talk to a human."* → the landing page rings; click **Accept** and say hi to yourself. 🎉

## Gotchas

- **Trial accounts** can only call/text verified numbers, and Twilio plays a
  trial notice before the call connects.
- **US SMS needs A2P 10DLC approval first.** The sending number must sit in the
  sender pool of a Messaging Service whose campaign is approved, or Twilio
  rejects the message with error **30034 ("Message from an Unregistered
  Number")** — it fails hard, not soft. You do *not* need to pass the Messaging
  Service SID: TAC always sends `From: TWILIO_PHONE_NUMBER`, and registration
  binds to the number via the sender pool. See KNOWN-ISSUES #13 for this
  account's current campaign state.
- The landing page must be **open (softphone ready)** before you ask for a
  human, or the Studio flow rings a client that isn't registered.
- If you restart ngrok you get a new domain — update `.env` and restart the app.
- Browser autoplay rules: click anywhere on the page once before accepting a
  call so the browser allows audio.
