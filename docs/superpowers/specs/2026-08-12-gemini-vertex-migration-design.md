# Design: replace the OpenAI Agents SDK with Gemini on Vertex AI

> **Superseded.** This specifies the hand-rolled `google-genai` design, which was
> replaced by `LitellmModel` on the OpenAI Agents SDK. Kept only as a record.

**Date:** 2026-08-12
**Branch:** `gemini-vertex`
**Status:** approved, not yet implemented

## Goal

Run the TAC payment-reminder demo's LLM turns on Gemini via Vertex AI instead of
the OpenAI Agents SDK. OpenAI is removed outright — no dual-provider
abstraction, no fallback.

The demo's purpose is unchanged: teach how TAC integrates with an LLM and how
TAC tools are handed to it. This migration makes that lesson *more* visible,
because the tool bridge stops being hidden inside an SDK method.

## Why this is a small change

`TACTool` already exposes everything a non-OpenAI runtime needs:

- `params_json_schema` — a plain JSON Schema dict
- `await tool(**kwargs)` — executes the tool, applying `InjectedToolArg` values

The OpenAI SDK was never load-bearing. It is confined to `app.py`: one import,
`set_tracing_disabled(True)`, three `.to_openai_agents_sdk_tool()` calls, and the
`Agent`/`Runner` block inside `handle_message_ready` (plus its `to_input_list()`,
`new_items` and `final_output_as` result accessors). Nothing else in the repo
touches it. TAC ships no Gemini adapter (only
`to_openai_format`, `to_anthropic_format`, `to_openai_agents_sdk_tool`), so we
write the bridge ourselves — about 30 lines.

## Architecture

New module `llm.py` owns everything Gemini. It imports `TACTool` for typing and
**nothing else from the app** — no `events`, no `web`. `app.py` keeps TAC
wiring, the three tool factories, and both prompts.

```
TACTool.params_json_schema  ──▶  types.FunctionDeclaration(parameters_json_schema=…)
Gemini function_call        ──▶  await tool(**call.args)
tool return value           ──▶  types.Part.from_function_response({"result": …})
```

The only thing crossing the seam is an `on_tool_call: Callable[[str], None]`
callback, so `app.py` still publishes the `tool` and `handoff` SSE events the
landing page renders.

