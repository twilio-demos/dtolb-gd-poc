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
        event_type: Tag static/index.html styles the feed line by — call_status,
            caller_said, agent_said, tool, sms_sent, link_clicked, handoff.
        text: The line to show in the feed.
        **data: Extra JSON fields for the page, e.g. call_sid= or link=.
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
    """Feed one browser's SSE connection, a ``data:`` frame per published event."""
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    _subscribers.append(queue)
    try:
        while True:
            event = await queue.get()
            yield f"data: {json.dumps(event)}\n\n"
    finally:
        _subscribers.remove(queue)
