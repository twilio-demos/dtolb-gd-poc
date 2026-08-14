"""
Landing-page routes: trigger the call, watch it live, ring the browser.

Everything here is plain FastAPI — TAC owns the Twilio webhooks; these routes
only serve the demo UI and glue:

  GET  /            landing page (static/index.html)
  POST /api/call    "Call me" button -> app.start_reminder_call()
  GET  /events      SSE stream of everything happening on the call
  GET  /token       access token so the page can register as Twilio Voice
                    JS SDK client "browser-agent"
  POST /handoff     TwiML that transfers the live call to that browser client
  GET  /pay/{id}    the tracked link from the SMS — logs the click, pushes it
                    to the live feed, shows a fake payment page
"""

import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel
from tac.server import build_http_signature_dependency
from twilio.jwt.access_token import AccessToken
from twilio.jwt.access_token.grants import VoiceGrant

import events

STATIC_DIR = Path(__file__).parent / "static"

BROWSER_AGENT_IDENTITY = "browser-agent"


@dataclass
class PaymentLink:
    """A payment link that was texted, and whether the customer opened it."""

    to: str
    clicked: bool = False


PAYMENT_LINKS: dict[str, PaymentLink] = {}


class CallRequest(BaseModel):
    phone: str


def dialing_allowlist() -> list[str]:
    """Read the numbers this deployment is allowed to call.

    Returns:
        Exact-match E.164 numbers from DEMO_ALLOWED_NUMBERS (comma-separated),
        or an empty list, which permits any number. POST /api/call has no auth
        and every call it places is real and billed, so a deployment on a stable
        public URL should set it.
    """
    return [n.strip() for n in os.getenv("DEMO_ALLOWED_NUMBERS", "").split(",") if n.strip()]


def create_router(
    start_call: Callable[[str], Awaitable[str]],
    tac_config: Any,
) -> APIRouter:
    """Build the landing page's routes.

    Args:
        start_call: Places the reminder call and returns its SID — this is
            app.start_reminder_call, passed in because app.py imports this
            module, not the other way round.
        tac_config: TAC's resolved config, read for the account SID, API key
            pair, auth token and phone number the Twilio-facing routes need.

    Returns:
        A router carrying only the demo's UI and glue; TAC owns the webhooks.
    """
    router = APIRouter()

    @router.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        """Serve the landing page, which opens the feed and the softphone."""
        return FileResponse(STATIC_DIR / "index.html")

    @router.post("/api/call")
    async def trigger_call(body: CallRequest) -> dict[str, str]:
        """Place the reminder call to the number the page submitted.

        Args:
            body: The customer's number in E.164.

        Returns:
            The new call's SID.

        Raises:
            HTTPException: 403 when the number is not on the allowlist.
        """
        allowed = dialing_allowlist()
        if allowed and body.phone.strip() not in allowed:
            raise HTTPException(403, "Number not in DEMO_ALLOWED_NUMBERS.")

        call_sid = await start_call(body.phone)
        return {"call_sid": call_sid}

    @router.get("/events")
    async def sse_stream() -> StreamingResponse:
        """Stream the live feed — one events.subscribe() queue per browser."""
        return StreamingResponse(events.subscribe(), media_type="text/event-stream")

    @router.get("/token")
    async def voice_token() -> dict[str, str]:
        """Mint the Voice JS access token the landing page registers with.

        Returns:
            The JWT and the identity it was minted for. incoming_allow is what
            lets /handoff's <Dial><Client> ring the page.
        """
        token = AccessToken(
            tac_config.account_sid,
            tac_config.api_key,
            tac_config.api_secret,
            identity=BROWSER_AGENT_IDENTITY,
        )
        token.add_grant(VoiceGrant(incoming_allow=True))
        return {"token": token.to_jwt(), "identity": BROWSER_AGENT_IDENTITY}

    @router.post(
        "/handoff",
        include_in_schema=False,
        dependencies=[Depends(build_http_signature_dependency(tac_config.auth_token))],
    )
    async def handoff(request: Request) -> Response:
        """Transfer the still-live call to the browser softphone.

        app.py points default_twiml_options.action_url here.

        Args:
            request: Twilio's POST to that action URL. Twilio sends it whenever a
                ConversationRelay session ends, not only on a handoff, so the
                dial happens only when HandoffData is present — otherwise a
                dropped websocket would ring the browser unprompted.

        Returns:
            TwiML dialing BROWSER_AGENT_IDENTITY, or a hangup. callerId must be a
            number this account owns.
        """
        form = await request.form()
        if form.get("HandoffData"):
            events.publish("handoff", "Transferring the call to the browser agent")
            body = (
                f'<Dial callerId="{tac_config.phone_number}" timeout="30">'
                f"<Client>{BROWSER_AGENT_IDENTITY}</Client>"
                f"</Dial>"
            )
        else:
            body = "<Hangup/>"
        return Response(
            content=f'<?xml version="1.0" encoding="UTF-8"?><Response>{body}</Response>',
            media_type="application/xml",
        )

    @router.get("/pay/{link_id}", include_in_schema=False)
    async def payment_link(link_id: str) -> HTMLResponse:
        """Record the click and show the stand-in payment page.

        Args:
            link_id: The id minted by app._mint_tracked_link. An id this process
                never minted still gets the page, just no feed line.

        Returns:
            The demo payment page.
        """
        link = PAYMENT_LINKS.get(link_id)
        if link:
            link.clicked = True
            events.publish("link_clicked", f"Customer opened the payment link ({link.to})")
        return HTMLResponse(
            """
            <html><body style="font-family:sans-serif;text-align:center;padding-top:12vh">
              <h1>&#128095; Owl Shoes</h1>
              <h2>Update payment information</h2>
              <p>This is the demo payment page. In a real app, your payment form goes here.</p>
              <p style="color:green;font-size:1.2em">&#10003; Click was tracked and streamed to the dashboard.</p>
            </body></html>
            """
        )

    return router
