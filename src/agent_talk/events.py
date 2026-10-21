"""Fan-out of live events to connected clients.

Each subscriber gets a bounded queue. A client that falls too far behind is
dropped (it reconnects and reloads state) rather than slowing the agents down.
"""
from __future__ import annotations

import asyncio
import logging

log = logging.getLogger(__name__)

QUEUE_SIZE = 1000


class Subscriber:
    def __init__(self):
        self.queue: asyncio.Queue = asyncio.Queue(QUEUE_SIZE)
        self.dropped = asyncio.Event()

    async def next(self) -> dict | None:
        """The next event, or None once this subscriber has been dropped."""
        if self.dropped.is_set():
            return None
        get = asyncio.ensure_future(self.queue.get())
        dropped = asyncio.ensure_future(self.dropped.wait())
        done, _ = await asyncio.wait({get, dropped}, return_when=asyncio.FIRST_COMPLETED)
        dropped.cancel()
        if get in done:
            return get.result()
        get.cancel()
        return None


class EventBus:
    def __init__(self):
        self.subscribers: set[Subscriber] = set()

    async def publish(self, event: dict):
        for subscriber in list(self.subscribers):
            try:
                subscriber.queue.put_nowait(event)
            except asyncio.QueueFull:
                log.warning("dropping a slow event subscriber")
                self.unsubscribe(subscriber)
                subscriber.dropped.set()

    def subscribe(self) -> Subscriber:
        subscriber = Subscriber()
        self.subscribers.add(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: Subscriber):
        self.subscribers.discard(subscriber)
