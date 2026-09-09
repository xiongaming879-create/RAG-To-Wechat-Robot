# -*- coding: utf-8 -*-
"""W2: 静态 Web 页面 — GET / 返回聊天页，/static 托管 admin.html。"""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_index_serves_chat_page():
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    body = resp.text
    # 聊天页标记 + 管理入口链接 + 核心接口调用
    assert "知识库问答" in body
    assert "/static/admin.html" in body
    assert "/api/chat" in body


def test_static_admin_page():
    resp = client.get("/static/admin.html")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    body = resp.text
    assert "知识库管理" in body
    for endpoint in ("/api/admin/upload", "/api/admin/docs", "/api/admin/doc/"):
        assert endpoint in body
