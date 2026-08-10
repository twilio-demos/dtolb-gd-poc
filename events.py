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

_subscribers: list[asyncio.Queue] = []


def publish(event_type: str, text: str, **data: Any) -> None:
    """Push an event to every connected browser.

    event_type: short machine tag ("call_status", "caller_said", "agent_said",
                "tool", "sms_sent", "link_clicked", "handoff", ...)
    text:       human-readable line for the feed
    data:       any extra fields the page might want
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
    """Async generator yielding SSE-formatted lines. One per connected browser."""
    queue: asyncio.Queue = asyncio.Queue()
    _subscribers.append(queue)
    try:
        while True:
            event = await queue.get()
            yield f"data: {json.dumps(event)}\n\n"
    finally:
        _subscribers.remove(queue)
