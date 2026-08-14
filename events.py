"""
Tiny in-memory SSE hub for the landing page's live event feed.

Demo-grade on purpose: every connected browser gets its own asyncio.Queue,
publish() fans out to all of them, nothing is persisted. If the page reloads
it starts from a blank feed.
"""

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

_subscribers: list[asyncio.Queue[dict[str, Any]]] = []


def publish(event_type: str, text: str, **data: Any) -> None:
    """Push an event to every connected browser.

    Args:
        event_type: The machine tag static/index.html styles the feed line by:
            "call_status", "caller_said", "agent_said", "tool", "sms_sent",
            "link_clicked" or "handoff".
        text: The line to show in the feed.
        **data: Any extra JSON fields the page might want, e.g. call_sid= or
            link=.
    """
    event = {
        "type": event_type,
        "text": text,
        "time": datetime.now().strftime("%H:%M:%S"),
        **data,
    }
    for queue in _subscribers:
        queue.put_nowait(event)


async def subscribe() -> AsyncIterator[str]:
    """Feed one browser's SSE connection.

    Yields:
        Each published event as an SSE ``data:`` frame. The queue joins the fan-out
        on the first iteration and leaves it when the browser disconnects.
    """
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    _subscribers.append(queue)
    try:
        while True:
            event = await queue.get()
            yield f"data: {json.dumps(event)}\n\n"
    finally:
        _subscribers.remove(queue)
