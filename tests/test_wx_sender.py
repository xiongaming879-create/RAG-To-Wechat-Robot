# -*- coding: utf-8 -*-
"""Unit tests for WxSender. All HTTP calls go through httpx.MockTransport — no real network."""
import httpx
import pytest

from app.wx_sender import WxSender, split_content

pytestmark = pytest.mark.anyio

SEND_URL = "https://qyapi.weixin.qq.com/cgi-bin/message/send"


class FakeTokens:
    """模拟 wx_access_token.get()：按顺序吐 token，最后一个无限重复."""

    def __init__(self, *tokens):
        self.tokens = list(tokens)
        self.calls = 0

    async def get(self) -> str:
        self.calls += 1
        return self.tokens.pop(0) if len(self.tokens) > 1 else self.tokens[0]


def make_sender(handler, tokens=("tok-1",)):
    counter = []

    def counting_handler(request):
        counter.append(request)
        return handler(request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(counting_handler))
    return WxSender(token_getter=FakeTokens(*tokens).get, client=client, backoff=(0, 0)), counter


# ---------- send_text ----------

async def test_short_message_single_send():
    """≤1800 单条发送，body 含 chatid/content，无分片标记."""
    bodies = []

    def handler(request):
        bodies.append(request.read())
        return httpx.Response(200, json={"errcode": 0, "errmsg": "ok"})

    sender, counter = make_sender(handler)
    ok = await sender.send_text("chat-1", "你好世界")
    assert ok is True
    assert len(counter) == 1
    body = __import__("json").loads(bodies[0])
    assert body["chatid"] == "chat-1"
    assert body["msgtype"] == "text"
    assert body["text"]["content"] == "你好世界"
    assert "(1/" not in body["text"]["content"]
    assert "access_token=tok-1" in str(counter[0].url)


async def test_2500_chars_split_two_chunks_in_order():
    """2500 字按段落拆 2 段，带 (1/2)(2/2)，逐条顺序发送."""
    bodies = []

    def handler(request):
        bodies.append(request.read())
        return httpx.Response(200, json={"errcode": 0})

    sender, counter = make_sender(handler)
    content = "甲" * 1300 + "\n" + "乙" * 1200  # 2501 字符，两个段落
    ok = await sender.send_text("chat-1", content)
    assert ok is True
    assert len(counter) == 2
    c1 = __import__("json").loads(bodies[0])["text"]["content"]
    c2 = __import__("json").loads(bodies[1])["text"]["content"]
    assert c1.endswith("(1/2)")
    assert c2.endswith("(2/2)")
    assert len(c1) <= 1800 + 6
    assert len(c2) <= 1800 + 6


async def test_long_paragraph_never_splits_sentence():
    """无换行长文本按句子边界拆，不拆开一句话."""

    def handler(request):
        return httpx.Response(200, json={"errcode": 0})

    sender, _ = make_sender(handler)
    sentence = "知" * 499 + "。"  # 每句 500 字
    content = sentence * 5  # 2500 字无换行
    ok = await sender.send_text("chat-1", content)
    assert ok is True
    chunks = split_content(content)
    assert len(chunks) >= 2
    for chunk in chunks:
        body = chunk.rsplit("(", 1)[0]  # 去掉 (n/m) 标记
        assert body in content, "分片必须是原文的连续子串（不得切开一句话）"


async def test_at_userids_mapped_to_mentioned_list():
    bodies = []

    def handler(request):
        bodies.append(request.read())
        return httpx.Response(200, json={"errcode": 0})

    sender, _ = make_sender(handler)
    ok = await sender.send_text("chat-1", "hello", at_userids=["u1", "u2"])
    assert ok is True
    body = __import__("json").loads(bodies[0])
    assert body["text"]["mentioned_list"] == ["u1", "u2"]


async def test_no_at_userids_omits_mentioned_list():
    bodies = []

    def handler(request):
        bodies.append(request.read())
        return httpx.Response(200, json={"errcode": 0})

    sender, _ = make_sender(handler)
    await sender.send_text("chat-1", "hello")
    body = __import__("json").loads(bodies[0])
    assert "mentioned_list" not in body["text"]


async def test_40014_refreshes_token_and_resends():
    """errcode 40014 → 刷新 token → 重发一次成功."""
    requests_seen = []

    def route(request):
        requests_seen.append(request)
        if len(requests_seen) == 1:
            return httpx.Response(200, json={"errcode": 40014, "errmsg": "invalid credential"})
        return httpx.Response(200, json={"errcode": 0})

    client = httpx.AsyncClient(transport=httpx.MockTransport(route))
    toks = FakeTokens("stale-token", "fresh-token")
    sender = WxSender(token_getter=toks.get, client=client, backoff=(0, 0))

    ok = await sender.send_text("chat-1", "hello")
    assert ok is True
    assert toks.calls == 2  # 刷新调了 get()
    assert "access_token=stale-token" in str(requests_seen[0].url)
    assert "access_token=fresh-token" in str(requests_seen[1].url)


async def test_40014_retry_still_fails_returns_false():
    def handler(request):
        return httpx.Response(200, json={"errcode": 40014})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    toks = FakeTokens("t1", "t2")
    sender = WxSender(token_getter=toks.get, client=client, backoff=(0, 0))
    ok = await sender.send_text("chat-1", "hello")
    assert ok is False
    assert toks.calls == 2  # 刷新后重发一次即放弃


async def test_generic_error_retried_twice_then_false():
    counter = []

    def handler(request):
        counter.append(request)
        return httpx.Response(200, json={"errcode": 40013, "errmsg": "invalid corpid"})

    sender, _ = make_sender(handler)
    ok = await sender.send_text("chat-1", "hello")
    assert ok is False
    assert len(counter) == 3  # 首次 + 重试 2 次


# ---------- split_content 纯函数 ----------

def test_split_exactly_1800_no_split():
    content = "字" * 1800
    assert split_content(content) == [content]


def test_split_just_over_1800_hard_split():
    content = "字" * 1801
    chunks = split_content(content)
    assert len(chunks) == 2
    assert chunks[0].endswith("(1/2)")
    assert chunks[1].endswith("(2/2)")
    assert chunks[0].rsplit("(", 1)[0] == "字" * 1800
    assert chunks[1].rsplit("(", 1)[0] == "字" * 1


def test_split_very_long_multi_chunks():
    content = "字" * 4000
    chunks = split_content(content)
    assert len(chunks) == 3
    for i, c in enumerate(chunks):
        assert c.endswith(f"({i + 1}/3)")
        assert len(c.rsplit("(", 1)[0]) <= 1800


def test_split_packs_paragraphs_greedily():
    p1, p2, p3 = "a" * 700, "b" * 700, "c" * 700
    content = f"{p1}\n{p2}\n{p3}"
    chunks = split_content(content)
    assert len(chunks) == 2
    assert chunks[0].rsplit("(", 1)[0] == f"{p1}\n{p2}"
    assert chunks[1].rsplit("(", 1)[0] == p3
