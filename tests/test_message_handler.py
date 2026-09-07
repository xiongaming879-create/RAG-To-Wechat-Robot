# -*- coding: utf-8 -*-
"""T15 消息处理流水线测试：全部 mock 依赖，不连真实服务."""
import asyncio

import pytest

import app.message_handler as mh
from app.config import settings
from app.message_handler import (
    SlidingWindowLimiter,
    handle_message,
    pending_uploads,
    process_question,
    stash_pending_file,
)
from app.message_parser import MsgIdDedup
from app.queue.task_queue import TaskQueue
from app.rag import chain, kb_service
from app.rag.kb_service import KbError
from app.rag.llm_client import LLMClientError
from app.wx_callback import _parse_msg
from app.wx_sender import wx_sender

pytestmark = pytest.mark.anyio


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr(settings, "WX_BOT_USERID", "bot")
    monkeypatch.setattr(settings, "ADMIN_USER_LIST", ["admin"])
    monkeypatch.setattr(mh, "msgid_dedup", MsgIdDedup())
    monkeypatch.setattr(mh, "user_limiter", SlidingWindowLimiter(5, 60.0))
    monkeypatch.setattr(mh, "chat_limiter", SlidingWindowLimiter(2, 1.0))
    pending_uploads.clear()
    return monkeypatch


@pytest.fixture
def sent(monkeypatch):
    calls = []

    async def fake_send(chat_id, content, at_userids=None):
        calls.append({"chat_id": chat_id, "content": content, "at": at_userids})
        return True

    monkeypatch.setattr(wx_sender, "send_text", fake_send)
    return calls


@pytest.fixture
def enqueued(monkeypatch):
    calls = []

    async def fake_put(handler, payload):
        calls.append((handler, payload))
        return {"ok": True, "msg": "ok"}

    monkeypatch.setattr(mh.task_queue, "put", fake_put)
    return calls


def make_msg(**kw):
    msg = {
        "msgid": "m1",
        "msg_type": "text",
        "from_user": "u1",
        "chat_id": "c1",
        "content": "什么是 RAG",
        "at_userids": [],
    }
    msg.update(kw)
    return msg


async def test_non_at_message_dropped(env, sent, enqueued):
    await handle_message(make_msg(at_userids=["someone"], content="普通群消息"))
    assert sent == []
    assert enqueued == []


async def test_at_question_enqueued(env, sent, enqueued):
    msg = make_msg(at_userids=["bot"])
    await handle_message(msg)
    assert enqueued and enqueued[0][0] is process_question
    assert enqueued[0][1] is msg
    assert sent == []


async def test_at_without_text_prompt(env, sent, enqueued):
    await handle_message(make_msg(at_userids=["bot"], content="@bot "))
    assert len(sent) == 1
    assert sent[0]["content"] == "请问有什么可以帮您？"
    assert sent[0]["chat_id"] == "c1"
    assert enqueued == []


async def test_non_admin_command_ignored(env, sent, enqueued):
    await handle_message(make_msg(at_userids=["bot"], from_user="u1", content="@bot #kb:list"))
    assert sent == []
    assert enqueued == []


async def test_admin_list_command_replies_at_admin(env, sent, enqueued, monkeypatch):
    async def fake_list():
        return [
            {"doc_id": "d1", "filename": "a.pdf", "file_hash": "h1", "upload_time": "1", "chunk_count": 3},
            {"doc_id": "d2", "filename": "b.md", "file_hash": "h2", "upload_time": "2", "chunk_count": 1},
        ]

    monkeypatch.setattr(kb_service, "list_docs", fake_list)
    await handle_message(make_msg(at_userids=["bot"], from_user="admin", content="@bot #kb:list"))
    assert len(sent) == 1
    assert "a.pdf" in sent[0]["content"] and "b.md" in sent[0]["content"]
    assert sent[0]["at"] == ["admin"]
    assert enqueued == []


async def test_admin_list_empty_kb(env, sent, monkeypatch):
    async def fake_list():
        return []

    monkeypatch.setattr(kb_service, "list_docs", fake_list)
    await handle_message(make_msg(at_userids=["bot"], from_user="admin", content="@bot #kb:list"))
    assert sent[0]["content"] == "知识库为空"


async def test_admin_delete_without_arg_usage_hint(env, sent, monkeypatch):
    await handle_message(make_msg(at_userids=["bot"], from_user="admin", content="@bot #kb:delete"))
    assert len(sent) == 1
    assert "用法" in sent[0]["content"]
    assert sent[0]["at"] == ["admin"]


async def test_admin_delete_success(env, sent, monkeypatch):
    async def fake_list():
        return [{"doc_id": "d1", "filename": "a.pdf", "file_hash": "h", "upload_time": "1", "chunk_count": 1}]

    deleted = []

    async def fake_delete(doc_id):
        deleted.append(doc_id)
        return {"status": "ok"}

    monkeypatch.setattr(kb_service, "list_docs", fake_list)
    monkeypatch.setattr(kb_service, "delete", fake_delete)
    await handle_message(make_msg(at_userids=["bot"], from_user="admin", content="@bot #kb:delete a.pdf"))
    assert deleted == ["d1"]
    assert "a.pdf" in sent[0]["content"]
    assert sent[0]["at"] == ["admin"]


async def test_admin_delete_not_found(env, sent, monkeypatch):
    async def fake_list():
        return []

    monkeypatch.setattr(kb_service, "list_docs", fake_list)
    await handle_message(make_msg(at_userids=["bot"], from_user="admin", content="@bot #kb:delete nope.pdf"))
    assert "未找到" in sent[0]["content"]


