# Gemini / Vertex AI Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the OpenAI Agents SDK with Gemini on Vertex AI as the demo's LLM runtime, moving the TAC→LLM tool bridge into a new `llm.py`.

**Architecture:** A new `llm.py` owns everything Gemini — lazily-built Vertex client, `TACTool` → `FunctionDeclaration` conversion, and an explicit tool-dispatch loop. `app.py` keeps TAC wiring, the three tool factories, and both prompts; its `handle_message_ready` calls `llm.run_turn(...)` and passes an `on_tool_call` callback so SSE events still publish. OpenAI is removed outright.

**Tech Stack:** Python 3.12, `google-genai>=2.17.0` (Vertex mode), TAC SDK (git-pinned), FastAPI, uv.

**Spec:** `docs/superpowers/specs/2026-08-12-gemini-vertex-migration-design.md` (commit `1e18086`)

## Global Constraints

Every task's requirements implicitly include these.

- **Branch:** `gemini-vertex`. Already created. Do not merge to `main`.
- **`google-genai>=2.17.0`** — `parameters_json_schema` does not exist below this.
- **Model default `gemini-2.5-flash`, `thinking_budget=0`.** Both env-tunable via `GEMINI_MODEL`.
- **`MAX_TOOL_ROUNDS = 5`.**
- **No test suite exists and none is being added.** This is a teaching demo; CLAUDE.md forbids production-hardening it. Verification is `py_compile`, import smoke tests, and the Task 2 harness — not pytest.
- **Comment style: senior, sparse, present-tense.** Comment only a non-obvious *why*, a constraint, or an SDK gotcha. Do not narrate debugging history. Do not scatter KNOWN-ISSUES numbers through source.
- **In-memory dicts are deliberate.** No persistence, retries, auth, or multi-user handling.
- **Do NOT remove any of these five** (CLAUDE.md: each was added after a real observed failure): `TrustProxyHTTPS` middleware, the `action_url` → `/handoff` route, the narrow consent wording in `VOICE_INSTRUCTIONS`, the `DEMO_ALLOWED_NUMBERS` guard, the tracked `uv.lock`.
- **`load_dotenv(override=True)` stays.** A shell-exported `TWILIO_API_KEY` otherwise shadows `.env`.
- **`uv.lock` must keep its current TAC git revision.** Check after any dependency change; a moved revision is a separate decision, not a side effect.
- **Do not place calls or send SMS without explicit permission** — both cost money and ring a real phone.
- **Scope is local + ngrok.** Do not `twl deploy` this branch.
- `timeout` is not a stock macOS binary; use `curl -m N`.

---

## Prerequisites (operator-run, interactive)

These are **not** agent steps — they need a browser and a Google account. The
operator runs them before Task 2 can be verified. In Claude Code, prefixing with
`!` runs them in-session.

```bash
brew install --cask google-cloud-sdk
gcloud auth application-default login
gcloud config set project <project-id>
gcloud services enable aiplatform.googleapis.com --project <project-id>
```

Confirm before starting Task 2:

```bash
gcloud auth application-default print-access-token | head -c 20
ls ~/.config/gcloud/application_default_credentials.json
```

Expected: a token prefix and the file path. If `gcloud` is missing or ADC is
absent, **stop** — Task 2's verification cannot run.

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `llm.py` | Create | All Gemini: lazy Vertex client, `TACTool`→`FunctionDeclaration`, tool loop. Imports `TACTool` for typing and nothing else from the app |
| `app.py` | Modify | Drops `agents` imports; `handle_message_ready` delegates to `llm.run_turn`; `get_knowledge_tool` loses its serialization shim |
| `pyproject.toml` | Modify | `-openai-agents`, `+google-genai` |
| `.env.example` | Modify | `-OPENAI_API_KEY`, `+GOOGLE_CLOUD_PROJECT` / `GOOGLE_CLOUD_LOCATION` / `GEMINI_MODEL` |
| `CLAUDE.md`, `README.md`, `HANDOFF.md`, `KNOWN-ISSUES.md` | Modify | Runtime name, arch diagrams, layout table, prereqs, issue #1 status |
| `/tmp/check_llm.py` | Create (throwaway) | Proves the loop against real Vertex with no Twilio involvement. Deliberately outside the repo |

