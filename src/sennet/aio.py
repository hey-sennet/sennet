"""Asyncio helpers."""
from __future__ import annotations

import asyncio
from collections.abc import Iterable


async def settle(jobs: Iterable[asyncio.Future]):
    """Wait for jobs just cancelled to finish, keeping whatever they raised to themselves.

    Not asyncio.gather: if this task is cancelled while it waits (Starlette cancels a socket's
    handler as the socket closes), gather raises the jobs' cancellation instead of this task's,
    which anyio doesn't recognise as its own and lets escape the handler. asyncio.wait raises
    this task's own."""
    jobs = list(jobs)
    if not jobs:
        return
    await asyncio.wait(jobs)
    for job in jobs:
        if not job.cancelled():
            job.exception()  # retrieved, so asyncio doesn't log it as never retrieved
