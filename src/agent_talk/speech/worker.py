"""Inference off the event loop: one thread per model, taking work by priority."""
from __future__ import annotations

import asyncio
import itertools
import queue
import threading
from typing import Any, Callable

# Lower runs first. A final pass is what the user is waiting on; a segment finalized
# during a pause will be soon; captions are a preview.
FINAL = 0
SEGMENT = 1
CAPTION = 2


class Worker:
    def __init__(self, name: str):
        self.name = name
        self.jobs: queue.PriorityQueue = queue.PriorityQueue()
        self.order = itertools.count()
        self.thread = threading.Thread(target=self._loop, name=f"speech-{name}", daemon=True)
        self.thread.start()

    async def run(self, fn: Callable[..., Any], *args, priority: int = FINAL) -> Any:
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self.jobs.put((priority, next(self.order), fn, args, future, loop))
        return await future

    def close(self):
        self.jobs.put((-1, next(self.order), None, (), None, None))

    def _loop(self):
        while True:
            _, _, fn, args, future, loop = self.jobs.get()
            if fn is None:
                return
            if future.cancelled():  # the caller gave up while it was queued
                continue
            try:
                result, error = fn(*args), None
            except BaseException as exc:  # handed to the awaiting coroutine
                result, error = None, exc
            try:
                loop.call_soon_threadsafe(_settle, future, result, error)
            except RuntimeError:  # the loop closed while this ran (the daemon shut down); nobody is waiting
                pass


def _settle(future: asyncio.Future, result, error):
    if future.cancelled():
        return
    if error is not None:
        future.set_exception(error)
    else:
        future.set_result(result)