`events.py`, `web.py`, `static/index.html`, `Dockerfile`, `studio-flow.json` are **untouched**.

---

## Task 1: Add the Gemini dependency and config surface

`openai-agents` stays installed through this task so the tree keeps importing at
every commit. Task 3 removes it.

**Files:**
- Modify: `pyproject.toml:6-14`
- Modify: `.env.example` (the `# --- LLM ---` block)
- Modify: `uv.lock` (generated)

**Interfaces:**
- Consumes: nothing
- Produces: `google.genai` importable in `.venv`; `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION`, `GEMINI_MODEL` documented in `.env.example`

- [ ] **Step 1: Record the current TAC pin so a bump can't slip through**

```bash
grep -n -A2 'name = "twilio-agent-connect"' uv.lock | head -10
```

Save that output. The `revision = "…"` line must be identical at Step 5.

- [ ] **Step 2: Add the dependency**

```bash
uv add 'google-genai>=2.17.0'
```

- [ ] **Step 3: Replace the dependency comment block in `pyproject.toml`**

Replace lines 6-14 with:

```toml
dependencies = [
    # TAC SDK with FastAPI server support -- the main entry point for all Twilio communications
    "twilio-agent-connect[server] @ git+https://github.com/twilio/twilio-agent-connect-python.git",
    # Gemini on Vertex AI -- the LLM runtime. llm.py bridges TAC tools to it via
    # TACTool.params_json_schema; needs >=2.17.0 for parameters_json_schema.
    "google-genai>=2.17.0",
    # OpenAI Agents SDK -- removed in a later commit on this branch
    "openai-agents>=0.1.0",
    # Twilio helper lib -- used only for the Voice JS SDK access token (browser softphone)
    "twilio>=9.8.3",
    "python-dotenv>=1.0.0",
]
```

- [ ] **Step 4: Swap the LLM block in `.env.example`**

Replace:

```
# --- LLM ---
OPENAI_API_KEY=
```

with:

```
# --- LLM: Gemini on Vertex AI ---
# Auth is ADC, not a key in this file. One time:
#   gcloud auth application-default login
#   gcloud services enable aiplatform.googleapis.com --project <project-id>
GOOGLE_CLOUD_PROJECT=
GOOGLE_CLOUD_LOCATION=us-central1
# gemini-2.5-flash keeps voice turns snappy. llm.py also sets thinking_budget=0;
# 2.5 Flash reasons before answering by default, which on a call is dead air.
GEMINI_MODEL=gemini-2.5-flash
```

- [ ] **Step 5: Verify the install and that the TAC pin did not move**

```bash
uv sync
uv run python -c "import google.genai; print(google.genai.__version__)"
grep -n -A2 'name = "twilio-agent-connect"' uv.lock | head -10
```

Expected: a version `>= 2.17.0`, and a `revision =` identical to Step 1. **If the
revision changed, stop and report it** — do not "fix" it silently.

- [ ] **Step 6: Confirm the app still imports**

```bash
uv run python -c "import app; print('ok')"
```