async def test_admin_upload_without_pending_file(env, sent, monkeypatch):
    await handle_message(make_msg(at_userids=["bot"], from_user="admin", content="@bot #kb:upload"))
    assert sent[0]["content"] == "请先发送要上传的文件"


async def test_admin_upload_with_pending_file(env, sent, monkeypatch):
    stash_pending_file("admin", "media-123", "a.pdf")
    downloaded = []
    uploaded = []

    async def fake_download(media_id):
        downloaded.append(media_id)
        return b"file-bytes"

    async def fake_upload(file_bytes, filename):
        uploaded.append((file_bytes, filename))
        return {"status": "ok"}

    monkeypatch.setattr(wx_sender, "download_media", fake_download)
    monkeypatch.setattr(kb_service, "upload", fake_upload)
    await handle_message(make_msg(at_userids=["bot"], from_user="admin", content="@bot #kb:upload"))
    assert downloaded == ["media-123"]
    assert uploaded == [(b"file-bytes", "a.pdf")]
    assert "a.pdf" in sent[0]["content"]
    assert sent[0]["at"] == ["admin"]
    assert "admin" not in pending_uploads  # 用后即清


async def test_admin_upload_kb_error_reported(env, sent, monkeypatch):
    stash_pending_file("admin", "media-123", "a.exe")

    async def fake_download(media_id):
        return b"x"

    async def fake_upload(file_bytes, filename):
        raise KbError("不支持的文件格式")

    monkeypatch.setattr(wx_sender, "download_media", fake_download)
    monkeypatch.setattr(kb_service, "upload", fake_upload)
    await handle_message(make_msg(at_userids=["bot"], from_user="admin", content="@bot #kb:upload"))
    assert "不支持的文件格式" in sent[0]["content"]
    assert sent[0]["at"] == ["admin"]


async def test_file_message_stashes_pending_for_admin(env, sent, enqueued):
    await handle_message(make_msg(msg_type="file", from_user="admin", media_id="m1", file_name="a.pdf"))
    assert pending_uploads["admin"]["media_id"] == "m1"
    assert pending_uploads["admin"]["file_name"] == "a.pdf"
    assert sent == []
    assert enqueued == []


async def test_file_message_from_non_admin_ignored(env, sent):
    await handle_message(make_msg(msg_type="file", from_user="u1", media_id="m1", file_name="a.pdf"))
    assert pending_uploads == {}


async def test_parse_msg_extracts_media_fields():
    xml = (
        "<xml><MsgId>123</MsgId><MsgType>file</MsgType><FromUserName>admin</FromUserName>"
        "<ChatId>c1</ChatId><MediaId>MEDIA_ID</MediaId><FileName>报告.pdf</FileName></xml>"
    )
    msg = _parse_msg(xml)
    assert msg["media_id"] == "MEDIA_ID"
    assert msg["file_name"] == "报告.pdf"


def test_user_rate_limit_sixth_within_minute_dropped():
    t = [100.0]
    limiter = SlidingWindowLimiter(5, 60.0, now=lambda: t[0])
    assert all(limiter.allow("u1") for _ in range(5))
    assert limiter.allow("u1") is False
    t[0] += 61.0  # 窗口滑过 → 放行
    assert limiter.allow("u1") is True


def test_chat_rate_limit_two_per_second():
    t = [100.0]
    limiter = SlidingWindowLimiter(2, 1.0, now=lambda: t[0])
    assert limiter.allow("c1") and limiter.allow("c1")
    assert limiter.allow("c1") is False
    t[0] += 1.1
    assert limiter.allow("c1") is True


async def test_handle_message_rate_limit_integration(env, sent, enqueued, monkeypatch):
    monkeypatch.setattr(mh, "user_limiter", SlidingWindowLimiter(5, 60.0))
    for i in range(6):
        # 每条换 chat_id, 隔离群 2 条/秒限制, 单独验证单用户 5 次/分钟
        await handle_message(make_msg(msgid=f"m{i}", at_userids=["bot"], from_user="flood", chat_id=f"c{i}"))
    assert len(enqueued) == 5  # 第 6 条被限流丢弃


async def test_process_question_success_sends_answer(env, sent, monkeypatch):
    async def fake_answer(question, chat_id, user_id):
        return "RAG 是检索增强生成"

    monkeypatch.setattr(chain, "answer", fake_answer)
    await process_question(make_msg(at_userids=["bot"]))
    assert sent == [{"chat_id": "c1", "content": "RAG 是检索增强生成", "at": None}]


async def test_process_question_qdrant_error_replies_maintenance(env, sent, monkeypatch):
    async def boom(question, chat_id, user_id):
        raise RuntimeError("qdrant connection refused")

    monkeypatch.setattr(chain, "answer", boom)
    await process_question(make_msg(at_userids=["bot"]))  # 不应向外抛异常
    assert sent[0]["content"] == "知识库维护中"


async def test_process_question_llm_error_retries_then_busy(env, sent, monkeypatch):
    calls = []

    async def boom(question, chat_id, user_id):
        calls.append(question)
        raise LLMClientError("llm down")

    monkeypatch.setattr(chain, "answer", boom)
    q = TaskQueue(workers=1, retries=2, retry_delay=0.01, on_failure=mh.on_queue_failure)
    await q.start()
    await q.put(process_question, make_msg(at_userids=["bot"]))
    await asyncio.sleep(0.3)
    await q.stop()
    assert len(calls) == 3  # 初次 + 重试 2 次
    assert sent[0]["content"] == "服务繁忙，请稍后再试"
    assert sent[0]["chat_id"] == "c1"
