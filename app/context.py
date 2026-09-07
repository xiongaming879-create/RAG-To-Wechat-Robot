"""对话上下文：按 群ID+用户ID 存最近 3 轮，最后消息 10 分钟过期。

内存 dict 实现；Redis 替换时保持 get/append 协议不变即可。
"""
import time

MAX_ROUNDS = 3
TTL_SECONDS = 600


class ContextStore:
    def __init__(self, ttl: float = TTL_SECONDS, clock=time.monotonic):
        self._ttl = ttl
        self._clock = clock
        # ponytail: 内存 dict 单进程版；多实例部署时按同协议换 Redis（ZSET/Hash + 过期）
        self._store: dict[tuple[str, str], list[dict]] = {}

    def _clean(self, key: tuple[str, str]) -> None:
        msgs = self._store.get(key)
        if msgs and self._clock() - msgs[-1]["ts"] > self._ttl:
            del self._store[key]

    def get(self, chat_id: str, user_id: str) -> list[dict]:
        """最近 MAX_ROUNDS 轮（时间序），每项 {"role", "content"}，无内部字段。"""
        key = (chat_id, user_id)
        self._clean(key)
        return [{"role": m["role"], "content": m["content"]}
                for m in self._store.get(key, [])]

    def append(self, chat_id: str, user_id: str, question: str, answer: str) -> None:
        """追加一轮，超 MAX_ROUNDS 淘汰最旧；过期条目清理。"""
        key = (chat_id, user_id)
        self._clean(key)
        now = self._clock()
        msgs = self._store.setdefault(key, [])
        msgs.append({"role": "user", "content": question, "ts": now})
        msgs.append({"role": "assistant", "content": answer, "ts": now})
        del msgs[:-2 * MAX_ROUNDS]


context_store = ContextStore()