Expected: `ok`. (Nothing has been rewired yet.)

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock .env.example
git commit -m "Add google-genai dependency and Vertex config vars"
```

---

## Task 2: Create `llm.py` and prove it against real Vertex

The riskiest assumption in the whole migration lives here: whether Vertex accepts
a `FunctionDeclaration` for a tool with no LLM-visible parameters
(`send_payment_link`). This task settles it without ringing a phone.

**Files:**
- Create: `llm.py`
- Create: `/tmp/check_llm.py` (throwaway, outside the repo)

**Interfaces:**
- Consumes: `google.genai` from Task 1; `tac.tools.TACTool`
- Produces, relied on by Task 3:
  - `llm.History = list[types.Content]`
  - `async llm.run_turn(*, user_message: str, history: History, instructions: str, tools: list[TACTool], on_tool_call: Callable[[str], None]) -> tuple[str, History]` — returns `(reply, updated_history)`

- [ ] **Step 1: Create `llm.py`**

```python
"""
Gemini (Vertex AI) runtime for the demo's LLM turns.

TAC ships no Gemini adapter, so this module *is* the bridge: a TACTool's
params_json_schema becomes a Gemini FunctionDeclaration, and a function_call
coming back is dispatched through ``await tool(**args)``, which is also what
applies the tool's injected arguments. Everything else here is the turn loop.

Knows nothing about Twilio on purpose — run_turn takes an on_tool_call callback
so the caller publishes its own events.
"""

import os
from collections.abc import Callable

from google import genai
from google.genai import types

from tac.tools import TACTool

History = list[types.Content]

# A model that only ever calls tools never speaks, and the voice channel gives
# the caller silence while it waits.
MAX_TOOL_ROUNDS = 5

_FALLBACK_REPLY = "Sorry, I'm having trouble with that right now."

_client: genai.Client | None = None


def _get_client() -> genai.Client:
    """Vertex client, built on first use.

    Not at import: app.py calls load_dotenv() *after* its import block, so
    module-level env reads here would run before .env exists.
    """
    global _client
    if _client is None:
        _client = genai.Client(
            vertexai=True,
            project=os.environ["GOOGLE_CLOUD_PROJECT"],
            location=os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1"),
        )
    return _client


def _model() -> str:
    return os.getenv("GEMINI_MODEL", "gemini-2.5-flash")


def _declare(tools: list[TACTool]) -> types.Tool:
    """Convert TAC tools into one Gemini tool declaration."""
    declarations = []
    for tool in tools:
        schema = tool.params_json_schema
        declarations.append(
            types.FunctionDeclaration(
                name=tool.name,
                description=tool.description,
                # Vertex rejects an OBJECT schema with no properties, so a tool
                # whose every parameter is injected must declare none at all.
                parameters_json_schema=schema if schema.get("properties") else None,
            )
        )
    return types.Tool(function_declarations=declarations)


def _as_response(result: object) -> dict[str, object]:
    """Wrap a tool's return value the way Gemini expects.

    Pydantic models are flattened first — the knowledge tool returns them and
    they are not JSON-serializable. Duck-typed, so plain dicts pass through.
    """
    if isinstance(result, list):
        result = [
            item.model_dump() if hasattr(item, "model_dump") else item for item in result
        ]
    elif hasattr(result, "model_dump"):
        result = result.model_dump()  # type: ignore[attr-defined]
    return {"result": result}


async def run_turn(
    *,
    user_message: str,
    history: History,
    instructions: str,
    tools: list[TACTool],
    on_tool_call: Callable[[str], None],
) -> tuple[str, History]:
    """Run one turn to completion, executing tool calls. Returns (reply, history)."""
    by_name = {tool.name: tool for tool in tools}
    contents: History = [
        *history,
        types.Content(role="user", parts=[types.Part.from_text(text=user_message)]),
    ]
    config = types.GenerateContentConfig(
        system_instruction=instructions,
        tools=[_declare(tools)] if tools else None,
        # 2.5 Flash reasons before answering by default; on a call that is dead air.
        thinking_config=types.ThinkingConfig(thinking_budget=0),
    )

    try:
        for _ in range(MAX_TOOL_ROUNDS):
            response = await _get_client().aio.models.generate_content(
                model=_model(), contents=contents, config=config
            )

            candidate = response.candidates[0].content if response.candidates else None
            if candidate is None:
                # Safety block, or no candidate at all. Appending None here would
                # corrupt the history for every later turn.
                return _FALLBACK_REPLY, history
            contents.append(candidate)

            if not response.function_calls:
                return response.text or "", contents

            parts = []
            for call in response.function_calls:
                on_tool_call(call.name)
                result = await by_name[call.name](**(call.args or {}))
                parts.append(
                    types.Part.from_function_response(
                        name=call.name, response=_as_response(result)
                    )
                )
            # role="user" carries tool results, matching the SDK's own
            # automatic-function-calling path.
            contents.append(types.Content(role="user", parts=parts))
    except Exception as exc:
        # The voice channel only logs exceptions raised from the message
        # callback, so raising here is silence on a live call.
        print(f"LLM turn failed: {exc}")
        return _FALLBACK_REPLY, history

    return _FALLBACK_REPLY, contents
