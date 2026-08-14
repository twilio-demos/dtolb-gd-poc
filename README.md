# TAC Payment Reminder Demo

A small, readable demo of the [Twilio Agent Connect (TAC) Python SDK](https://github.com/twilio/twilio-agent-connect-python):
an AI agent **calls a customer** to remind them to update their payment
information, while a landing page watches the whole call live.

**On the call, the customer can:**

| Customer says… | What happens | TAC integration point |
|---|---|---|
| "Thanks, text me the link" | Agent texts a **tracked payment link**; the click streams to the dashboard | custom `@function_tool` → `sms_channel.initiate_outbound_conversation` |
| "When does my plan renew?" | Agent answers from the **knowledge base** | built-in `create_knowledge_tool` (Enterprise Knowledge) |
| "Get me a human" | The **landing page rings** (browser softphone) and you take over the call | built-in `create_studio_handoff_tool` → `action_url` → `/handoff` → `<Client>browser-agent` |

> ⚠️ Demo code for a hackathon audience. In-memory state, no auth, no retries,
> single user. Read it, steal from it — don't ship it.

## How it works

```
landing page ──POST /api/call──▶ app.py ──TAC VoiceChannel──▶ 📞 customer
     ▲                            │
     │ SSE /events                │ ConversationRelay websocket (TAC handles it)
     │                            ▼
     └──────── events.py ◀── handle_message_ready() ──▶ OpenAI Agents SDK ──▶ Gemini (Vertex, via LiteLLM)
                                                          │  ├─ send_payment_link (custom tool)
     browser softphone ◀── /handoff TwiML ◀─ handoff ─────┘  ├─ search_renewal_faq (TAC knowledge tool)
     (Voice JS SDK)                                          └─ connect_to_human_agent (TAC handoff tool)
```

- **`app.py`** — everything TAC: channels, prompts, the three tools, the LLM loop, outbound call.
- **`web.py`** — landing-page routes: trigger call, SSE stream, softphone token, tracked `/pay/<id>` link.
- **`events.py`** — 40-line SSE hub.
- **`static/index.html`** — the landing page (vanilla JS + Twilio Voice JS SDK).
- **`Dockerfile`** — for deploying to the `twl` dev box (linux/arm64).
- **[`HANDOFF.md`](HANDOFF.md)** — current status, what works, and the traps.
  **Start here if you're picking this up.**
- **[`KNOWN-ISSUES.md`](KNOWN-ISSUES.md)** — 19 findings and their current status.

The voice handoff is the neat part: the LLM calls `connect_to_human_agent`,
TAC finishes speaking the goodbye sentence, ends the ConversationRelay session,
and Twilio hands the **still-live call** to the `action_url` — our `/handoff`
route — which dials the `browser-agent` client, ringing the landing page.

> **Why not Studio?** TAC's built-in Studio handoff points `<Connect action>` at
> `webhooks.twilio.com/…/Flows/{sid}?Trigger=incomingCall`, and that endpoint
> answers **HTTP 400 for `Direction=outbound-api`** calls (verified by replaying
> it with only `Direction` changed). This demo dials out, so the Studio path
> cannot work here at any flow revision — the caller just hears "an application
> error has occurred". `/handoff` serves the transfer TwiML instead and gates on
> `HandoffData` so a dead relay session doesn't ring the browser. See
> KNOWN-ISSUES #17.

## Setup (one time, ~15 minutes)

Prereqs: Python 3.10+, [uv](https://docs.astral.sh/uv/), a Twilio account with a
voice+SMS-capable phone number, a Google Cloud project you can enable the Vertex AI
API on (step 2), and **one way to be publicly reachable** —
[ngrok](https://ngrok.com) in practice; the `twl` dev box is the author's own
scratch deploy rather than something you can reproduce (see
[Two ways to be publicly reachable](#two-ways-to-be-publicly-reachable)).

### 1. TAC services (Conversation Memory + Configuration)

The TAC repo ships a setup wizard (`git clone …/twilio-agent-connect-python && make setup`,
then follow `http://localhost:8080`). Or create the two resources directly — this
is scriptable and what was actually used here. Both calls are **async**: they
return `202` with an operation URL you must poll.

```bash
# A memory store. Note displayName allows NO spaces here: ^[a-zA-Z0-9-]+$
curl -X POST "https://memory.twilio.com/v1/ControlPlane/Stores" \
  -u "$TWILIO_API_KEY:$TWILIO_API_SECRET" -H "Content-Type: application/json" \
  -d '{"displayName":"my-demo-store","description":"Profiles for the demo"}'
# -> poll the returned statusUrl for status:COMPLETED to get mem_store_…

# A conversation configuration pointing at that store.
curl -X POST "https://conversations.twilio.com/v2/ControlPlane/Configurations" \
  -u "$TWILIO_API_KEY:$TWILIO_API_SECRET" -H "Content-Type: application/json" \
  -d '{"displayName":"My Demo","description":"Payment reminder demo",
       "conversationGroupingType":"GROUP_BY_PROFILE",
       "memoryStoreId":"mem_store_…",
       "memoryExtractionEnabled":false,
       "channelSettings":{}}'
# -> conv_configuration_… goes in .env as TWILIO_CONVERSATION_CONFIGURATION_ID
```

Two deliberate choices there:

- **`channelSettings: {}` — no VOICE capture rules.** TAC drives
  `<ConversationRelay>` actively; adding passive voice capture on the same call
  bills speech-to-text **twice**.
- **`memoryExtractionEnabled: false`** — both channels run the default
  `memory_mode="never"`, so extraction would be cost with no demo benefit.

### 2. Google Cloud (Vertex AI)

The LLM runs on Gemini through Vertex AI — the OpenAI Agents SDK with
`LitellmModel("vertex_ai/…")`, authenticated with Application Default
Credentials. There is no model API key in `.env`.

```bash
brew install --cask google-cloud-sdk                    # or any gcloud install
gcloud auth application-default login                   # writes the ADC file
gcloud services enable aiplatform.googleapis.com --project <your-project>
```

Then set three variables in `.env` (step 3): `VERTEXAI_PROJECT`,
`VERTEXAI_LOCATION` (e.g. `us-central1`) and `GEMINI_MODEL`, which is a LiteLLM
model string and so needs the `vertex_ai/` prefix (default
`vertex_ai/gemini-2.5-flash`).

Skip any of this and the failure is **quiet**: the greeting is plain TwiML, so the
call answers and sounds normal, then *every* turn is dead air — the voice channel
only logs the error, as `Failed to handle prompt:`. Check the log before
suspecting the model.

### 3. Environment

```bash
cp .env.example .env   # then fill it in
```

### 4. A public domain

TAC needs a public HTTPS domain for Twilio's webhooks and the ConversationRelay
`wss://` socket. Put the bare hostname (no scheme, no trailing slash) in `.env`
as `TWILIO_VOICE_PUBLIC_DOMAIN` — it's also the domain in the SMS payment link.
See [Two ways to be publicly reachable](#two-ways-to-be-publicly-reachable) for
ngrok vs. the twl dev box, and the extra env var the latter requires.

### 5. Studio flow (optional — NOT used for handoff)

> Handoff no longer goes through Studio (see the note under
> [How it works](#how-it-works) and KNOWN-ISSUES #17) — `/handoff` serves the
> transfer TwiML directly. You still need a Flow SID in `.env` because TAC's
> `create_studio_handoff_tool` requires one to build the handoff tool at all;
> the flow itself is never invoked.

1. Twilio Console → **Studio** → **Create new Flow** → name it, choose **Import from JSON**.
2. Paste the contents of [`studio-flow.json`](studio-flow.json) and publish.
3. Put the Flow SID (`FW…`) in `.env` as `TWILIO_STUDIO_HANDOFF_FLOW_SID`.

The flow is two widgets: incoming call → **Split Based On `{{trigger.call.HandoffData}}`**
→ **Connect Call To → Client `browser-agent`**. It is kept only so the SID
resolves to something real; nothing invokes it.

The equivalent gate now lives in `/handoff`, which dials only when the request
carries `HandoffData` and returns `<Hangup/>` otherwise — Twilio hits that URL
whenever *any* relay session ends, not just on a handoff.

### 6. Knowledge base (renewal FAQ)

1. Twilio Console → **Enterprise Knowledge** → create a knowledge base
   (name: *Owl Shoes Renewal FAQ*).
2. Upload [`knowledge/renewal-faq.md`](knowledge/renewal-faq.md) as a **Text**
   source and let it index. Two things matter for retrieval quality, both learned
   the hard way (KNOWN-ISSUES #11):
   - **Drop the title and the "Load this document into…" preamble.** An indexed
     instruction chunk competes with real answers and can win the search.
   - **Rewrite each `## Question` + body as one self-contained
     `Question: … Answer: …` block.** Otherwise the chunker separates a question
     from its answer and the wrong section comes back.
3. Put the knowledge base ID (`know_knowledgebase_…`) in `.env` as
   `TWILIO_KNOWLEDGE_BASE_ID`.

## Run it

```bash
uv sync
uv run python app.py
```

### Two ways to be publicly reachable

TAC needs a public HTTPS domain for Twilio's webhooks and the ConversationRelay
`wss://` socket. Both options below serve that socket and the webhooks, and they
can run at the same time — `TWILIO_VOICE_PUBLIC_DOMAIN` is read **per call** to
build the websocket URL and the SMS payment link, so each instance uses its own
domain.

**A. Local + ngrok** — fast iteration, domain changes on every restart.

```bash
ngrok http 8000                     # put the hostname in .env, no scheme
uv run python app.py
```

**B. The twl dev box** — stable domain, survives restarts. Specific to the
author's machine, though: a scratch deploy, not a path you can reproduce.

> **The hosted container has no Vertex credential.** `twl` injects env vars only
> and ADC is a file on your laptop, so the call answers normally — the greeting is
> plain TwiML, no LLM — and then *every* turn is dead air. Use option A for the LLM
> legs. See KNOWN-ISSUES #19.

```bash
twl deploy                          # builds on the Pi (linux/arm64)
twl env set TWILIO_VOICE_PUBLIC_DOMAIN=<app>.twl.dtolb.com \
            TRUST_PROXY_HTTPS=1 \
            DEMO_ALLOWED_NUMBERS=+1... # your own mobile
twl logs                            # watch the call happen
```

Three things are non-obvious about option B, all learned by breaking them:

- **`TRUST_PROXY_HTTPS=1` is mandatory.** The proxy terminates TLS and forwards
  plain HTTP, so Twilio's signature check fails on *everything* — webhooks and the
  voice websocket. See KNOWN-ISSUES #15.
- **`DEMO_ALLOWED_NUMBERS` matters more here.** `POST /api/call` places real billed
  calls and has no auth; a stable public URL is discoverable in a way an ngrok URL
  isn't.
- **Only open one landing page at a time.** Both register the Twilio Client
  identity `browser-agent`, so a handoff to two open pages is ambiguous
  (KNOWN-ISSUES #14).

Operational note: for ~10-25s after `twl deploy` replaces the container, the proxy
has not re-registered the backend, so **every route returns 404, then 502** while
the app itself logs a clean startup. It clears on its own — but don't deploy
minutes before demoing, and poll `GET /` until it returns 200 before you trust it.

### Then

Open the landing page — **http://localhost:8000** when running locally, or
`https://<app>.twl.dtolb.com` when deployed. Wait for **"softphone ready"**,
enter your mobile number, click **Call me**, and answer the phone.

If you set `DEMO_ALLOWED_NUMBERS`, only the numbers listed there will be dialed;
anything else returns 403 and the feed shows an error line.

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
