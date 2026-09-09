# -*- coding: utf-8 -*-
"""W1: Web 问答 API 测试 — TestClient + mock chain，不连真实服务。"""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.rag import chain
from app.rag.llm_client import LLMClientError

client = TestClient(app)

SOURCES = [
    {"filename": "baoxiao.pdf", "chunk_index": 2, "score": 0.9},
    {"filename": "baoxiao.pdf", "chunk_index": 5, "score": 0.8},
]


def _fake_chain(answer="mock 回答", sources=SOURCES, exc=None):
    async def _answer_with_sources(question, chat_id, user_id):
        assert chat_id == "web" and user_id == "web"
        if exc:
            raise exc
        return {"answer": answer, "sources": sources}

    return _answer_with_sources


def test_missing_question_400(monkeypatch):
    monkeypatch.setattr(chain, "answer_with_sources", _fake_chain())
    resp = client.post("/api/chat", json={})
    assert resp.status_code == 400
    assert resp.json()["error"]


def test_blank_question_400(monkeypatch):
    monkeypatch.setattr(chain, "answer_with_sources", _fake_chain())
    resp = client.post("/api/chat", json={"question": "   "})
    assert resp.status_code == 400
    assert resp.json()["error"]


def test_chat_ok_200(monkeypatch):
    monkeypatch.setattr(chain, "answer_with_sources", _fake_chain())
    resp = client.post("/api/chat", json={"question": "报销流程是什么"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["answer"] == "mock 回答"
    assert body["sources"] == SOURCES
    assert isinstance(body["elapsed_ms"], int)
    assert body["elapsed_ms"] >= 0


def test_chat_no_hits_empty_sources(monkeypatch):
    monkeypatch.setattr(
        chain, "answer_with_sources", _fake_chain(answer="【知识库暂无相关信息】", sources=[])
    )
    resp = client.post("/api/chat", json={"question": "无关问题"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["answer"] == "【知识库暂无相关信息】"
    assert body["sources"] == []


def test_llm_client_error_502(monkeypatch):
    monkeypatch.setattr(
        chain, "answer_with_sources", _fake_chain(exc=LLMClientError("重试耗尽"))
    )
    resp = client.post("/api/chat", json={"question": "报销流程"})
    assert resp.status_code == 502
    assert resp.json() == {"error": "服务繁忙，请稍后再试"}


def test_unexpected_error_500(monkeypatch):
    monkeypatch.setattr(
        chain, "answer_with_sources", _fake_chain(exc=RuntimeError("boom"))
    )
    resp = client.post("/api/chat", json={"question": "报销流程"})
    assert resp.status_code == 500
    assert resp.json()["error"]