```

- [ ] **Step 2: Compile it**

```bash
uv run python -m py_compile llm.py && echo compiled
```

Expected: `compiled`.

- [ ] **Step 3: Write the throwaway harness**

Write to `/tmp/check_llm.py` — outside the repo on purpose:

```python
"""Throwaway: proves llm.run_turn against real Vertex, with no Twilio involved.

Covers the two declaration shapes the demo actually uses -- one tool with no
LLM-visible parameters (like send_payment_link) and one with a string parameter
(like search_renewal_faq) -- plus replaying returned history on a second turn.
"""

import asyncio
import sys

REPO = "/Users/dtolbert/code/gd-poc"
sys.path.insert(0, REPO)

from dotenv import load_dotenv

load_dotenv(f"{REPO}/.env", override=True)

from tac.tools import function_tool  # noqa: E402

import llm  # noqa: E402

calls: list[str] = []


async def ping() -> str:
    """Return the secret word."""
    calls.append("ping")
    return "banana"


async def lookup(query: str) -> dict:
    """Look up a fact about the given query."""
    calls.append(f"lookup:{query}")
    return {"answer": "42"}


INSTRUCTIONS = "You are a test harness. Use the tools when asked, then report the result."


async def main() -> None:
    no_arg = function_tool(name="ping")(ping)
    with_arg = function_tool(name="lookup")(lookup)
    print("ping schema  :", no_arg.params_json_schema)
    print("lookup schema:", with_arg.params_json_schema)

    tools = [no_arg, with_arg]

    reply, history = await llm.run_turn(
        user_message="Call the ping tool and tell me the secret word.",
        history=[],
        instructions=INSTRUCTIONS,
        tools=tools,
        on_tool_call=lambda name: print("  tool called:", name),
    )
    print("reply 1:", reply)
    assert "ping" in calls, f"no-arg tool never executed; calls={calls}"
    assert "banana" in reply.lower(), f"tool result not reflected in reply: {reply!r}"

    reply2, _ = await llm.run_turn(
        user_message="Now look up the answer for 'meaning of life'.",
        history=history,
        instructions=INSTRUCTIONS,
        tools=tools,
        on_tool_call=lambda name: print("  tool called:", name),
    )
    print("reply 2:", reply2)
    assert any(c.startswith("lookup:") for c in calls), f"param tool never executed; calls={calls}"

    print("\nOK - both declarations accepted by Vertex, both tools executed, history replayed.")


