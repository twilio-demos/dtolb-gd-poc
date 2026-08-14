# Complexity notes

Docstrings in `app.py`, `llm.py`, `web.py` and `events.py` are **a one-line
summary plus `Args:`** — no `Returns:` section, since the type hint and the
function name carry it, and no multi-sentence `Args:` entries. Anything a
function needs explained beyond that lives **one level up, in the module
docstring**. That is a deliberate trade: the *why* sits a little further from the
code it explains, in exchange for functions you can read at a glance.

So the table below is a map, not a to-do list: for each fact that no code can
express, where it lives now. **Every one of these was written after a real
observed failure or a review round that corrected a wrong comment — relocate
them, never delete them.** Nothing here should be acted on casually; `CLAUDE.md`
forbids production-hardening this demo.

Line numbers are as of the docstring-trimming pass of 2026-08-14 on branch
`gemini-vertex`.

## Where each vendor constraint lives

| The fact | Lives in |
|---|---|
| A TLS-terminating proxy sends `X-Forwarded-Proto: http`, so every Twilio signature check 403s — webhooks and the ConversationRelay websocket alike | `TrustProxyHTTPS` class docstring, `app.py:321`. Kept on the class: it is the clearest home, and a class is not a one-statement function. |
| An exported `TWILIO_API_KEY` shadows `.env` unless `load_dotenv(override=True)` | comment at `app.py:65` — a module-level call has no enclosing docstring |
| Studio answers 400 for `Direction=outbound-api`, so TAC's built-in Studio handoff cannot serve an outbound call; an explicit `action_url` outranks `studio_handoff_flow_sid` and `/handoff` renders the TwiML | `app.py` module docstring |
| `configure_injection()` mutates in place and returns `self`, so the payment-link and handoff tools are built per conversation; the knowledge tool takes no session and is shared | `app.py` module docstring |
| `create_studio_handoff_tool` raises without a flow SID / Orchestrator / memory store, and the voice channel only *logs* exceptions from the message callback, so `handoff_tool_for` degrades to `None` | `app.py` module docstring |
| The payment link and the handoff are voice-only — an SMS replier already has the link, and Studio handoff has no messaging path | `app.py` module docstring |
| `on_message_ready` serves both channels and its `memory_response` is always `None` under `memory_mode="never"` | `app.py` module docstring, plus a one-line `Args:` entry on `handle_message_ready` (`app.py:255`) |
| Registering `on_call_status` is what makes TAC attach the status callback URL, and Twilio then reports only `completed` unless `status_callback_event` is listed out | `app.py` module docstring |
| Vertex rejects an OBJECT schema with empty `properties`, so an all-injected tool declares none | `llm.py` module docstring, plus the `Args:` line on `_model_facing_parameters` (`llm.py:66`) |
| TAC's knowledge tool returns Pydantic models, which Gemini cannot serialize | `llm.py` module docstring |
| Tool results go back with `role="user"`, matching the Gemini SDK's own automatic-function-calling path | `llm.py` module docstring |
| Thinking is off because 2.5 Flash reasons before answering by default, which is dead air on a call | `llm.py` module docstring |
| Exceptions are caught and answered as text because the voice channel only logs them — raising is silence on a live call | `llm.py` module docstring |
| `_get_client` is lazy because `app.py` calls `load_dotenv()` after its import block | `llm.py` module docstring |
| Both of `run_turn`'s failure exits return the **pre-turn** history, dropping any tool round that already ran — a sent SMS can leave no record, so `VOICE_INSTRUCTIONS`' "at most once per call" has nothing to act on | `llm.py` module docstring, as an explicitly-labelled known quirk (it is ours, not a vendor's) |
| `POST /api/call` has no auth and every call is real and billed, so `DEMO_ALLOWED_NUMBERS` caps who a public deployment can dial | `web.py` module docstring; the value format is the one-line docstring on `dialing_allowlist` (`web.py:64`) |
| Twilio requests `action_url` whenever a ConversationRelay session ends, not only on a handoff, and `callerId` must be a number the account owns | `web.py` module docstring |
| `incoming_allow` is what lets `/handoff`'s `<Dial><Client>` ring the page | `web.py` module docstring |
| The `event_type` values are a contract with `static/index.html`, which styles each feed line by them | the `Args:` entry on `events.publish` (`events.py:18`) — a compact tag list, deliberately not a table |

`_send_payment_link` (`app.py:147`) is the hard exception: its docstring **is**
the LLM-facing tool description, because `function_tool()` falls back to
`func.__doc__` (`tac/tools/base.py:501`). It is written for the model, not the
reader. No `Args:`, no rewording, no trimming.

## Complexity — worth revisiting

| Location | Function | What smells | Suggestion (one line) |
|---|---|---|---|
| `llm.py:114` | `run_turn` | Its vendor facts are exempt, but one is ours: both failure exits return the **pre-turn** history, silently discarding a tool round that already ran. | On the fallback paths, keep `contents` and append a synthetic model part carrying `_FALLBACK_REPLY`, so the tool round survives in history instead of being dropped. **Behavior change — out of scope for a readability pass.** |
| `llm.py:54` | `_get_client` | The laziness exists only because module import order is load-bearing. | Considered on 2026-08-14 and **declined**: having `app.py` push project/location into `llm` swaps an implicit ordering constraint for an explicit one, plus a new setter, new module state, and a new way to get it wrong (import `llm` without calling it). Three lazy lines is the cheaper trade. |

## Resolved by the 2026-08-14 readability passes

- `create_send_payment_link_tool` → `payment_link_tool_for(session)`, with the
  decorator/injection chain split into two named steps. The name carries "one
  tool per conversation".
- The redundant `studio_handoff_flow_sid` pre-check in the handoff factory is
  gone: `create_studio_handoff_tool` raises `ValueError` for exactly that case
  (`tac/tools/handoff.py:288`), and the `except ValueError` arm already degrades
  to "no handoff tool".
- `handle_message_ready`'s per-channel tool assembly moved to
  `tools_for(session)`, and its inline `on_tool_call` closure to
  `publish_tool_call`. The callback is now orchestration only.
- Function docstrings trimmed to summary + `Args:` (275 lines → 144), with the
  constraints above relocated to the module docstrings (48 → 101) rather than
  dropped.
