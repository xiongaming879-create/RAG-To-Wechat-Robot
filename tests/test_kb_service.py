"""T13: 知识库管理服务测试 — 全部 mock，不连真实 MinerU/LLM/Qdrant。"""
import asyncio
import hashlib

import pytest

from app.rag import kb_service
from app.rag.kb_service import KbError
from app.rag.loader import ParseError

NEW_BYTES = b"new doc content"
OLD_BYTES = b"old doc content"
FILENAME = "a.pdf"
MD5_NEW = hashlib.md5(NEW_BYTES).hexdigest()
MD5_OLD = hashlib.md5(OLD_BYTES).hexdigest()


class FakeStore:
    def __init__(self):
        self.calls = []  # (name, args) 顺序记录
        self.docs = []
        self.upsert_failures = 0

    async def list_docs(self):
        self.calls.append("list_docs")
        return list(self.docs)

    async def delete_doc(self, doc_id):
        self.calls.append(("delete_doc", doc_id))

    async def upsert_chunks(self, chunks, vectors):
        self.calls.append(("upsert", [c["text"] for c in chunks]))
        if self.upsert_failures > 0:
            self.upsert_failures -= 1
            raise RuntimeError("qdrant down")


def fake_parse(parse_map, order=None):
    async def _parse(file_bytes, filename):
        if order is not None:
            order.append(("parse", file_bytes))
        if file_bytes not in parse_map:
            raise ParseError("unexpected bytes")
        result = parse_map[file_bytes]
        if isinstance(result, Exception):
            raise result
        return result

    return _parse


def fake_make_chunks(text, doc_id, filename, file_hash, upload_time):
    return [
        {
            "text": f"{text}-{i}",
            "doc_id": doc_id,
            "filename": filename,
            "file_hash": file_hash,
            "chunk_index": i,
            "upload_time": upload_time,
        }
        for i in range(2)
    ]


def fake_embed(order=None):
    async def _embed(texts):
        if order is not None:
            order.append("embed")
        return [[0.0] * 4 for _ in texts]

    return _embed


@pytest.fixture
def env(tmp_path, monkeypatch):
    store = FakeStore()
    monkeypatch.setattr(kb_service, "vector_store", store)
    monkeypatch.setattr(kb_service.llm_client, "embed", fake_embed())
    monkeypatch.setattr(kb_service.splitter, "make_chunks", fake_make_chunks)
    monkeypatch.setattr(kb_service, "UPLOAD_DIR", str(tmp_path))
    return store, tmp_path


def test_upload_bad_format_raises(env):
    store, _ = env
    with pytest.raises(KbError, match="不支持的文件格式"):
        asyncio.run(kb_service.upload(NEW_BYTES, "a.exe"))
    assert store.calls == []


def test_upload_too_large_raises(env):
    store, _ = env
    with pytest.raises(KbError, match="20MB"):
        asyncio.run(kb_service.upload(b"x" * (20 * 1024 * 1024 + 1), FILENAME))
    assert store.calls == []


def test_upload_duplicate_hash_skipped(env, monkeypatch):
    store, _ = env
    store.docs = [{"doc_id": "d1", "filename": "b.pdf", "file_hash": MD5_NEW, "upload_time": "1"}]
    embed_calls = []

    async def _embed(texts):
        embed_calls.append(texts)
        return []

    monkeypatch.setattr(kb_service.llm_client, "embed", _embed)
    result = asyncio.run(kb_service.upload(NEW_BYTES, FILENAME))
    assert result == {"status": "skipped"}
    assert embed_calls == []
    assert all(not isinstance(c, tuple) for c in store.calls)  # 不调 delete/upsert