asyncio.run(main())
```

- [ ] **Step 4: Add the two Vertex vars to the real `.env`**

`.env` is gitignored, so this is a local edit. Append (substituting the real
project id):

```
GOOGLE_CLOUD_PROJECT=<project-id>
GOOGLE_CLOUD_LOCATION=us-central1
GEMINI_MODEL=gemini-2.5-flash
```

- [ ] **Step 5: Run the harness**

```bash
uv run python /tmp/check_llm.py
```

Expected, in order: both schemas printed (`ping` shows `'properties': {}`),
`tool called: ping`, a reply containing "banana", `tool called: lookup`, then the
final `OK` line.

Failure triage:
- `LLM turn failed: … 400 … properties: should be non-empty for OBJECT type` — the
  empty-schema guard in `_declare` is wrong; report it rather than patching blind.
- `LLM turn failed: … 403 …` / `DefaultCredentialsError` — ADC or the
  `aiplatform.googleapis.com` enablement is missing. Re-run the prerequisites.
- `LLM turn failed: … CERTIFICATE_VERIFY_FAILED` — corporate TLS interception on
  `*.googleapis.com`. Retry with `SSL_CERT_FILE` pointed at a keychain-exported
  bundle, then note it in `zscaler_issues.md`.
- Assertion on `"banana"` only (tools did fire) — the model paraphrased. Read the
  reply; if the word is genuinely relayed some other way, loosen that one assert.

- [ ] **Step 6: Commit `llm.py`**

The harness is not committed — it lives in `/tmp`.

```bash
git add llm.py
git commit -m "Add llm.py: Gemini/Vertex tool loop bridging TACTool schemas"
```

---

## Task 3: Rewire `app.py` and drop OpenAI

**Files:**
- Modify: `app.py:17-46` (imports, `set_tracing_disabled`)
- Modify: `app.py:166-200` (`get_knowledge_tool`)
- Modify: `app.py:229-279` (`handle_message_ready` and the history dict)
- Modify: `pyproject.toml` (remove `openai-agents`)

**Interfaces:**
- Consumes: `llm.run_turn`, `llm.History` from Task 2
- Produces: an `app` module with no `agents` import anywhere

- [ ] **Step 1: Replace the import block**

In `app.py`, replace:

```python
from agents import Agent, Runner, set_tracing_disabled
from dotenv import load_dotenv
from fastapi import FastAPI
```

with:

```python
from dotenv import load_dotenv
from fastapi import FastAPI
```

Then replace:

```python
from tac.tools import InjectedToolArg, TACTool, create_tool, function_tool
```

with:

```python
from tac.tools import InjectedToolArg, TACTool, function_tool
```

Then replace:

```python
import events
import web
```

with:

```python
import events
import llm
import web
```

- [ ] **Step 2: Drop the OpenAI tracing call**

Replace:

```python
# override=True: an exported TWILIO_API_KEY would otherwise shadow .env.
load_dotenv(override=True)
set_tracing_disabled(True)
```

with:

```python
# override=True: an exported TWILIO_API_KEY would otherwise shadow .env.
load_dotenv(override=True)
```

- [ ] **Step 3: Simplify `get_knowledge_tool`**

Replace the whole block from the `# Built once and cached;` comment through the
end of `get_knowledge_tool` (currently `app.py:166-200`) with:

```python
# Built once and cached; the knowledge tool takes no per-conversation session.
_knowledge_tool: TACTool | None = None


async def get_knowledge_tool() -> TACTool | None:
    global _knowledge_tool
    if _knowledge_tool is None and tac.knowledge_client and tac.config.knowledge_base_id:
        _knowledge_tool = await create_knowledge_tool(
            knowledge_client=tac.knowledge_client,
            knowledge_base_id=tac.config.knowledge_base_id,
            name="search_renewal_faq",
            description=(
                "Search Owl Shoes' renewal and billing FAQ. "
                "The input MUST be a question in the form of a string."
            ),
            top_k=3,
        )
    return _knowledge_tool
```

The deleted `search_renewal_faq` shim only existed to `.model_dump()` Pydantic
results around a bug in `to_openai_agents_sdk_tool()`; `llm._as_response` now
handles that for every tool.

- [ ] **Step 4: Rewrite the LLM loop section**

Replace the whole block from the `# --- LLM loop ---` banner through the end of
`handle_message_ready` (currently `app.py:229-279`, ending at `return reply`) with:

