"""
Landing-page routes: trigger the call, watch it live, ring the browser.

Everything here is plain FastAPI — TAC owns the Twilio webhooks; these routes
only serve the demo UI and glue:

  GET  /            landing page (static/index.html)
  POST /api/call    "Call me" button -> app.start_reminder_call()
  GET  /events      SSE stream of everything happening on the call
  GET  /token       access token so the page can register as Twilio Voice
                    JS SDK client "browser-agent" (what the Studio flow dials)
  GET  /pay/{id}    the "branded/tracked" link from the SMS — logs the click,
                    pushes it to the live feed, shows a fake payment page
"""

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel
from twilio.jwt.access_token import AccessToken
from twilio.jwt.access_token.grants import VoiceGrant

import events

STATIC_DIR = Path(__file__).parent / "static"

# The Twilio Client identity the landing page registers as, and the Studio
# flow dials. Keep in sync with studio-flow.json.
BROWSER_AGENT_IDENTITY = "browser-agent"

# link_id -> {"to": phone, "clicked": bool}; filled in by the
# send_payment_link tool in app.py.
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
        call_sid = await start_call(body.phone)
        return {"call_sid": call_sid}

    @router.get("/events")
    async def sse_stream() -> StreamingResponse:
        return StreamingResponse(events.subscribe(), media_type="text/event-stream")

    @router.get("/token")
    async def voice_token() -> dict[str, str]:
        # Standard Twilio Voice JS SDK access token. incoming_allow is what
        # lets the Studio flow's "Connect Call To > Client" ring this browser.
        token = AccessToken(
            tac_config.account_sid,
            tac_config.api_key,
            tac_config.api_secret,
            identity=BROWSER_AGENT_IDENTITY,
        )
        token.add_grant(VoiceGrant(incoming_allow=True))
        return {"token": token.to_jwt(), "identity": BROWSER_AGENT_IDENTITY}

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
