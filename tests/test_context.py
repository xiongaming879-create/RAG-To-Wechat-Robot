"""Unit tests for ContextStore. 注入时钟控制过期，无真实 IO。"""
from app.context import ContextStore


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def make_store(ttl=600.0):
    clock = FakeClock()
    return ContextStore(ttl=ttl, clock=clock), clock


def test_append_then_get_round_trip():
    store, _ = make_store()
    store.append("c1", "u1", "q1", "a1")
    assert store.get("c1", "u1") == [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
    ]


def test_fourth_round_evicts_first():
    store, _ = make_store()
    for i in range(4):
        store.append("c1", "u1", f"q{i}", f"a{i}")
    history = store.get("c1", "u1")
    assert [m["content"] for m in history] == ["q1", "a1", "q2", "a2", "q3", "a3"]


def test_expired_history_cleared():
    store, clock = make_store(ttl=600.0)
    store.append("c1", "u1", "q1", "a1")
    clock.advance(601)
    assert store.get("c1", "u1") == []


def test_expiry_is_from_last_message():
    """最后消息后 10 分钟过期：中途加一轮可延长寿命。"""
    store, clock = make_store(ttl=600.0)
    store.append("c1", "u1", "q1", "a1")
    clock.advance(500)
    store.append("c1", "u1", "q2", "a2")
    clock.advance(500)
    assert len(store.get("c1", "u1")) == 4  # 未过期
    clock.advance(101)
    assert store.get("c1", "u1") == []


def test_isolated_by_chat_and_user():
    store, _ = make_store()
    store.append("c1", "u1", "q1", "a1")
    assert store.get("c1", "u2") == []
    assert store.get("c2", "u1") == []
    store.append("c2", "u1", "q2", "a2")
    assert store.get("c1", "u1")[0]["content"] == "q1"
    assert store.get("c2", "u1")[0]["content"] == "q2"


def test_get_returns_copy_without_ts():
    """返回的列表不带内部 ts 字段，且修改不影响内部状态。"""
    store, _ = make_store()
    store.append("c1", "u1", "q1", "a1")
    history = store.get("c1", "u1")
    assert all(set(m) == {"role", "content"} for m in history)
    history.append({"role": "user", "content": "injected"})
    assert len(store.get("c1", "u1")) == 2
