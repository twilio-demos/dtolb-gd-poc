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
            if candidate is None or not candidate.parts:
                # Safety block, no candidate, or a part-less one from a MAX_TOKENS
                # or RECITATION stop. Appending any of those corrupts the history
                # for every later turn.
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

    return _FALLBACK_REPLY, history