```python
# --- LLM loop --------------------------------------------------------------
# TAC calls this with each transcribed utterance (voice) or inbound SMS body;
# the string we return is spoken or texted back. llm.py holds the Gemini side.

conversation_history: dict[str, llm.History] = {}


async def handle_message_ready(
    user_message: str,
    context: ConversationSession,
    # Always None: both channels use the default memory_mode="never", and
    # conversation_history carries context instead. To use TAC memory, set
    # memory_mode="always" and compose the prompt with MemoryPromptBuilder.
    _memory_response: TACMemoryResponse | None,
) -> str:
    events.publish("caller_said", f"Customer: {user_message}")

    on_voice = context.channel == "VOICE"

    tools: list[TACTool] = []
    if on_voice:
        # Voice only: an SMS replier already has the link, and handoff has no
        # messaging path.
        tools.append(create_send_payment_link_tool(context))
        handoff_tool = get_handoff_tool(context)
        if handoff_tool:
            tools.append(handoff_tool)
    knowledge_tool = await get_knowledge_tool()
    if knowledge_tool:
        tools.append(knowledge_tool)

    def on_tool_call(tool_name: str) -> None:
        events.publish("tool", f"Agent used tool: {tool_name}")
        if tool_name == "connect_to_human_agent":
            events.publish("handoff", "Transferring to a human — your browser will ring!")

    reply, history = await llm.run_turn(
        user_message=user_message,
        history=conversation_history.get(context.conversation_id, []),
        instructions=VOICE_INSTRUCTIONS if on_voice else SMS_INSTRUCTIONS,
        tools=tools,
        on_tool_call=on_tool_call,
    )
    conversation_history[context.conversation_id] = history

    events.publish("agent_said", f"Ava: {reply}")
    return reply
```

Note the feed ordering improves as a side effect: `on_tool_call` fires *before*
the tool runs, so `Agent used tool: send_payment_link` now precedes `sms_sent`
instead of trailing it.

- [ ] **Step 5: Confirm no `agents` references survive**

```bash
grep -rn "openai\|from agents\|to_openai_agents_sdk_tool\|Runner\|set_tracing_disabled" app.py llm.py web.py events.py || echo "clean"
```

Expected: `clean`.

- [ ] **Step 6: Remove the dependency**

```bash
uv remove openai-agents
```

Then delete the leftover comment line in `pyproject.toml`:

```toml
    # OpenAI Agents SDK -- removed in a later commit on this branch
```

- [ ] **Step 7: Verify the TAC pin again, then compile and import**

The pin is a `#<sha>` fragment on the `source = { git = … }` line, not a
`revision =` key — grepping for the package name misses it. Use:

```bash
grep -n 'twilio-agent-connect-python.git#' uv.lock
git diff main -- uv.lock | grep -E '^[+-].*twilio-agent-connect-python\.git' || echo "TAC pin unchanged vs main"
uv sync
uv run python -m py_compile app.py llm.py web.py events.py && echo compiled
uv run python -c "import app; print('ok')"
uv run python -c "import agents" 2>&1 | tail -1
```

Expected: the same TAC `revision` as Task 1 Step 1; `compiled`; `ok`; and
`ModuleNotFoundError: No module named 'agents'` on the last line — proof the
runtime is gone, not merely unreferenced.

- [ ] **Step 8: Commit**

```bash
git add app.py pyproject.toml uv.lock
git commit -m "Run LLM turns on Gemini/Vertex via llm.py; drop openai-agents"
```

- [ ] **Step 9: STOP and ask before live traffic**

Do not proceed to a real call unprompted. Report that the code path is complete
and ask permission, naming the cost: one billed outbound call to a real phone,
plus one billed SMS if the payment-link leg is exercised.

When granted, run the app (`uv run python app.py`) with ngrok, trigger from the
landing page, and exercise: the greeting, an FAQ question (`search_renewal_faq`),
agreeing to the link (`send_payment_link`), and asking for a person
(`connect_to_human_agent` → browser softphone rings).

Check SMS delivery in the Twilio **Messages log**, not the live feed — the feed
publishes `sms_sent` when TAC accepts the message, which is before Twilio decides
to reject it. All three 30034 failures in KNOWN-ISSUES #13 looked like successes
on the dashboard.

---

## Task 4: Update the documentation

**Files:**
- Modify: `CLAUDE.md`, `README.md:20-30`, `README.md:56-57`, `HANDOFF.md:55-64`, `HANDOFF.md:141`, `KNOWN-ISSUES.md`

