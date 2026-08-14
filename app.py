"""
TAC payment-reminder demo — main app.

An AI agent places an outbound call reminding the customer to update their
payment information. On the call the customer can:
  - ask for the payment link      -> custom TAC tool sends an SMS with a tracked link
  - ask renewal questions         -> TAC's built-in knowledge tool (Enterprise Knowledge)
  - ask for a human               -> TAC's built-in handoff tool; /handoff then dials
                                     the browser softphone (Voice JS client "browser-agent")

Everything Twilio goes through the TAC SDK. The landing page at / watches the
whole call live over SSE.

Run:  uv run python app.py   (see README for the one-time Twilio setup)

Why bits of this look the way they do: KNOWN-ISSUES.md, "SDK behavior the code is
shaped around".
"""

import os
import uuid
from typing import Annotated, Any, Final

from dotenv import load_dotenv
from fastapi import FastAPI

from tac import TAC, TACConfig
from tac.channels.sms import SMSChannel, SMSChannelConfig
from tac.channels.voice import CallStatusEvent, VoiceChannel, VoiceChannelConfig
from tac.models.outbound import (
    CallOptions,
    InitiateMessagingConversationOptions,
    InitiateVoiceConversationOptions,
)
from tac.models.session import ConversationSession
from tac.models.tac import TACMemoryResponse
from tac.models.voice import TwiMLOptions
from tac.server import TACFastAPIServer
from agents import Agent, Runner, set_tracing_disabled
from agents.extensions.models.litellm_model import LitellmModel

from tac.tools import InjectedToolArg, TACTool, create_tool, function_tool
from tac.tools.handoff import create_studio_handoff_tool
from tac.tools.knowledge import create_knowledge_tool

import events
import web

# override=True: an exported TWILIO_API_KEY would otherwise shadow .env.
load_dotenv(override=True)

# Tracing would try to upload to OpenAI, which we are not talking to.
set_tracing_disabled(True)

# LiteLLM routes vertex_ai/* through ADC, so auth is gcloud, not a key.
GEMINI_MODEL: Final = os.getenv("GEMINI_MODEL", "vertex_ai/gemini-2.5-flash")

# The model-facing tool names. VOICE_INSTRUCTIONS names all three verbatim.
PAYMENT_LINK_TOOL: Final = "send_payment_link"
RENEWAL_FAQ_TOOL: Final = "search_renewal_faq"
HUMAN_HANDOFF_TOOL: Final = "connect_to_human_agent"

VOICE_CHANNEL: Final = "VOICE"

tac = TAC(config=TACConfig.from_env())

voice_channel = VoiceChannel(
    tac,
    config=VoiceChannelConfig(
        default_twiml_options=TwiMLOptions(
            welcome_greeting=(
                "Hello! This is Ava, an automated A I assistant calling from "
                "Owl Shoes. I'm reaching out with a quick reminder to update "
                "the payment information on your account. Is now an okay time?"
            ),
            action_url=f"https://{tac.config.voice_public_domain}/handoff",
        ),
    ),
)

sms_channel = SMSChannel(tac, config=SMSChannelConfig())


# Consent wording is deliberately narrow. The greeting ends with "is now an okay
# time?", so a looser rule ("if the customer agrees") fires the tool on the
# caller's first "yes".
VOICE_INSTRUCTIONS = (
    "You are Ava, an AI assistant for Owl Shoes, on an outbound phone call. "
    "The greeting already introduced you and asked whether now is a good time. "
    "You are calling because the payment method on their subscription needs "
    "updating before it renews.\n"
    "Style: one or two short sentences per turn, plain spoken text. No "
    "markdown, bullets, or emojis. Never read a URL aloud — the link goes by "
    "text message.\n"
    "How to handle the call:\n"
    "- If they say it's a good time, briefly say their saved card needs "
    "updating before the next renewal, then ASK whether they'd like a secure "
    "link by text.\n"
    "- Only use send_payment_link after they have clearly agreed to receive a "
    "text or have asked for the link. A plain 'yes', 'sure' or 'okay' in reply "
    "to 'is now a good time' is NOT permission to send it — offer the link "
    "first and wait for an answer. Being thanked is not permission either.\n"
    "- Send the link at most once per call. After sending, say it's on its way, "
    "then wrap up politely.\n"
    "- For anything about renewal dates, pricing, billing, refunds, "
    "cancellation or member benefits, answer using the search_renewal_faq "
    "tool. Never guess at policy or invent amounts or dates.\n"
    "- If they ask for a person, get frustrated, or you cannot help, use "
    "connect_to_human_agent and tell them you're transferring them now.\n"
    "- If it's a bad time, apologize, mention they can update the card anytime "
    "in their account, and end the call politely without sending anything."
)

