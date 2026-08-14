# Complexity notes

Every function in `app.py`, `llm.py`, `web.py` and `events.py` whose docstring
needs more than one sentence is listed below. **This is a to-do list, not a
record of work done** — nothing here has been acted on, and nothing here should
be acted on casually: `CLAUDE.md` forbids production-hardening this demo.

The rule being applied: a function that needs more than a sentence of docstring
is a complexity smell worth revisiting, *unless* the extra sentences are a
vendor constraint (TAC, Twilio, Vertex/Gemini, the TLS-terminating proxy) or an
env-var contract. Those are exempt — no amount of refactoring makes an external
API's behavior self-evident.

Line numbers are as of commit `dbc0dfa`'s successor (the comment-reduction
commit) on branch `gemini-vertex`.

## Vendor-constraint / contract — exempt, leave alone

| Location | Function | Why the docstring is long |
|---|---|---|
| `app.py:142` | `create_send_payment_link_tool` | TAC's `configure_injection()` mutates in place and returns `self`; `function_tool()` derives the model-facing schema from the wrapped signature and docstring. |
| `app.py:179` | `get_handoff_tool` | `create_studio_handoff_tool` raises without a flow SID / Orchestrator / memory store, and TAC's voice channel only *logs* exceptions from the message callback. |
| `app.py:256` | `on_call_status` | Registering the handler is the side effect that makes TAC attach the status callback URL. |
| `app.py:268` | `start_reminder_call` | Twilio reports only `completed` unless `status_callback_event` is listed out. |
| `app.py:291` | `TrustProxyHTTPS` (class) | Twilio signs the full URL and TAC validates using `X-Forwarded-Proto`; a TLS-terminating proxy sends `http`. Pure ASGI because `@app.middleware("http")` never sees `websocket` scopes. |
| `llm.py:52` | `_declare` | Vertex rejects an OBJECT schema with empty `properties`. |
| `llm.py:71` | `_as_response` | TAC's knowledge tool returns Pydantic models, which Gemini cannot serialize. |
| `web.py:55` | `trigger_call` | `DEMO_ALLOWED_NUMBERS` env-var contract on a route that places real, billed calls with no auth. |
| `web.py:74` | `voice_token` | `incoming_allow` is what lets `/handoff`'s `<Dial><Client>` ring the page. |
| `web.py:93` | `handoff` | Twilio requests `action_url` whenever a ConversationRelay session ends, not only on a handoff, and `callerId` must be a number the account owns. |
| `events.py:18` | `publish` | The `event_type` tag list is a contract with `static/index.html`, which styles each feed line by it. A `StrEnum` would not reach the JS side, so this is documentation, not a smell. |

## Complexity — worth revisiting

| Location | Function | What smells | Suggestion (one line) |
|---|---|---|---|
| `llm.py:86` | `run_turn` | Four of its five docstring facts are vendor constraints, but the last paragraph is ours: both failure exits return the **pre-turn** history, silently discarding a tool round that already ran — a sent SMS can leave no record, so `VOICE_INSTRUCTIONS`' "at most once per call" has nothing to act on. | On the fallback paths, keep `contents` and append a synthetic model part carrying `_FALLBACK_REPLY`, so the tool round survives in history instead of being dropped. |
| `llm.py:32` | `_get_client` | The second sentence exists only because module import order is load-bearing: `app.py` calls `load_dotenv()` after its import block, so this module cannot read env at import time. | Have `app.py` pass project and location to `llm` once at startup, and the ordering constraint (and the sentence) disappears. |
| `app.py:208` | `handle_message_ready` | Mild. One callback serves both channels (TAC's contract, exempt), but the per-channel tool assembly is a second job inside it, and it is what forces the third docstring sentence. | Extract `_tools_for(context) -> list[TACTool]`; the docstring then only has to describe the TAC callback contract. |

Module docstrings in all four files are longer than a sentence by design — they
are this demo's orientation text for a reader arriving cold, and the
one-sentence rule is about functions.
