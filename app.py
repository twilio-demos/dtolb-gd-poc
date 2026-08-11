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
"""

import os
import uuid
from typing import Annotated, Any

from agents import Agent, Runner, set_tracing_disabled
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
from tac.tools import InjectedToolArg, TACTool, create_tool, function_tool
from tac.tools.handoff import create_studio_handoff_tool
from tac.tools.knowledge import create_knowledge_tool

import events
import web

# override=True: an exported TWILIO_API_KEY would otherwise shadow .env.
load_dotenv(override=True)
set_tracing_disabled(True)

tac = TAC(config=TACConfig.from_env())

voice_channel = VoiceChannel(
    tac,
    config=VoiceChannelConfig(
        default_twiml_options=TwiMLOptions(
            # Spoken on answer, before any LLM round-trip: our AI disclosure.
            welcome_greeting=(
                "Hello! This is Ava, an automated A I assistant calling from "
                "Owl Shoes. I'm reaching out with a quick reminder to update "
                "the payment information on your account. Is now an okay time?"
            ),
            # Where the still-live call goes when ConversationRelay ends. TAC
            # would default to the Studio flow webhook, but Studio rejects
            # outbound-api calls, so we render the transfer TwiML ourselves.
            # An explicit action_url outranks studio_handoff_flow_sid.
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


# --- Tools -----------------------------------------------------------------
# function_tool() derives the LLM-facing schema from the signature and
# docstring; InjectedToolArg params are hidden from the LLM and supplied by us.


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

    # Tracked link: it points back here, so the click reaches the live feed
    # before the payment page renders.
    link_id = uuid.uuid4().hex[:8]
    web.PAYMENT_LINKS[link_id] = {"to": to_number, "clicked": False}
    link = f"https://{tac.config.voice_public_domain}/pay/{link_id}"

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


def create_send_payment_link_tool(session: ConversationSession) -> TACTool:
    """Build a send_payment_link tool bound to one conversation.

    A factory rather than a module-level tool: configure_injection() mutates in
    place and returns self, so a shared instance would leak one caller's session
    into another's turn.

    name= is explicit because function_tool would otherwise use the underscored
    function name, which no longer matches VOICE_INSTRUCTIONS.
    """
    return function_tool(name="send_payment_link")(_send_payment_link).configure_injection(
        session=session
    )


# Built once and cached; the knowledge tool takes no per-conversation session.
#
# The wrapper works around an upstream SDK bug: search returns Pydantic models,
# but to_openai_agents_sdk_tool()'s on_invoke encodes results with a bare
# json.dumps(), raising TypeError that the voice channel only logs — the caller
# hears dead air. Reusing search.params_json_schema keeps the LLM-facing
# parameter exactly `query`. Remove once on_invoke handles Pydantic.
_knowledge_tool: TACTool | None = None


async def get_knowledge_tool() -> TACTool | None:
    global _knowledge_tool
    if _knowledge_tool is None and tac.knowledge_client and tac.config.knowledge_base_id:
        search = await create_knowledge_tool(
            knowledge_client=tac.knowledge_client,
            knowledge_base_id=tac.config.knowledge_base_id,
            name="search_renewal_faq",
            description=(
                "Search Owl Shoes' renewal and billing FAQ. "
                "The input MUST be a question in the form of a string."
            ),
            top_k=3,
        )

        async def search_renewal_faq(query: str) -> list[dict[str, Any]]:
            chunks = await search(query=query)
            return [chunk.model_dump() for chunk in chunks]

        _knowledge_tool = create_tool(
            name=search.name,
            description=search.description,
            params_json_schema=search.params_json_schema,
            implementation=search_renewal_faq,
        )
    return _knowledge_tool


def get_handoff_tool(context: ConversationSession) -> TACTool | None:
    """Build the handoff tool, or None if handoff isn't configured.

    create_studio_handoff_tool raises without a flow SID, Conversation
    Orchestrator and a memory store. The voice channel only logs exceptions from
    the message callback, so letting it raise means dead air on every utterance;
    degrading to "no handoff tool" keeps the rest of the demo working.
    """
    if not tac.config.studio_handoff_flow_sid:
        return None
    try:
        return create_studio_handoff_tool(
            tac,
            context,
            attributes={"department": "billing"},
            name="connect_to_human_agent",
            description=(
                "Transfer this call to a live human agent. Use when the "
                "customer asks for a person or you cannot resolve their issue."
            ),
        )
    except ValueError as exc:
        print(f"Human handoff disabled: {exc}")
        return None


# --- LLM loop --------------------------------------------------------------
# TAC calls this with each transcribed utterance (voice) or inbound SMS body;
# the string we return is spoken or texted back.

conversation_history: dict[str, list[Any]] = {}


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

    tools: list[Any] = []
    if on_voice:
        # Voice only: an SMS replier already has the link, and handoff has no
        # messaging path.
        tools.append(create_send_payment_link_tool(context).to_openai_agents_sdk_tool())
        handoff_tool = get_handoff_tool(context)
        if handoff_tool:
            tools.append(handoff_tool.to_openai_agents_sdk_tool())
    knowledge_tool = await get_knowledge_tool()
    if knowledge_tool:
        tools.append(knowledge_tool.to_openai_agents_sdk_tool())

    agent = Agent(
        name="Ava",
        instructions=VOICE_INSTRUCTIONS if on_voice else SMS_INSTRUCTIONS,
        tools=tools,
    )

    history = conversation_history.get(context.conversation_id, [])
    result = await Runner.run(agent, history + [{"role": "user", "content": user_message}])
    conversation_history[context.conversation_id] = result.to_input_list()

    for item in result.new_items:
        if item.type == "tool_call_item":
            tool_name = getattr(item.raw_item, "name", "tool")
            events.publish("tool", f"Agent used tool: {tool_name}")
            if tool_name == "connect_to_human_agent":
                events.publish("handoff", "Transferring to a human — your browser will ring!")

    reply = result.final_output_as(str)
    events.publish("agent_said", f"Ava: {reply}")
    return reply


tac.on_message_ready(handle_message_ready)


# Registering this handler is also what makes TAC attach the status callback URL
# to the outbound call.
async def on_call_status(event: CallStatusEvent) -> None:
    events.publish("call_status", f"Call {event.call_status}", call_sid=event.call_sid)


voice_channel.on_call_status(on_call_status)


async def start_reminder_call(to_number: str) -> str:
    """Place the reminder call. Triggered by POST /api/call."""
    result = await voice_channel.initiate_outbound_conversation(
        InitiateVoiceConversationOptions(
            to=to_number,
            call_options=CallOptions(
                # Without this Twilio only reports "completed".
                status_callback_event=["initiated", "ringing", "answered", "completed"],
                timeout=30,
            ),
        )
    )
    events.publish("call_status", f"Placing call to {to_number}", call_sid=result.call_sid)
    return result.call_sid


# --- Server ----------------------------------------------------------------

app = FastAPI(title="TAC Payment Reminder Demo")
app.include_router(web.create_router(start_reminder_call, tac.config))


class TrustProxyHTTPS:
    """Force ``X-Forwarded-Proto: https`` on HTTP and WebSocket scopes.

    Twilio signs the full request URL and TAC validates that signature using
    ``X-Forwarded-Proto``, preferring it over the ASGI scheme. A proxy that
    terminates TLS and forwards plain HTTP sends ``http``, so validation runs
    against the wrong URL and every Twilio callback 403s.

    Enable with ``TRUST_PROXY_HTTPS=1`` behind such a proxy; leave it unset
    under ngrok, which forwards the header correctly. Pure ASGI because
    ``@app.middleware("http")`` never sees ``websocket`` scopes, and the
    ConversationRelay socket is validated the same way.
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
