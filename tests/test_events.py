import asyncio

from sennet.events import EventBus


async def test_next_delivers_events_in_order():
    bus = EventBus()
    subscriber = bus.subscribe()
    await bus.publish({"n": 1})
    await bus.publish({"n": 2})
    assert await subscriber.next() == {"n": 1}
    assert await subscriber.next() == {"n": 2}


async def test_next_returns_none_once_dropped():
    bus = EventBus()
    subscriber = bus.subscribe()
    waiting = asyncio.ensure_future(subscriber.next())
    await asyncio.sleep(0)
    subscriber.dropped.set()
    assert await waiting is None


async def test_cancelled_next_leaves_nothing_pending():
    # A client disconnecting cancels the pump mid-wait; its helper tasks must not linger.
    before = asyncio.all_tasks()
    subscriber = EventBus().subscribe()
    waiting = asyncio.ensure_future(subscriber.next())
    await asyncio.sleep(0)
    waiting.cancel()
    await asyncio.gather(waiting, return_exceptions=True)
    await asyncio.sleep(0)
    assert {task for task in asyncio.all_tasks() - before if not task.done()} == set()