# An SMS reply arrives on a new conversation_id with empty history, so it must
# not inherit the outbound-call prompt.
SMS_INSTRUCTIONS = (
    "You are Ava, an AI assistant for Owl Shoes, replying by text message to a "
    "customer who was just sent a link to update their payment information. "
    "Keep replies to one or two short sentences, plain text only. They already "
    "have the link, so do not send another one. If they ask about renewals, "
    "billing dates, refunds, or their plan, answer using the search_renewal_faq "
    "tool."
)


def _mint_tracked_link(to_number: str) -> str:
    """Register a payment URL whose click the live feed can attribute.

    Args:
        to_number: The customer about to be texted; /pay/{link_id} names them.
    """
    link_id = uuid.uuid4().hex[:8]
    web.PAYMENT_LINKS[link_id] = web.PaymentLink(to=to_number)
    return f"https://{tac.config.voice_public_domain}/pay/{link_id}"


async def _send_payment_link(
    session: Annotated[ConversationSession, InjectedToolArg],
) -> str:
    """Text the customer a secure link to update their payment information.

    Use this once the customer agrees to receive the link. Returns a short
    status message you can relay to the customer.
    """
    to_number = session.author_info.address if session.author_info else None
    if not to_number:
        return "Could not determine the customer's phone number."

    link = _mint_tracked_link(to_number)
    await sms_channel.initiate_outbound_conversation(
        InitiateMessagingConversationOptions(
            to=to_number,
            message=(
                "Owl Shoes: update your payment information here "
                f"{link} — thanks for keeping your account current!"
            ),
        )
    )

    events.publish("sms_sent", f"SMS with payment link sent to {to_number}", link=link)
    return "Payment link sent by text message."


def payment_link_tool_for(session: ConversationSession) -> TACTool:
    """Build this conversation's own copy of the send_payment_link tool.

    Args:
        session: The conversation injected into _send_payment_link.
    """
    tool = function_tool(name=PAYMENT_LINK_TOOL)(_send_payment_link)
    return tool.configure_injection(session=session)


_shared_knowledge_tool: TACTool | None = None


async def knowledge_tool() -> TACTool | None:
    """Build the shared renewal-FAQ tool on first use; None without a knowledge base.

    The search result is re-wrapped because to_openai_agents_sdk_tool() JSON-encodes
    it with a bare json.dumps(), which raises on the Pydantic chunks the built-in
    tool returns — the caller then hears dead air. Reusing search.params_json_schema
    keeps the model-facing parameter exactly `query`. KNOWN-ISSUES #1.
    """
    global _shared_knowledge_tool
    if _shared_knowledge_tool is None and tac.knowledge_client and tac.config.knowledge_base_id:
        search = await create_knowledge_tool(
            knowledge_client=tac.knowledge_client,
            knowledge_base_id=tac.config.knowledge_base_id,
            name=RENEWAL_FAQ_TOOL,
            description=(
                "Search Owl Shoes' renewal and billing FAQ. "
                "The input MUST be a question in the form of a string."
            ),
            top_k=3,
        )

        async def search_renewal_faq(query: str) -> list[dict[str, Any]]:
            return [chunk.model_dump() for chunk in await search(query=query)]

        _shared_knowledge_tool = create_tool(
            name=search.name,
            description=search.description,
            params_json_schema=search.params_json_schema,
            implementation=search_renewal_faq,
        )
    return _shared_knowledge_tool


def handoff_tool_for(session: ConversationSession) -> TACTool | None:
    """Build this conversation's own copy of the connect_to_human_agent tool.

    Args:
        session: The conversation Studio is being asked to take over.
    """
    try:
        return create_studio_handoff_tool(
            tac,
            session,
            attributes={"department": "billing"},
            name=HUMAN_HANDOFF_TOOL,
            description=(
                "Transfer this call to a live human agent. Use when the "
                "customer asks for a person or you cannot resolve their issue."
            ),
        )
    except ValueError as exc:
        print(f"Human handoff disabled: {exc}")
        return None


