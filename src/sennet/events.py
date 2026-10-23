"""Fan-out of live events to connected clients.

Each subscriber gets a bounded queue. A client that falls too far behind is
dropped (it reconnects and reloads state) rather than slowing the agents down.

Streamed text arrives a few characters at a time, 25 to 40 deltas a second. A
client gets them coalesced (`batches`): one message per item every 50 ms, so a
phone isn't woken for every packet (docs/performance.md, step 3).
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

log = logging.getLogger(__name__)

QUEUE_SIZE = 1000
DELTA_WINDOW_S = 0.05
IDLE: dict = {}  # what `next` returns when its timeout passes first


class Subscriber:
    def __init__(self):
        self.queue: asyncio.Queue = asyncio.Queue(QUEUE_SIZE)
        self.dropped = asyncio.Event()

    async def next(self, timeout: float | None = None) -> dict | None:
        """The next event, None once this subscriber has been dropped, or IDLE if `timeout` passes first."""
        if self.dropped.is_set():
            return None
        get = asyncio.ensure_future(self.queue.get())
        dropped = asyncio.ensure_future(self.dropped.wait())
        try:
            done, _ = await asyncio.wait({get, dropped}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        finally:
            # Also when the caller is cancelled (the client disconnected), so neither is left pending.
            dropped.cancel()
            if not get.done():
                get.cancel()
        if get in done:
            return get.result()
        return None if dropped in done else IDLE


async def batches(subscriber: Subscriber, window: float = DELTA_WINDOW_S) -> AsyncIterator[list[dict]]:
    """A subscriber's events in order, with each item's deltas joined over `window` seconds.

    Any other event first sends the deltas held before it, so nothing overtakes text it follows.
    Ends once the subscriber is dropped."""
    loop = asyncio.get_running_loop()
    held: dict[tuple, dict] = {}  # (task, item) → the deltas so far, joined
    deadline = 0.0
    while True:
        event = await subscriber.next(max(0.0, deadline - loop.time()) if held else None)
        if event is None:
            return
        if event is IDLE:
            yield list(held.values())
            held.clear()
        elif event.get("type") == "delta":
            key = (event.get("task_id"), event.get("item_id"))
            if not held:
                deadline = loop.time() + window
            if key in held:
                held[key] = {**held[key], "delta": held[key]["delta"] + event["delta"]}
            else:
                held[key] = event
        else:
            yield [*held.values(), event]
            held.clear()


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
