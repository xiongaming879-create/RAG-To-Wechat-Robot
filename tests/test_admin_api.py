# -*- coding: utf-8 -*-
"""T14: 管理 API 测试 — TestClient + mock kb_service，不连真实服务。"""
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.rag import kb_service
from app.rag.kb_service import KbError

TOKEN = "secret-admin-token"
HEADERS = {"X-Admin-Token": TOKEN}

client = TestClient(app)


@pytest.fixture(autouse=True)
def _admin_token(monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_API_TOKEN", TOKEN)


def _fake_upload(result=None, exc=None):
    async def _upload(file_bytes, filename):
        if exc:
            raise exc
        return result

    return _upload


# ---------- 401: 无/错 token ----------

def test_upload_without_token_401():
    resp = client.post("/api/admin/upload", files={"file": ("a.pdf", b"x")})
    assert resp.status_code == 401


def test_upload_wrong_token_401():
    resp = client.post(
        "/api/admin/upload",
        files={"file": ("a.pdf", b"x")},
        headers={"X-Admin-Token": "wrong"},
    )
    assert resp.status_code == 401


def test_delete_wrong_token_401():
    resp = client.delete("/api/admin/doc/abc", headers={"X-Admin-Token": "bad"})
    assert resp.status_code == 401


def test_docs_without_token_401():
    resp = client.get("/api/admin/docs")
    assert resp.status_code == 401


# ---------- POST /upload ----------

def test_upload_ok(monkeypatch):
    monkeypatch.setattr(
        kb_service, "upload", _fake_upload({"status": "ok", "doc_id": "d1"})
    )
    resp = client.post(
        "/api/admin/upload", files={"file": ("a.pdf", b"pdf-bytes")}, headers=HEADERS
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "doc_id": "d1"}


def test_upload_skipped(monkeypatch):
    monkeypatch.setattr(kb_service, "upload", _fake_upload({"status": "skipped"}))
    resp = client.post(
        "/api/admin/upload", files={"file": ("a.md", b"same")}, headers=HEADERS
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "skipped"}


def test_upload_kb_error_400_with_reason(monkeypatch):
    monkeypatch.setattr(
        kb_service, "upload", _fake_upload(exc=KbError("解析失败: mineru 挂了"))
    )
    resp = client.post(
        "/api/admin/upload", files={"file": ("a.pdf", b"x")}, headers=HEADERS
    )
    assert resp.status_code == 400
    assert "解析失败" in resp.json()["detail"]


def test_upload_unexpected_error_500(monkeypatch):
    monkeypatch.setattr(
        kb_service, "upload", _fake_upload(exc=RuntimeError("boom"))
    )
    resp = client.post(
        "/api/admin/upload", files={"file": ("a.txt", b"x")}, headers=HEADERS
    )
    assert resp.status_code == 500


# ---------- DELETE /doc/{doc_id} ----------

def test_delete_ok(monkeypatch):
    async def fake_list_docs():
        return [{"doc_id": "d1", "filename": "a.pdf"}]

    deleted = []

    async def fake_delete(doc_id):
        deleted.append(doc_id)
        return {"status": "ok"}

    monkeypatch.setattr(kb_service, "list_docs", fake_list_docs)
    monkeypatch.setattr(kb_service, "delete", fake_delete)
    resp = client.delete("/api/admin/doc/d1", headers=HEADERS)
    assert resp.status_code == 200
    assert deleted == ["d1"]


def test_delete_not_found_404(monkeypatch):
    async def fake_list_docs():
        return [{"doc_id": "other"}]

    monkeypatch.setattr(kb_service, "list_docs", fake_list_docs)
    resp = client.delete("/api/admin/doc/ghost", headers=HEADERS)
    assert resp.status_code == 404


def test_delete_kb_error_400(monkeypatch):
    async def fake_list_docs():
        return [{"doc_id": "d1"}]

    async def fake_delete(doc_id):
        raise KbError("删除失败: qdrant down")

    monkeypatch.setattr(kb_service, "list_docs", fake_list_docs)
    monkeypatch.setattr(kb_service, "delete", fake_delete)
    resp = client.delete("/api/admin/doc/d1", headers=HEADERS)
    assert resp.status_code == 400
    assert "删除失败" in resp.json()["detail"]


# ---------- GET /docs 分页 ----------

@pytest.fixture()
def fake_docs_25(monkeypatch):
    docs = [{"doc_id": f"d{i}", "filename": f"f{i}.pdf"} for i in range(25)]

    async def fake_list_docs():
        return docs

    monkeypatch.setattr(kb_service, "list_docs", fake_list_docs)


def test_docs_default_pagination(fake_docs_25):
    resp = client.get("/api/admin/docs", headers=HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 25
    assert body["page"] == 1
    assert body["page_size"] == 20
    assert len(body["items"]) == 20
    assert body["items"][0]["doc_id"] == "d0"


def test_docs_page2(fake_docs_25):
    resp = client.get(
        "/api/admin/docs?page=2&page_size=20", headers=HEADERS
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 5
    assert body["items"][0]["doc_id"] == "d20"


def test_docs_kb_error_400(monkeypatch):
    async def bad():
        raise KbError("查询文档列表失败: qdrant down")

    monkeypatch.setattr(kb_service, "list_docs", bad)
    resp = client.get("/api/admin/docs", headers=HEADERS)
    assert resp.status_code == 400
    assert "查询文档列表失败" in resp.json()["detail"]