def test_upload_same_name_replace_order(env, monkeypatch):
    store, _ = env
    store.docs = [
        {"doc_id": "old-1", "filename": FILENAME, "file_hash": "other", "upload_time": "1"}
    ]
    order = []
    monkeypatch.setattr(kb_service.loader, "parse_file", fake_parse({NEW_BYTES: "t"}, order))
    monkeypatch.setattr(kb_service.llm_client, "embed", fake_embed(order))
    result = asyncio.run(kb_service.upload(NEW_BYTES, FILENAME))
    assert result["status"] == "ok"
    assert result["doc_id"]
    store_seq = [(c[0],) for c in store.calls if isinstance(c, tuple)]
    assert order + store_seq == [
        ("parse", NEW_BYTES),
        "embed",
        ("delete_doc",),
        ("upsert",),
    ]


def test_parse_failure_no_store_writes(env, monkeypatch):
    store, _ = env
    store.docs = [
        {"doc_id": "old-1", "filename": FILENAME, "file_hash": "other", "upload_time": "1"}
    ]
    monkeypatch.setattr(
        kb_service.loader, "parse_file", fake_parse({NEW_BYTES: ParseError("mineru 500")})
    )
    with pytest.raises(KbError, match="解析失败"):
        asyncio.run(kb_service.upload(NEW_BYTES, FILENAME))
    assert all(not isinstance(c, tuple) for c in store.calls)  # delete/upsert 均未调用


def test_upsert_failure_rolls_back_old_doc(env, tmp_path, monkeypatch):
    store, upload_dir = env
    store.docs = [
        {"doc_id": "old-1", "filename": FILENAME, "file_hash": MD5_OLD, "upload_time": "1"}
    ]
    (upload_dir / FILENAME).write_bytes(OLD_BYTES)
    store.upsert_failures = 1
    monkeypatch.setattr(
        kb_service.loader, "parse_file", fake_parse({NEW_BYTES: "new-text", OLD_BYTES: "old-text"})
    )
    with pytest.raises(KbError, match="入库失败"):
        asyncio.run(kb_service.upload(NEW_BYTES, FILENAME))
    assert [c for c in store.calls if isinstance(c, tuple)] == [
        ("delete_doc", "old-1"),
        ("upsert", ["new-text-0", "new-text-1"]),
        ("upsert", ["old-text-0", "old-text-1"]),  # 回滚：旧内容重新入库
    ]
    assert (upload_dir / FILENAME).read_bytes() == OLD_BYTES  # 磁盘未被新文件覆盖


def test_upload_success_writes_file(env, monkeypatch, tmp_path):
    store, upload_dir = env
    monkeypatch.setattr(kb_service.loader, "parse_file", fake_parse({NEW_BYTES: "t"}))
    result = asyncio.run(kb_service.upload(NEW_BYTES, FILENAME))
    assert result["status"] == "ok"
    assert result["doc_id"]
    assert (upload_dir / FILENAME).read_bytes() == NEW_BYTES
    assert [c[0] for c in store.calls if isinstance(c, tuple)] == ["upsert"]


def test_upsert_failure_without_old_file_raises_honestly(env, monkeypatch):
    store, _ = env
    store.upsert_failures = 1
    monkeypatch.setattr(kb_service.loader, "parse_file", fake_parse({NEW_BYTES: "t"}))
    with pytest.raises(KbError, match="入库失败"):
        asyncio.run(kb_service.upload(NEW_BYTES, FILENAME))


def test_delete_ok(env):
    store, _ = env
    result = asyncio.run(kb_service.delete("d1"))
    assert result == {"status": "ok"}
    assert ("delete_doc", "d1") in store.calls


def test_list_docs_passthrough(env):
    store, _ = env
    store.docs = [{"doc_id": "d1", "file_hash": "h"}]
    assert asyncio.run(kb_service.list_docs()) == store.docs


def test_upload_strips_path_traversal(env, monkeypatch, tmp_path):
    store, upload_dir = env
    monkeypatch.setattr(kb_service.loader, "parse_file", fake_parse({NEW_BYTES: "t"}))
    result = asyncio.run(kb_service.upload(NEW_BYTES, "../evil.md"))
    assert result["status"] == "ok"
    # 写盘落在 uploads/ 内，而非目录外
    assert (upload_dir / "evil.md").read_bytes() == NEW_BYTES
    assert not (upload_dir.parent / "evil.md").exists()