async def tools_for(session: ConversationSession) -> list[TACTool]:
    """Assemble the tools the agent may call, skipping anything unprovisioned.

    Args:
        session: The conversation being served; its channel decides the set.
    """
    voice_only = (
        [payment_link_tool_for(session), handoff_tool_for(session)]
        if session.channel == VOICE_CHANNEL
        else []
    )
    return [tool for tool in (*voice_only, await knowledge_tool()) if tool]


def publish_tool_call(tool_name: str) -> None:
    """Announce a tool call on the live feed.

    Args:
        tool_name: The tool the agent invoked; the handoff, which is the moment
            the softphone is about to ring, gets a second louder line.
    """
    events.publish("tool", f"Agent used tool: {tool_name}")
    if tool_name == HUMAN_HANDOFF_TOOL:
        events.publish("handoff", "Transferring to a human — your browser will ring!")


conversation_history: dict[str, list[Any]] = {}


async def handle_message_ready(
    user_message: str,
    session: ConversationSession,
    _memory_response: TACMemoryResponse | None,
) -> str:
    """Answer one transcribed utterance or one inbound SMS.

    Args:
        user_message: What the customer just said or texted.
        session: The conversation it arrived on; picks the prompt and the tools.
        _memory_response: Always None under memory_mode="never".
    """
    events.publish("caller_said", f"Customer: {user_message}")

    on_voice = session.channel == VOICE_CHANNEL
    agent = Agent(
        name="Ava",
        instructions=VOICE_INSTRUCTIONS if on_voice else SMS_INSTRUCTIONS,
        tools=[tool.to_openai_agents_sdk_tool() for tool in await tools_for(session)],
        model=LitellmModel(model=GEMINI_MODEL),
    )

    history = conversation_history.get(session.conversation_id, [])
    result = await Runner.run(agent, [*history, {"role": "user", "content": user_message}])
    conversation_history[session.conversation_id] = result.to_input_list()

    for item in result.new_items:
        if item.type == "tool_call_item":
            publish_tool_call(getattr(item.raw_item, "name", "tool"))

    reply = result.final_output_as(str)
    events.publish("agent_said", f"Ava: {reply}")
    return reply


tac.on_message_ready(handle_message_ready)


async def on_call_status(event: CallStatusEvent) -> None:
    """Publish each call-status webhook to the live feed.

    Args:
        event: Twilio's status callback, as parsed by the voice channel.
    """
    events.publish("call_status", f"Call {event.call_status}", call_sid=event.call_sid)


voice_channel.on_call_status(on_call_status)


async def start_reminder_call(to_number: str) -> str:
    """Place the reminder call, triggered by POST /api/call.

    Args:
        to_number: The customer's number in E.164.
    """
    result = await voice_channel.initiate_outbound_conversation(
        InitiateVoiceConversationOptions(
            to=to_number,
            call_options=CallOptions(
                status_callback_event=["initiated", "ringing", "answered", "completed"],
                timeout=30,
            ),
        )
    )
    events.publish("call_status", f"Placing call to {to_number}", call_sid=result.call_sid)
    return result.call_sid


app = FastAPI(title="TAC Payment Reminder Demo")
app.include_router(web.create_router(start_reminder_call, tac.config))


class TrustProxyHTTPS:
    """Force ``X-Forwarded-Proto: https`` on HTTP and WebSocket scopes.

    Twilio signs the full request URL and TAC validates that signature using
    ``X-Forwarded-Proto`` in preference to the ASGI scheme, so behind a proxy that
    terminates TLS and forwards plain HTTP every Twilio callback 403s — webhooks and
    the ConversationRelay websocket alike. Enable with ``TRUST_PROXY_HTTPS=1``; leave
    it unset under ngrok, which forwards the header correctly. Pure ASGI because
    ``@app.middleware("http")`` never sees ``websocket`` scopes.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] in ("http", "websocket"):
            headers = [(k, v) for k, v in scope["headers"] if k != b"x-forwarded-proto"]
            headers.append((b"x-forwarded-proto", b"https"))
            scope = dict(scope, headers=headers)
        await self.app(scope, receive, send)


if os.getenv("TRUST_PROXY_HTTPS") == "1":
    app.add_middleware(TrustProxyHTTPS)

# Constructed at import, not under __main__: the constructor is what registers
# TAC's webhook and websocket routes, so `uvicorn app:app` would otherwise serve
# the landing page with no /twiml and no ConversationRelay socket.
server = TACFastAPIServer(
    tac=tac,
    voice_channel=voice_channel,
    messaging_channels=[sms_channel],
    app=app,
)

if __name__ == "__main__":
    print("Landing page: http://localhost:8000/")
    server.start()
