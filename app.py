"""
TAC payment-reminder demo — main app.

An AI agent places an outbound call reminding the customer to update their
payment information. On the call the customer can:
  - say "thanks, text me the link"  -> custom TAC tool sends an SMS with a tracked link
  - ask renewal questions           -> TAC's built-in knowledge tool (Enterprise Knowledge)
  - say "get me a human"            -> TAC's built-in Studio handoff tool; the Studio
                                       flow dials the browser softphone on the landing
                                       page (Twilio Voice JS SDK identity "browser-agent")

Everything Twilio goes through the TAC SDK. The landing page at / watches the
whole call live over SSE.

Modeled on the TAC repo examples: features/outbound.py, features/handoff.py,
features/voice_call_events.py, features/dashboard/.

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

# override=True so .env wins over anything already exported in your shell.
# Without it a stale exported TWILIO_API_KEY silently shadows .env and every
# Twilio call 401s with a confusing "Authorization Error".
load_dotenv(override=True)
set_tracing_disabled(True)

# ---------------------------------------------------------------------------
# TAC setup — one TAC instance, one channel per Twilio channel we use.
# TACFastAPIServer (bottom of file) auto-wires all webhooks + the
# ConversationRelay websocket from TWILIO_VOICE_PUBLIC_DOMAIN.
# ---------------------------------------------------------------------------

tac = TAC(config=TACConfig.from_env())

voice_channel = VoiceChannel(
    tac,
    config=VoiceChannelConfig(
        # Spoken by Twilio the moment the customer answers — before any LLM
        # round-trip. This is our "you are talking to an AI" disclosure.
        default_twiml_options=TwiMLOptions(
            welcome_greeting=(
                "Hello! This is Ava, an automated A I assistant calling from "
                "Owl Shoes. I'm reaching out with a quick reminder to update "
                "the payment information on your account. Is now an okay time?"
            ),
        ),
    ),
)

# Used by the send_payment_link tool below. Replies to the SMS also route
# back into handle_message_ready, on this same agent.
sms_channel = SMSChannel(tac, config=SMSChannelConfig())


VOICE_INSTRUCTIONS = (
    "You are Ava, an AI assistant for Owl Shoes, on an outbound phone call. "
    "You already introduced yourself in the call greeting: you're calling to "
    "remind the customer to update their payment information before their "
    "subscription renews. "
    "Keep responses short and conversational, one or two sentences. Plain "
    "spoken text only: no markdown, bullets, or emojis. "
    "If the customer agrees, thanks you, or asks for the link, use the "
    "send_payment_link tool to text them a secure update link, confirm it "
    "was sent, then wrap up the call politely. "
    "If they ask about renewals, billing dates, refunds, or their plan, "
    "answer using the search_renewal_faq tool. "
    "If they ask for a human or you cannot help, use the "
    "connect_to_human_agent tool and tell them you are transferring them now."
)

# Replies to the payment-link SMS arrive as a NEW conversation_id with empty
# history, so they must not inherit the outbound-call prompt (it would send a
# second link on "thanks!").
SMS_INSTRUCTIONS = (
    "You are Ava, an AI assistant for Owl Shoes, replying by text message to a "
    "customer who was just sent a link to update their payment information. "
    "Keep replies to one or two short sentences, plain text only. They already "
    "have the link, so do not send another one. If they ask about renewals, "
    "billing dates, refunds, or their plan, answer using the search_renewal_faq "
    "tool."
)


# ---------------------------------------------------------------------------
# Custom TAC tool — this is the pattern for giving your LLM new abilities.
# function_tool() builds the LLM-facing JSON schema from the signature +
# docstring; InjectedToolArg params are hidden from the LLM. It's applied by
# the factory below rather than as a decorator here, so each voice message gets
# its own tool with its own session injected (configure_injection()).
# Voice only: on SMS the customer already has the link (see SMS_INSTRUCTIONS).
# ---------------------------------------------------------------------------


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

    # "Branded/tracked" link: it points back at this server, so we log the
    # click and stream it to the landing page before showing the payment page.
    link_id = uuid.uuid4().hex[:8]
    web.PAYMENT_LINKS[link_id] = {"to": to_number, "clicked": False}
    link = f"https://{tac.config.voice_public_domain}/pay/{link_id}"

    # Outbound SMS through TAC — same SDK, different channel.
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
    """Build a fresh send_payment_link tool bound to one conversation.

    A factory, not a module-level tool: configure_injection() mutates the tool
    in place and returns self, so a shared instance would leak one caller's
    session into another's turn. This mirrors TAC's own
    create_studio_handoff_tool, which rebuilds its tool per call too.

    Pass name= explicitly: function_tool defaults to func.__name__, which is
    now the underscore-prefixed _send_payment_link and would no longer match
    the tool name in VOICE_INSTRUCTIONS.
    """
    return function_tool(name="send_payment_link")(_send_payment_link).configure_injection(
        session=session
    )


# TAC's built-in knowledge tool needs an async factory call, so build it on
# first use and reuse it. Passing name+description skips a metadata fetch.
#
# Workaround for an upstream SDK bug: the search returns Pydantic
# KnowledgeChunkResult models, but to_openai_agents_sdk_tool()'s on_invoke
# JSON-encodes results with a bare json.dumps() — which raises TypeError, and
# the voice channel only logs it, so the caller hears dead air. So we keep the
# SDK tool for the search itself and wrap it in a tool that returns plain
# dicts. Reusing search.params_json_schema keeps the LLM-facing parameter
# exactly `query` (the injected client/kb-id/top_k are already filtered out).
# Delete the wrapper once on_invoke handles Pydantic upstream.
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


# TAC's built-in Studio handoff tool needs TWILIO_STUDIO_HANDOFF_FLOW_SID plus
# Conversation Orchestrator and a memory store; it raises ValueError otherwise.
# The voice channel only logs exceptions from this callback, so an unguarded
# call means dead air on EVERY utterance. Degrade to "no handoff tool" instead
# so the SMS + FAQ parts of the demo still work with a partial .env.
def get_handoff_tool(context: ConversationSession) -> TACTool | None:
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
    except ValueError as exc:  # orchestrator or memory store not configured
        print(f"Human handoff disabled: {exc}")
        return None


# ---------------------------------------------------------------------------
# The LLM loop. TAC calls this with each transcribed caller utterance (voice)
# or inbound SMS body; whatever string we return is spoken/texted back.
# ---------------------------------------------------------------------------

conversation_history: dict[str, list[Any]] = {}


async def handle_message_ready(
    user_message: str,
    context: ConversationSession,
    # _memory_response is always None here: both channels use the default
    # memory_mode="never", and the conversation_history dict below is what
    # actually carries context. To use TAC memory instead, set
    # memory_mode="always" on the channel configs and compose the prompt with
    # tac.adapters.prompt_builder.MemoryPromptBuilder.build(...) — that also
    # gives SMS replies cross-channel context (see issue #5).
    _memory_response: TACMemoryResponse | None,
) -> str:
    events.publish("caller_said", f"Customer: {user_message}")

    # context.channel is "VOICE" or "SMS" — the prompt and the tool set both
    # depend on it. The session-bound tools are built fresh for this message so
    # they carry this conversation's session; the knowledge tool takes no
    # session, so it stays cached.
    on_voice = context.channel == "VOICE"

    tools: list[Any] = []
    if on_voice:
        # Voice only. The customer already has the link if they're replying by
        # SMS, and studio-flow.json has no incomingMessage path, so a digital
        # handoff would POST a Studio execution that dead-ends.
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

    # Surface tool calls on the landing page feed.
    for item in result.new_items:
        if item.type == "tool_call_item":
            tool_name = getattr(item.raw_item, "name", "tool")
            events.publish("tool", f"Agent used tool: {tool_name}")
            if tool_name == "connect_to_human_agent":
                # TAC ends the ConversationRelay session after this reply is
                # spoken; Twilio then hands the live call to the Studio flow,
                # which dials the "browser-agent" client — your browser rings.
                events.publish("handoff", "Transferring to a human — your browser will ring!")

    reply = result.final_output_as(str)
    events.publish("agent_said", f"Ava: {reply}")
    return reply


tac.on_message_ready(handle_message_ready)


# Call lifecycle webhooks (ringing / answered / completed) -> SSE feed.
# Registering the handler is what makes TAC attach the status callback URL
# to the outbound call.
async def on_call_status(event: CallStatusEvent) -> None:
    events.publish("call_status", f"Call {event.call_status}", call_sid=event.call_sid)


voice_channel.on_call_status(on_call_status)


# ---------------------------------------------------------------------------
# Placing the outbound call — triggered by the landing page's "Call me" button
# (POST /api/call in web.py).
# ---------------------------------------------------------------------------


async def start_reminder_call(to_number: str) -> str:
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


# ---------------------------------------------------------------------------
# Server — TACFastAPIServer registers TAC's webhook + websocket routes on our
# FastAPI app; web.py adds the landing page, SSE stream, browser-softphone
# token, and the tracked /pay/<id> link.
# ---------------------------------------------------------------------------

app = FastAPI(title="TAC Payment Reminder Demo")
app.include_router(web.create_router(start_reminder_call, tac.config))


class TrustProxyHTTPS:
    """Force ``X-Forwarded-Proto: https`` on incoming HTTP and WebSocket scopes.

    Twilio signs the full request URL, and TAC validates that signature on
    /twiml, the relay action callback, the call-event callbacks and the /ws
    upgrade. TAC rebuilds the URL from ``X-Forwarded-Proto`` (see
    tac/server/signature_validation.py ``_build_url``), preferring that header
    over the ASGI scheme.

    Some proxy chains terminate TLS and then forward over plain HTTP, arriving
    with ``X-Forwarded-Proto: http``. TAC then validates against
    ``http://your-domain/...`` while Twilio signed ``https://...``, and every
    callback 403s. Observed on the twl dev box, where Caddy terminates TLS and
    Traefik overwrites the header: four consecutive
    ``POST /twilio/call-events/status -> 403 Forbidden``.

    Enable with ``TRUST_PROXY_HTTPS=1`` when the app sits behind a TLS-
    terminating proxy. Not needed under ngrok, which forwards the header
    correctly. Pure ASGI rather than a FastAPI ``@app.middleware("http")``
    because that flavor never sees ``websocket`` scopes — and the
    ConversationRelay socket needs this just as much as the webhooks.
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

# TACFastAPIServer registers TAC's webhook + websocket routes in its
# constructor, so it must run at import time — `uvicorn app:app --reload`
# never executes the __main__ block and would serve the landing page with no
# /twiml route or ConversationRelay websocket. Constructing it here also means
# a missing TWILIO_VOICE_PUBLIC_DOMAIN fails fast at import instead of on the
# first inbound call. start() (uvicorn, using TWILIO_SERVER_HOST/PORT) stays
# under the guard; under `uvicorn app:app` host/port come from uvicorn's own
# CLI instead.
server = TACFastAPIServer(
    tac=tac,
    voice_channel=voice_channel,
    messaging_channels=[sms_channel],
    app=app,
)

if __name__ == "__main__":
    print("Landing page: http://localhost:8000/")
    server.start()
