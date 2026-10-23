import asyncio

from sennet.events import IDLE, EventBus, batches


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


async def test_next_returns_idle_after_its_timeout():
    subscriber = EventBus().subscribe()
    assert await subscriber.next(0.01) is IDLE


def delta(item: str, text: str, task: int = 1) -> dict:
    return {"type": "delta", "task_id": task, "item_id": item, "delta": text}


async def test_batches_join_deltas_per_item_within_the_window():
    bus = EventBus()
    stream = batches(bus.subscribe(), window=0.05)
    for event in (delta("a", "Hel"), delta("b", "Other"), delta("a", "lo"), delta("a", " there", task=2)):
        await bus.publish(event)
    assert await anext(stream) == [delta("a", "Hello"), delta("b", "Other"), delta("a", " there", task=2)]


async def test_batches_send_held_deltas_before_the_next_event():
    bus = EventBus()
    stream = batches(bus.subscribe(), window=10)
    await bus.publish(delta("a", "Hi"))
    await bus.publish({"type": "message", "task_id": 1})
    assert await anext(stream) == [delta("a", "Hi"), {"type": "message", "task_id": 1}]


async def test_batches_pass_other_events_straight_through():
    bus = EventBus()
    stream = batches(bus.subscribe(), window=10)
    await bus.publish({"type": "task", "n": 1})
    assert await asyncio.wait_for(anext(stream), 1) == [{"type": "task", "n": 1}]


async def test_batches_end_once_dropped():
    bus = EventBus()
    subscriber = bus.subscribe()
    stream = batches(subscriber)
    subscriber.dropped.set()
    assert [batch async for batch in stream] == []
