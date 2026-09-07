"""异步任务队列：固定并发 worker + 超时 + 重试 + 积压保护。

handler 契约：``async def handler(payload) -> str``，回复文本由 handler 自己负责发送；
超时/重试失败时通过 ``on_failure(payload, reason)`` 回调通知（T15 在此挂"服务繁忙"回复）。
"""
import asyncio
import logging

from app.config import settings

logger = logging.getLogger(__name__)

BACKOFF_FULL_MSG = "当前咨询量较大，请稍后再试"


class FatalTaskError(Exception):
    """不可恢复错误（如解析失败）：不重试，丢弃并记日志。"""


class TaskQueue:
    def __init__(
        self,
        maxsize: int | None = None,
        workers: int | None = None,
        timeout: float | None = None,
        retries: int | None = None,
        retry_delay: float = 1.0,
        on_failure=None,
    ):
        self.maxsize = maxsize if maxsize is not None else settings.QUEUE_MAXSIZE
        self.workers = workers if workers is not None else settings.QUEUE_WORKERS
        self.timeout = timeout if timeout is not None else settings.QUEUE_TIMEOUT
        self.retries = retries if retries is not None else settings.QUEUE_RETRIES
        # ponytail: 指数退避基数（1s/2s/...），仅测试调小；生产默认 1.0
        self.retry_delay = retry_delay
        self.on_failure = on_failure
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=self.maxsize)
        self._worker_tasks: list[asyncio.Task] = []

    async def enqueue(self, handler, payload) -> None:
        """入队原语；替换为 Redis 版时只改此方法。"""
        self._queue.put_nowait((handler, payload))

    def qsize(self) -> int:
        """当前积压任务数（只读，/health 用）。"""
        return self._queue.qsize()

    async def put(self, handler, payload) -> dict:
        """队列满返回 ok=False（积压保护），否则入队。"""
        try:
            await self.enqueue(handler, payload)
        except asyncio.QueueFull:
            return {"ok": False, "msg": BACKOFF_FULL_MSG}
        return {"ok": True, "msg": "已受理"}

    async def start(self) -> None:
        self._worker_tasks = [asyncio.create_task(self._worker()) for _ in range(self.workers)]

    async def stop(self) -> None:
        """优雅停止：先排空（drain）队列中剩余任务，再退出 worker。"""
        if self._worker_tasks:
            for _ in self._worker_tasks:
                try:
                    self._queue.put_nowait(None)
                except asyncio.QueueFull:
                    # 队列满恰说明 worker 活着且在消费，await put 必有界成功，
                    # 不会永久挂起（worker 死亡场景已被 _worker 兜底防护排除）
                    await self._queue.put(None)
            await asyncio.gather(*self._worker_tasks, return_exceptions=True)
        self._worker_tasks = []

    async def _worker(self) -> None:
        while True:
            item = await self._queue.get()
            if item is None:
                break
            handler, payload = item
            try:
                await self._run(handler, payload)
            except Exception:
                # 兜底防护：任何意外异常不允许杀死 worker（否则并发容量永久减少）
                logger.exception("worker 处理任务时发生意外异常 payload=%r", payload)

    async def _run(self, handler, payload) -> None:
        for attempt in range(self.retries + 1):
            if attempt:
                await asyncio.sleep(self.retry_delay * (2 ** (attempt - 1)))  # 1s / 2s
            try:
                await asyncio.wait_for(handler(payload), timeout=self.timeout)
                return
            except FatalTaskError as e:
                # 致命错误：丢弃，不重试，也不触发 on_failure
                logger.warning("任务致命错误，已丢弃: %s payload=%r", e, payload)
                return
            except asyncio.TimeoutError:
                reason = "timeout"
            except Exception as e:
                reason = f"error: {e}"
            logger.warning("任务失败 (attempt %d/%d): %s", attempt + 1, self.retries + 1, reason)
        if self.on_failure:
            try:
                self.on_failure(payload, reason)
            except Exception:
                # 回调抛异常（如回复发送失败）不能波及 worker
                logger.exception("on_failure 回调异常 payload=%r", payload)


task_queue = TaskQueue()