**Interfaces:**
- Consumes: the finished code from Task 3
- Produces: docs that match the code

- [ ] **Step 1: `CLAUDE.md` — the runtime line**

Replace:

```
- LLM runtime is the OpenAI Agents SDK (TAC tools convert via
  `.to_openai_agents_sdk_tool()`).
```

with:

```
- LLM runtime is Gemini on Vertex AI via `google-genai`. TAC ships no Gemini
  adapter, so `llm.py` is the bridge: `TACTool.params_json_schema` becomes a
  `FunctionDeclaration`, and calls dispatch through `await tool(**args)`.
```

- [ ] **Step 2: `CLAUDE.md` — the layout table**

Replace the `app.py` row:

```
| `app.py` | All TAC: channels, LLM loop, the 3 tools, outbound call, `TrustProxyHTTPS` |
```

with these two rows:

```
| `app.py` | All TAC: channels, prompts, the 3 tools, outbound call, `TrustProxyHTTPS` |
| `llm.py` | All Gemini: lazy Vertex client, `TACTool`→`FunctionDeclaration`, the tool loop |
```

- [ ] **Step 3: `CLAUDE.md` — add a gotcha**

Add to the "Gotchas that will waste your time" list:

```
- **`llm.py` builds its Vertex client lazily.** `app.py` calls `load_dotenv()`
  after its import block, so reading `GOOGLE_CLOUD_PROJECT` at module scope
  there would see an empty env. Vertex auth is ADC — `gcloud auth
  application-default login`, no key in `.env`.
- **This branch is local/ngrok only.** `twl` injects env vars and ADC is a file,
  so the deployed container has no Vertex credential yet.
```

- [ ] **Step 4: `README.md` — the arch diagram**

One line changes. In the diagram at `README.md:25`, replace this line:

```
     └──────── events.py ◀── handle_message_ready() ──▶ OpenAI Agents SDK
```

with:

```
     └──────── events.py ◀── handle_message_ready() ──▶ llm.py ──▶ Gemini (Vertex AI)
```

The label is the last thing on that line, so the tool-branch characters on the
three lines below sit at fixed columns and are **unaffected**. Do not re-indent
them.

- [ ] **Step 5: `README.md` — prerequisites**

Replace:

```
voice+SMS-capable phone number, an OpenAI API key, and **one way to be publicly
```

with:

```
voice+SMS-capable phone number, a Google Cloud project with the Vertex AI API
enabled (`gcloud auth application-default login`), and **one way to be publicly
```

- [ ] **Step 6: `HANDOFF.md` — the arch diagram and file table**

`HANDOFF.md`'s diagram is spaced differently from `README.md`'s, so use these
exact strings. Replace this line at `HANDOFF.md:61`:

```
     └──────── events.py ◀── handle_message_ready() ──▶ OpenAI Agents SDK
```

with:

```
     └──────── events.py ◀── handle_message_ready() ──▶ llm.py ──▶ Gemini (Vertex AI)
```

Again, the tool-branch lines below keep their existing columns.

Then in the file table at `HANDOFF.md:66-74`, replace this row:

```
| `app.py` | All TAC wiring: channels, the LLM loop, the three tools, the outbound call |
```

with these two rows:

```
| `app.py` | All TAC wiring: channels, prompts, the three tools, the outbound call |
| `llm.py` | All Gemini: lazy Vertex client, `TACTool`→`FunctionDeclaration`, the tool loop |
```

- [ ] **Step 7: `HANDOFF.md` — the upstream-bug item**

Replace:

```
2. **Two upstream bug reports, drafted but not filed** — in `KNOWN-ISSUES.md`
   #1 (Pydantic serialization in `to_openai_agents_sdk_tool()`) and #17 (Studio
   handoff unusable on outbound calls). Both are worth sending to
   `twilio/twilio-agent-connect-python`.
```

with:

