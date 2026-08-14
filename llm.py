"""
Gemini (Vertex AI) runtime for the demo's LLM turns.

TAC ships no Gemini adapter, so this module *is* the bridge: a TACTool's
params_json_schema becomes a Gemini FunctionDeclaration, and a function_call
coming back is dispatched through ``await tool(**args)``, which is also what
applies the tool's injected arguments. Everything else here is the turn loop.

Knows nothing about Twilio on purpose — run_turn takes an on_tool_call callback
so the caller publishes its own events.

Why bits of this look the way they do: KNOWN-ISSUES.md, "SDK behavior the code is
shaped around". run_turn's history handling has a known wart — see
docs/COMPLEXITY-NOTES.md.
"""

import os
from collections.abc import Callable
from typing import Final

from google import genai
from google.genai import types
from pydantic import BaseModel

from tac.tools import TACTool

History = list[types.Content]

# A model that only ever calls tools never speaks, and the voice channel gives
# the caller silence while it waits.
MAX_TOOL_ROUNDS: Final = 5

_FALLBACK_REPLY = "Sorry, I'm having trouble with that right now."

_client: genai.Client | None = None


def _get_client() -> genai.Client:
    """Return the process-wide Vertex client, building it on first use."""
    global _client
    if _client is None:
        _client = genai.Client(
            vertexai=True,
            project=os.environ["GOOGLE_CLOUD_PROJECT"],
            location=os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1"),
        )
    return _client


def _model_facing_parameters(tool: TACTool) -> dict[str, object] | None:
    """Return the parameter schema Gemini should see for one tool.

    Args:
        tool: The tool being declared; an all-injected one declares no parameters.
    """
    schema = tool.params_json_schema
    return schema if schema.get("properties") else None


def _declare(tools: list[TACTool]) -> types.Tool:
    """Convert TAC tools into one Gemini tool declaration.

    Args:
        tools: The tools this turn may call.
    """
    return types.Tool(
        function_declarations=[
            types.FunctionDeclaration(
                name=tool.name,
                description=tool.description,
                parameters_json_schema=_model_facing_parameters(tool),
            )
            for tool in tools
        ]
    )


def _jsonable(value: object) -> object:
    """Flatten a Pydantic tool result; anything else passes through untouched.

    Args:
        value: A tool's return value, or one element of it.
    """
    return value.model_dump() if isinstance(value, BaseModel) else value


def _as_response(result: object) -> dict[str, object]:
    """Wrap a tool's return value for types.Part.from_function_response.

    Args:
        result: Whatever the TACTool returned.
    """
    if isinstance(result, list):
        return {"result": [_jsonable(item) for item in result]}
    return {"result": _jsonable(result)}


def _user_turn(text: str) -> types.Content:
    """Wrap a customer utterance as one conversation turn.

    Args:
        text: What the customer said or texted.
    """
    return types.Content(role="user", parts=[types.Part.from_text(text=text)])


async def _generate(
    contents: History, instructions: str, tools: list[TACTool]
) -> types.GenerateContentResponse:
    """Ask Gemini for the next turn.

    Args:
        contents: The conversation so far, ending with the turn to answer.
        instructions: The system instruction for this channel.
        tools: The tools the model may call.
    """
    return await _get_client().aio.models.generate_content(
        model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=instructions,
            tools=[_declare(tools)] if tools else None,
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        ),
    )


def _model_turn(response: types.GenerateContentResponse) -> types.Content | None:
    """Return the model's turn, or None when it produced nothing usable.

    Args:
        response: What _generate returned.
    """
    candidate = response.candidates[0].content if response.candidates else None
    return candidate if candidate and candidate.parts else None


async def _tool_results(
    response: types.GenerateContentResponse,
    tools: list[TACTool],
    on_tool_call: Callable[[str], None],
) -> types.Content:
    """Run every tool the model asked for and package the results as one turn.

    Args:
        response: The model turn carrying the function calls.
        tools: The tools this turn may call.
        on_tool_call: Notified with each tool name as it is invoked.
    """
    by_name = {tool.name: tool for tool in tools}
    parts: list[types.Part] = []
    for call in response.function_calls or []:
        on_tool_call(call.name)
        result = await by_name[call.name](**(call.args or {}))
        parts.append(
            types.Part.from_function_response(name=call.name, response=_as_response(result))
        )
    return types.Content(role="user", parts=parts)


async def run_turn(
    *,
    user_message: str,
    history: History,
    instructions: str,
    tools: list[TACTool],
    on_tool_call: Callable[[str], None],
) -> tuple[str, History]:
    """Run one turn to completion, executing whatever tools the model calls.

    Args:
        user_message: What the customer just said or texted.
        history: Contents from earlier turns of this conversation.
        instructions: The system instruction for this channel.
        tools: The tools the model may call.
        on_tool_call: Notified with each tool name as it is invoked.
    """
    gave_up = (_FALLBACK_REPLY, history)
    contents: History = [*history, _user_turn(user_message)]

    try:
        for _ in range(MAX_TOOL_ROUNDS):
            response = await _generate(contents, instructions, tools)

            reply = _model_turn(response)
            if reply is None:
                return gave_up
            contents.append(reply)

            if not response.function_calls:
                return response.text or _FALLBACK_REPLY, contents

            contents.append(await _tool_results(response, tools, on_tool_call))
    except Exception as exc:
        print(f"LLM turn failed: {exc}")
        return gave_up

    return gave_up