`handle_message_ready` in `app.py` shrinks to: build the tool list, call
`llm.run_turn(...)`, publish events, return the reply string. Conversation
history changes type from `list[Any]` (OpenAI's `to_input_list()`) to
`list[types.Content]`, still keyed by `conversation_id` in an in-memory dict.

### `llm.py` shape

Signature:

```python
async def run_turn(
    *,
    user_message: str,
    history: list[types.Content],
    instructions: str,
    tools: list[TACTool],
    on_tool_call: Callable[[str], None],
) -> tuple[str, list[types.Content]]:   # (reply, updated history)
```

Illustrative body — the error handling and blocked-response guard described
below are both elided here:

```python
def _declare(tools: list[TACTool]) -> types.Tool:
    decls = []
    for t in tools:
        schema = t.params_json_schema
        decls.append(types.FunctionDeclaration(
            name=t.name,
            description=t.description,
            # Vertex rejects an OBJECT schema with no properties, so a tool
            # whose only params are injected must declare none at all.
            parameters_json_schema=schema if schema.get("properties") else None,
        ))
    return types.Tool(function_declarations=decls)

async def run_turn(*, user_message, history, instructions, tools, on_tool_call):
    by_name = {t.name: t for t in tools}
    contents = [*history, types.Content(role="user", parts=[types.Part(text=user_message)])]
    config = types.GenerateContentConfig(
        system_instruction=instructions,
        tools=[_declare(tools)] if tools else None,
        thinking_config=types.ThinkingConfig(thinking_budget=0),
    )

    for _ in range(MAX_TOOL_ROUNDS):
        response = await _get_client().aio.models.generate_content(
            model=_model(), contents=contents, config=config)
        contents.append(response.candidates[0].content)

        if not response.function_calls:
            return response.text or "", contents

        parts = []
        for call in response.function_calls:
            on_tool_call(call.name)
            result = await by_name[call.name](**(call.args or {}))
            parts.append(types.Part.from_function_response(
                name=call.name, response=_as_response(result)))
        contents.append(types.Content(role="user", parts=parts))

    return "Sorry, I'm having trouble with that right now.", contents
```

### The client must be built lazily, not at import

`app.py` calls `load_dotenv(override=True)` *after* its import block, so a
module-level `genai.Client(project=os.getenv("GOOGLE_CLOUD_PROJECT"))` in
`llm.py` would read the env **before** `.env` is loaded and come up empty
whenever the vars are only set in `.env` — which is the documented setup.

So `llm.py` reads env inside a cached accessor, not at module scope:

```python
_client: genai.Client | None = None

def _get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(
            vertexai=True,
            project=os.environ["GOOGLE_CLOUD_PROJECT"],
            location=os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1"),
        )
    return _client
```

This matches how `app.py` already defers `TACConfig.from_env()` to line 48
rather than import time, and how `events.py`/`web.py` read no env at import.
The model name gets the same treatment via a `_model()` helper reading
`GEMINI_MODEL` (default `gemini-2.5-flash`) — not a module-level constant.

### Blocked or empty responses

A safety block yields a candidate with no `content`, and `candidates` itself can
be empty. Appending `None` to `contents` would then break the next turn, so
`run_turn` treats a missing `candidates[0].content` the same as an error: return
the spoken apology and leave history unchanged.

### SDK facts this rests on

Verified against google-genai 2.17.0, not assumed:

| Fact | Source |
|---|---|
| `FunctionDeclaration.parameters_json_schema` exists and takes raw JSON Schema | `types.FunctionDeclaration.model_fields` |
| Function-response turns use `role="user"` | `google/genai/models.py` AFC path |
| Results are wrapped `{"result": value}`, errors `{"error": str(e)}` | `_extra_utils.get_function_response_parts_async` |
| TAC's schemas pass client-side validation | constructed both real shapes |

The alternative `parameters=` field is a `Schema` with an uppercase `Type` enum
and would have needed a lowercase→uppercase converter.

TAC's three tool schemas:

| Tool | LLM-visible params |
|---|---|
| `send_payment_link` | none (`{"type":"object","properties":{},"required":[]}`) |
| `search_renewal_faq` | `query: str` |
| `connect_to_human_agent` | `reason: str` |

### Serialization, and one deletion it enables

`_as_response` flattens Pydantic (duck-typed `model_dump`, including inside a
list) before wrapping in `{"result": …}`.

Because we now own serialization, **the re-wrap inside `get_knowledge_tool()`
goes away**. To be precise: `get_knowledge_tool()` and its module-level
`_knowledge_tool` cache both stay; what is deleted is the inner
`async def search_renewal_faq` shim and the `create_tool(...)` re-wrap around it
(plus the now-unused `create_tool` import). The function assigns the
`create_knowledge_tool` result directly. That shim exists only because
`to_openai_agents_sdk_tool()`'s `on_invoke` calls a bare `json.dumps` on
Pydantic models (KNOWN-ISSUES #1) — a method this branch no longer calls.
Duck-typing `model_dump` also means it keeps working if upstream TAC ever
returns plain dicts, which is the exact fragility the `uv.lock` pin note in
CLAUDE.md warns about.

## Decisions

| Decision | Choice | Reasoning |
|---|---|---|
| Auth | ADC via `gcloud auth application-default login` | No secrets in `.env`; standard Vertex path |
| Runtime | native `google-genai` + explicit tool loop | Makes the TAC→LLM bridge visible, which is the demo's point; also lets us own serialization |
| Loop location | new `llm.py` | Clean seam between TAC and the LLM; keeps `app.py` focused |
| Model | `gemini-2.5-flash`, `thinking_budget=0` | 2.5 Flash thinks by default; on a voice call that latency is audible dead air. Both env-tunable so the tradeoff can be demoed |
| Provider abstraction | none | YAGNI. "Instead of", not "in addition to" |
| Scope | local + ngrok only | Explicitly agreed. The twl deploy is out of scope — see below |

### `MAX_TOOL_ROUNDS` is a bound, not hardening

An unbounded `while` that the model keeps feeding tool calls means dead air on a
live call, and nothing else backstops it. Set to 5; exhausting it returns a
spoken apology rather than an empty string.

### Error handling follows an existing precedent

The voice channel only *logs* exceptions raised from the message callback, so
anything that raises mid-turn is dead air — the same failure mode that motivated
`get_handoff_tool` degrading to `None`. `run_turn` therefore catches around the
API call and returns a spoken apology. This is the existing pattern, not new
hardening.

## Configuration

`.env` gains three vars and loses one:

```
GOOGLE_CLOUD_PROJECT=<project id>
GOOGLE_CLOUD_LOCATION=us-central1
GEMINI_MODEL=gemini-2.5-flash
```

`OPENAI_API_KEY` is removed. `load_dotenv(override=True)` stays as-is.

One-time operator setup (interactive, run by the user):

```
brew install --cask google-cloud-sdk
gcloud auth application-default login
gcloud services enable aiplatform.googleapis.com --project <project>
```

### Dependencies

Remove `openai-agents`, add `google-genai>=2.17.0`.

**`uv.lock` care:** the lock pins the TAC **git** dependency to a commit, and
CLAUDE.md flags an unpinned TAC as a real breakage. After `uv add`/`uv remove`,
confirm `git diff uv.lock` leaves the TAC `revision` untouched. If the resolver
bumped it, that is a separate decision, not a side effect of this change.

## Out of scope: the twl deploy

`twl` injects **environment variables only** — no file secrets, no volume
mounts (`twl --help`), and `.dockerignore` deliberately keeps every credential
out of the build context. ADC is a file on the laptop, so it cannot reach the
container as designed.

This branch is therefore **local + ngrok only**. The currently-deployed twl app
keeps running the OpenAI version on `main` and is unaffected. Getting Vertex
credentials into the container (service-account JSON as a base64 env var,
decoded to `service_account.Credentials.from_service_account_info` and passed as
`genai.Client(credentials=…)`) is a follow-up, recorded in `KNOWN-ISSUES.md`.

## Risk: corporate TLS interception

Zscaler intercepts selectively — `*.twilio.com` is served its genuine cert,
ngrok's endpoint was not. If `*.googleapis.com` is intercepted, `google-genai`
(httpx → certifi) fails cert validation. Unlike ngrok's Go binary, Python
honors `SSL_CERT_FILE`, so a keychain-exported bundle is the fix. Document in
`zscaler_issues.md` only if it actually bites.

## Verification plan

The riskiest assumption is server-side: whether Vertex accepts
`send_payment_link`'s no-parameter declaration. That gets tested before Twilio
is involved at all.

In order:

1. `uv sync` clean
2. `python -m py_compile app.py llm.py web.py events.py`
3. `python -c "import app"` smoke test
4. **Throwaway harness** — call `run_turn` directly with two fake `TACTool`s,
   one no-arg and one with a `query` param. Assert each tool body actually
   executed and text came back. Proves the loop and the empty-schema handling
   with no phone ringing and no billed call.
5. Only with explicit go-ahead: one real end-to-end call exercising the
   greeting, an FAQ question, the payment-link SMS, and the human handoff.

Per CLAUDE.md: do not place calls or send SMS without asking. Check SMS delivery
in the Messages log, not the live feed — the feed publishes `sms_sent` when TAC
accepts the message, before Twilio decides to reject it.

## Documentation to update

| File | Change |
|---|---|
| `CLAUDE.md` | LLM-runtime line; add `llm.py` to the layout table; current-state note |
| `README.md` | Arch diagram, prerequisites, env vars |
| `HANDOFF.md` | Arch diagram line |
| `KNOWN-ISSUES.md` | #1 moot on this path; add the twl-credential follow-up |
| `.env.example` | Swap `OPENAI_API_KEY` for the three `GOOGLE_*`/`GEMINI_*` vars |
| `pyproject.toml` | Dependency comments |
