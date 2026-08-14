# Complexity notes

Every function in `app.py`, `llm.py`, `web.py` and `events.py` that still needs
more than one sentence of *explanation* — over and above documenting its own
parameters and return value — is listed below. Nothing here should be acted on
casually: `CLAUDE.md` forbids production-hardening this demo.

The rule being applied: a function that needs more than a sentence of
explanation is a complexity smell worth revisiting, *unless* the extra sentences
are a vendor constraint (TAC, Twilio, Vertex/Gemini, the TLS-terminating proxy)
or an env-var contract. Those are exempt — no amount of refactoring makes an
external API's behavior self-evident.

Line numbers are as of the readability pass of 2026-08-14 on branch
`gemini-vertex`.

## Vendor-constraint / contract — exempt, leave alone

| Location | Function | Why the explanation is long |
|---|---|---|
| `app.py:134` | `_send_payment_link` | Its docstring **is** the LLM-facing tool description: `function_tool()` falls back to `func.__doc__` (`tac/tools/base.py:501`). Written for the model, not for the reader — do not add `Args:`/`Returns:` here, and do not reword it. |
| `app.py:161` | `payment_link_tool_for` | TAC's `configure_injection()` mutates the tool in place and returns `self`, so the tool has to be per-session. |
| `app.py:205` | `handoff_tool_for` | `create_studio_handoff_tool` raises without a flow SID / Orchestrator / memory store, and TAC's voice channel only *logs* exceptions from the message callback. |
| `app.py:270` | `handle_message_ready` | TAC's callback contract: one callback serves both channels, and `memory_response` is always `None` under the default `memory_mode="never"`. |
| `app.py:307` | `on_call_status` | Registering the handler is the side effect that makes TAC attach the status callback URL. |
| `app.py:322` | `start_reminder_call` | Twilio reports only `completed` unless `status_callback_event` is listed out. |
| `app.py:349` | `TrustProxyHTTPS` (class) | Twilio signs the full URL and TAC validates using `X-Forwarded-Proto`; a TLS-terminating proxy sends `http`. Pure ASGI because `@app.middleware("http")` never sees `websocket` scopes. |
| `llm.py:52` | `_model_facing_parameters` | Vertex rejects an OBJECT schema with empty `properties`. |
| `llm.py:88` | `_jsonable` | TAC's knowledge tool returns Pydantic models, which Gemini cannot serialize. |
| `web.py:52` | `dialing_allowlist` | `DEMO_ALLOWED_NUMBERS` env-var contract, guarding a route that places real, billed calls with no auth. |
| `web.py:113` | `voice_token` | `incoming_allow` is what lets `/handoff`'s `<Dial><Client>` ring the page. |
| `web.py:134` | `handoff` | Twilio requests `action_url` whenever a ConversationRelay session ends, not only on a handoff, and `callerId` must be a number the account owns. |
| `events.py:18` | `publish` | The `event_type` tag list is a contract with `static/index.html`, which styles each feed line by it. A `StrEnum` would not reach the JS side, so this is documentation, not a smell. |

## Complexity — worth revisiting

| Location | Function | What smells | Suggestion (one line) |
|---|---|---|---|
| `llm.py:115` | `run_turn` | Its vendor facts are exempt, but one is ours: both failure exits return the **pre-turn** history, silently discarding a tool round that already ran — a sent SMS can leave no record, so `VOICE_INSTRUCTIONS`' "at most once per call" has nothing to act on. | On the fallback paths, keep `contents` and append a synthetic model part carrying `_FALLBACK_REPLY`, so the tool round survives in history instead of being dropped. **Behavior change — out of scope for a readability pass.** |
| `llm.py:34` | `_get_client` | One sentence exists only because module import order is load-bearing: `app.py` calls `load_dotenv()` after its import block, so this module cannot read env at import time. | Considered on 2026-08-14 and **declined**: having `app.py` push project/location into `llm` swaps an implicit ordering constraint for an explicit one, plus a new setter, new module state, and a new way to get it wrong (import `llm` without calling it). Three lazy lines and one sentence is the cheaper trade. |

## Resolved by the 2026-08-14 readability pass

- `create_send_payment_link_tool` → `payment_link_tool_for(session)`, with the
  decorator/injection chain split into two named steps. The name now carries
  "one tool per conversation", so only the `configure_injection()` constraint
  needs saying.
- The redundant `studio_handoff_flow_sid` pre-check in the handoff factory is
  gone: `create_studio_handoff_tool` raises `ValueError` for exactly that case
  (`tac/tools/handoff.py:288`), and the `except ValueError` arm already degrades
  to "no handoff tool". One exit, one reason, no annotated guard.
- `handle_message_ready`'s per-channel tool assembly moved to
  `tools_for(session)`, and its inline `on_tool_call` closure to
  `publish_tool_call`. The callback is now orchestration only.

Module docstrings in all four files are longer than a sentence by design — they
are this demo's orientation text for a reader arriving cold, and the
one-sentence rule is about functions.