```
2. **Two upstream bug reports, drafted but not filed** — in `KNOWN-ISSUES.md`
   #1 (Pydantic serialization in `to_openai_agents_sdk_tool()`) and #17 (Studio
   handoff unusable on outbound calls). Both are worth sending to
   `twilio/twilio-agent-connect-python`. #1 no longer affects this demo — the
   Gemini path never calls that method — but the upstream bug is unfixed.
```

- [ ] **Step 8: `KNOWN-ISSUES.md` — retire #1's workaround, keep the report**

In issue #1, replace the **Applied workaround** paragraph:

```
**Applied workaround** (`app.py`, `get_knowledge_tool`): the SDK tool is bound to a
local `search`, and a thin wrapper returns `[chunk.model_dump() for chunk in
chunks]`, rebuilt with `create_tool(...)` reusing `search.params_json_schema` so
the LLM-facing parameter stays exactly `query`. **Delete the wrapper once this is
fixed upstream.**
```

with:

```
**No longer reachable here (2026-08-12).** The demo moved off the OpenAI Agents
SDK to Gemini on Vertex, so `to_openai_agents_sdk_tool()` is never called. The
`app.py` wrapper is deleted; `llm._as_response` flattens Pydantic for every tool
instead, duck-typed so plain dicts pass through unchanged. The upstream bug is
still real and the report below still stands.
```

- [ ] **Step 9: `KNOWN-ISSUES.md` — add the deploy-credential follow-up**

Add under `## Open`:

```
### 19. The twl deploy has no Vertex credential
**Open. Out of scope for the Gemini migration; local + ngrok are unaffected.**

`twl env set` injects environment variables only — no file secrets, no volume
mounts — and `.dockerignore` deliberately keeps credentials out of the build
context. ADC is a file on the laptop, so the container cannot authenticate to
Vertex.

**Likely fix:** a service-account JSON as a base64 env var, decoded to
`service_account.Credentials.from_service_account_info(...)` and passed as
`genai.Client(credentials=...)`. Costs one long-lived secret, which is why it
wasn't done blind.
```

- [ ] **Step 10: Reconcile CLAUDE.md's verification claims with what actually ran**

CLAUDE.md currently asserts "**Verified end to end on real traffic**" and "No leg
of the demo is unexercised any more." Those claims were earned by the OpenAI
build. Whether they still hold depends on whether Task 3 Step 9 ran.

- If the live call **did** run and every leg passed, add "Re-verified on Gemini
  2026-08-12" to that section and leave the claims standing.
- If it **did not** run, the claims are now false. Replace "No leg of the demo is
  unexercised any more." with:

```
The Gemini runtime is verified against real Vertex (tool loop, both declaration
shapes, history replay) but **not yet on a live call** — the voice, SMS and
handoff legs were last exercised on the OpenAI build.
```

Do not skip this step. An overclaiming CLAUDE.md is how the next person wastes a
day trusting something that was never run.

- [ ] **Step 11: Check for stale OpenAI references**

```bash
grep -rn -i "openai" --include='*.md' --include='*.py' --include='*.toml' . | grep -v '^\./\.venv' | grep -v uv.lock | grep -v docs/superpowers
```

Expected: only KNOWN-ISSUES #1's historical record and HANDOFF's pointer to it.
Any hit in `README.md`, `CLAUDE.md`, `app.py`, or `pyproject.toml` is a miss —
fix it.

- [ ] **Step 12: Commit**

```bash
git add CLAUDE.md README.md HANDOFF.md KNOWN-ISSUES.md
git commit -m "Update docs for the Gemini/Vertex runtime"
```

---

## Do not do

- Do not add a `tests/` directory or pytest. The harness in `/tmp` is the test.
- Do not add retries, backoff, provider fallback, or an LLM abstraction layer.
- Do not `twl deploy` this branch (KNOWN-ISSUES #19).
- Do not merge to `main` without being asked.
- Do not "fix" a moved TAC revision in `uv.lock` — report it.
- Do not place a call or send an SMS without explicit permission.
