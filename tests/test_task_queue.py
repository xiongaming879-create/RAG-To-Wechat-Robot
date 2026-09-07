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


async def test_worker_concurrency_capped():
    concurrent = 0
    peak = 0

    async def handler(payload):
        nonlocal concurrent, peak
        concurrent += 1
        peak = max(peak, concurrent)
        await asyncio.sleep(0.05)
        concurrent -= 1

    q = TaskQueue(workers=2, maxsize=10)
    await q.start()
    for i in range(6):
        await q.put(handler, i)
    await asyncio.sleep(0.4)
    await q.stop()
    assert peak == 2  # 两个 worker 满负载跑，但峰值不超过 workers


async def test_retry_success_no_on_failure():
    calls = []
    failures = []

    async def handler(payload):
        calls.append(payload)
        if len(calls) == 1:
            raise ValueError("flaky")
        return "ok"

    q = TaskQueue(workers=1, retries=2, retry_delay=0.01, on_failure=lambda p, r: failures.append(r))

    await q.start()
    await q.put(handler, "p1")
    await asyncio.sleep(0.2)
    await q.stop()
    assert calls == ["p1", "p1"]  # 首次失败 + 重试成功
    assert failures == []


async def test_on_failure_raises_does_not_kill_worker():
    calls = []

    async def handler(payload):
        calls.append(payload)

    def bad_on_failure(p, r):
        raise RuntimeError("reply send failed")

    q = TaskQueue(workers=1, timeout=0.05, retries=0, on_failure=bad_on_failure)

    await q.start()
    await q.put(handler, "slow")  # 触发超时 → on_failure 抛异常
    await asyncio.sleep(0.2)
    await q.put(handler, "next")  # worker 仍应存活并处理后续任务
    await asyncio.sleep(0.2)
    await q.stop()
    assert calls == ["slow", "next"]


async def test_stop_with_full_queue_returns_bounded():
    async def handler(payload):
        await asyncio.sleep(0.05)

    q = TaskQueue(maxsize=2, workers=1, retries=0)
    await q.start()
    await q.put(handler, 0)
    await asyncio.sleep(0.02)  # worker 已取走 item0 并在处理中
    assert (await q.put(handler, 1))["ok"] is True
    assert (await q.put(handler, 2))["ok"] is True  # 队列满
    assert (await q.put(handler, 3))["ok"] is False  # 积压保护
    # 满队列下 stop()：哨兵 put_nowait 失败 → 回退 await put，排空后有界返回，不挂起
    await asyncio.wait_for(q.stop(), timeout=2)


def test_module_level_singleton():
    assert isinstance(task_queue, TaskQueue)


def test_qsize_readonly():
    q = TaskQueue(workers=1)
    assert q.qsize() == 0

    async def handler(payload):
        return "ok"

    q._queue.put_nowait((handler, {}))
    assert q.qsize() == 1
