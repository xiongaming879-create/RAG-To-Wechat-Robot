import asyncio

import pytest

from app.queue.task_queue import FatalTaskError, TaskQueue, task_queue

pytestmark = pytest.mark.anyio


async def test_put_calls_handler_with_payload():
    q = TaskQueue(workers=1)
    got = asyncio.Event()
    seen = {}

    async def handler(payload):
        seen["p"] = payload
        got.set()
        return "ok"

    await q.start()
    result = await q.put(handler, {"msg": "hi"})
    await asyncio.wait_for(got.wait(), timeout=2)
    await q.stop()
    assert result == {"ok": True, "msg": "已受理"}
    assert seen["p"] == {"msg": "hi"}


async def test_timeout_interrupts_handler_and_calls_on_failure():
    failures = []

    async def handler(payload):
        await asyncio.sleep(1)

    q = TaskQueue(workers=1, timeout=0.05, retries=0, on_failure=lambda p, r: failures.append((p, r)))

    await q.start()
    await q.put(handler, "p1")
    await asyncio.sleep(0.3)
    await q.stop()
    assert failures == [("p1", "timeout")]


async def test_retries_twice_then_on_failure():
    calls = []

    async def handler(payload):
        calls.append(payload)
        raise ValueError("llm boom")

    q = TaskQueue(workers=1, retries=2, retry_delay=0.01, on_failure=lambda p, r: None)

    await q.start()
    await q.put(handler, "p1")
    await asyncio.sleep(0.3)
    await q.stop()
    assert calls == ["p1", "p1", "p1"]  # 初次 + 重试 2 次


async def test_fatal_error_drops_without_retry():
    calls = []

    async def handler(payload):
        calls.append(payload)
        raise FatalTaskError("parse failed")

    q = TaskQueue(workers=1, retries=2, retry_delay=0.01)

    await q.start()
    await q.put(handler, "p1")
    await asyncio.sleep(0.2)
    await q.stop()
    assert calls == ["p1"]  # 只调用一次，不重试


async def test_queue_full_rejects_put():
    q = TaskQueue(maxsize=2)
    # 不 start，队列不消费
    async def handler(payload):
        return "ok"

    assert (await q.put(handler, "a"))["ok"] is True
    assert (await q.put(handler, "b"))["ok"] is True
    result = await q.put(handler, "c")
    assert result == {"ok": False, "msg": "当前咨询量较大，请稍后再试"}


async def test_stop_stops_consuming():
    q = TaskQueue(workers=1)
    called = []

    async def handler(payload):
        called.append(payload)

    await q.start()
    await q.stop()
    await q.put(handler, "late")
    await asyncio.sleep(0.1)
    assert called == []


def test_module_level_singleton():
    assert isinstance(task_queue, TaskQueue)
