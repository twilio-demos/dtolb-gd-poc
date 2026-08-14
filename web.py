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

# The Twilio Client identity the landing page registers as, and /handoff dials.
BROWSER_AGENT_IDENTITY = "browser-agent"

# link_id -> {"to": phone, "clicked": bool}
PAYMENT_LINKS: dict[str, dict[str, Any]] = {}


class CallRequest(BaseModel):
    phone: str


def create_router(
    start_call: Callable[[str], Awaitable[str]],
    tac_config: Any,
) -> APIRouter:
    router = APIRouter()

    @router.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @router.post("/api/call")
    async def trigger_call(body: CallRequest) -> dict[str, str]:
        """Place the reminder call to the number the page submitted.

        The route has no auth and every call is real and billed, so
        DEMO_ALLOWED_NUMBERS (comma-separated E.164, exact match) caps who can be
        dialed on a public deployment; unset means any number.
        """
        allowed = [n.strip() for n in os.getenv("DEMO_ALLOWED_NUMBERS", "").split(",") if n.strip()]
        if allowed and body.phone.strip() not in allowed:
            raise HTTPException(403, "Number not in DEMO_ALLOWED_NUMBERS.")

        call_sid = await start_call(body.phone)
        return {"call_sid": call_sid}

    @router.get("/events")
    async def sse_stream() -> StreamingResponse:
        return StreamingResponse(events.subscribe(), media_type="text/event-stream")

    @router.get("/token")
    async def voice_token() -> dict[str, str]:
        """Mint the Voice JS access token the landing page registers with.

        incoming_allow is what lets /handoff's <Dial><Client> ring the page.
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

        app.py points default_twiml_options.action_url here. Twilio requests it
        whenever a ConversationRelay session ends, not only on a handoff, so we
        dial only when HandoffData is present — otherwise a dropped websocket
        would ring the browser unprompted. callerId must be a number this account
        owns.
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
        info = PAYMENT_LINKS.get(link_id)
        if info:
            info["clicked"] = True
            events.publish("link_clicked", f"Customer opened the payment link ({info['to']})")
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
