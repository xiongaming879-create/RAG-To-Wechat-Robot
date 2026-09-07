"""Unit tests for RAG chain. retriever / llm_client 全 mock，context 用独立实例。"""
import pytest

from app.context import ContextStore
from app.rag import chain, retriever

pytestmark = pytest.mark.anyio

CHUNKS = [
    {"text": "报销流程：先提交OA。", "filename": "baoxiao.pdf", "chunk_index": 2, "score": 0.9},
    {"text": "报销周期为 3 个工作日。", "filename": "baoxiao.pdf", "chunk_index": 5, "score": 0.8},
]


def patch_all(monkeypatch, retrieve_result, fresh_context=True):
    calls = {"retrieve": [], "chat": []}

    async def fake_retrieve(q):
        calls["retrieve"].append(q)
        return retrieve_result

    async def fake_chat(system, user):
        calls["chat"].append({"system": system, "user": user})
        return "mock 回答"

    monkeypatch.setattr(retriever, "retrieve", fake_retrieve)
    monkeypatch.setattr(chain.llm_client, "chat", fake_chat)
    if fresh_context:
        monkeypatch.setattr(chain, "context_store", ContextStore())
    return calls


async def test_no_hits_returns_fallback_and_skips_chat(monkeypatch):
    calls = patch_all(monkeypatch, None)
    reply = await chain.answer("不相干问题", "c1", "u1")
    assert reply == "【知识库暂无相关信息】"
    assert calls["chat"] == []
    assert chain.context_store.get("c1", "u1") == []


async def test_chat_receives_prompt_with_source_annotations(monkeypatch):
    calls = patch_all(monkeypatch, CHUNKS)
    reply = await chain.answer("报销流程", "c1", "u1")
    assert reply == "mock 回答"
    msg = calls["chat"][0]
    assert msg["system"] == chain.SYSTEM_PROMPT
    for rule in ("严格仅使用", "【知识库暂无相关信息】", "禁止编造", "简洁、准确"):
        assert rule in msg["system"]
    assert "来源：baoxiao.pdf（第 2 块）" in msg["user"]
    assert "来源：baoxiao.pdf（第 5 块）" in msg["user"]
    assert "报销流程：先提交OA。" in msg["user"]
    assert "用户问题：报销流程" in msg["user"]


async def test_history_included_in_user_message_and_retrieval_query(monkeypatch):
    calls = patch_all(monkeypatch, CHUNKS)
    chain.context_store.append("c1", "u1", "上一问", "上一答")
    await chain.answer("当前问题", "c1", "u1")
    assert "上一问" in calls["retrieve"][0]
    assert "当前问题" in calls["retrieve"][0]
    user_msg = calls["chat"][0]["user"]
    assert "user：上一问" in user_msg or "用户：上一问" in user_msg
    assert "assistant：上一答" in user_msg or "助手：上一答" in user_msg
    assert "用户问题：当前问题" in user_msg


async def test_answer_written_to_context(monkeypatch):
    patch_all(monkeypatch, CHUNKS)
    await chain.answer("问", "c1", "u1")
    assert chain.context_store.get("c1", "u1") == [
        {"role": "user", "content": "问"},
        {"role": "assistant", "content": "mock 回答"},
    ]


async def test_no_history_retrieve_gets_raw_question(monkeypatch):
    calls = patch_all(monkeypatch, None)
    await chain.answer("裸问题", "c1", "u1")
    assert calls["retrieve"] == ["裸问题"]
