"""Unit tests for Qdrant wrapper. All qdrant-client calls are mocked — no real Qdrant."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from qdrant_client import models

from app.rag import vector_store as vs_module
from app.rag.vector_store import VectorStore

pytestmark = pytest.mark.anyio


def _make_store():
    with patch.object(vs_module, "AsyncQdrantClient") as MockClient:
        store = VectorStore(url="http://fake:6333", collection="test_kb")
    store.client = MockClient.return_value
    store.client.collection_exists = AsyncMock(return_value=False)
    store.client.create_collection = AsyncMock()
    store.client.create_payload_index = AsyncMock()
    store.client.upsert = AsyncMock()
    store.client.delete = AsyncMock()
    store.client.query_points = AsyncMock()
    store.client.scroll = AsyncMock()
    store.client.get_collections = AsyncMock()
    return store


CHUNK = {
    "doc_id": "d1",
    "filename": "a.pdf",
    "file_hash": "h1",
    "chunk_index": 0,
    "upload_time": "2026-01-01T00:00:00",
}


@pytest.mark.anyio
async def test_ensure_collection_creates_with_dim_cosine_and_payload_index():
    store = _make_store()
    store.client.collection_exists.return_value = False

    await store.ensure_collection()

    store.client.create_collection.assert_awaited_once()
    kwargs = store.client.create_collection.await_args.kwargs
    vectors_config = kwargs["vectors_config"]
    assert vectors_config.size == 1024
    assert vectors_config.distance == models.Distance.COSINE
    # doc_id keyword payload index
    store.client.create_payload_index.assert_awaited_once()
    idx_kwargs = store.client.create_payload_index.await_args.kwargs
    assert idx_kwargs["field_name"] == "doc_id"
    assert idx_kwargs["field_schema"] == models.PayloadSchemaType.KEYWORD


@pytest.mark.anyio
async def test_ensure_collection_idempotent_when_exists():
    store = _make_store()
    store.client.collection_exists.return_value = True

    await store.ensure_collection()

    store.client.create_collection.assert_not_awaited()
    store.client.create_payload_index.assert_not_awaited()


@pytest.mark.anyio
async def test_upsert_chunks_pairs_vector_and_payload():
    store = _make_store()
    chunks = [dict(CHUNK), dict(CHUNK, chunk_index=1)]
    vectors = [[0.1] * 4, [0.2] * 4]

    await store.upsert_chunks(chunks, vectors)

    store.client.upsert.assert_awaited_once()
    kwargs = store.client.upsert.await_args.kwargs
    points = kwargs["points"]
    assert len(points) == 2
    assert points[0].vector == [0.1] * 4
    assert points[0].payload["doc_id"] == "d1"
    assert points[0].payload["chunk_index"] == 0
    assert points[1].payload["chunk_index"] == 1
    assert points[1].vector == [0.2] * 4
    assert points[0].id != points[1].id


@pytest.mark.anyio
async def test_upsert_chunks_length_mismatch_raises():
    store = _make_store()

    with pytest.raises(ValueError):
        await store.upsert_chunks([dict(CHUNK)], [])

    store.client.upsert.assert_not_awaited()


@pytest.mark.anyio
async def test_delete_doc_uses_doc_id_filter():
    store = _make_store()

    await store.delete_doc("d1")

    store.client.delete.assert_awaited_once()
    kwargs = store.client.delete.await_args.kwargs
    selector = kwargs["points_selector"]
    cond = selector.filter.must[0]
    assert cond.key == "doc_id"
    assert cond.match.value == "d1"


@pytest.mark.anyio
async def test_search_returns_payload_score_id():
    store = _make_store()
    hit = models.ScoredPoint(
        id="p1", version=1, score=0.87, payload=dict(CHUNK), vector=None
    )
    store.client.query_points.return_value = SimpleNamespace(points=[hit])

    results = await store.search([0.5] * 4, top_k=15)

    assert results == [{"payload": CHUNK, "score": 0.87, "id": "p1"}]
    kwargs = store.client.query_points.await_args.kwargs
    assert kwargs["limit"] == 15
    assert kwargs["query"] == [0.5] * 4


@pytest.mark.anyio
async def test_search_default_top_k():
    store = _make_store()
    store.client.query_points.return_value = SimpleNamespace(points=[])

    await store.search([0.5] * 4)

    assert store.client.query_points.await_args.kwargs["limit"] == 15


@pytest.mark.anyio
async def test_list_docs_aggregates_by_doc_id():
    store = _make_store()
    points = [
        models.ScoredPoint(
            id=str(i),
            version=1,
            score=1.0,
            payload=dict(CHUNK, chunk_index=i),
            vector=None,
        )
        for i in range(3)
    ]
    points.append(
        models.ScoredPoint(
            id="x",
            version=1,
            score=1.0,
            payload=dict(CHUNK, doc_id="d2", filename="b.pdf", file_hash="h2"),
            vector=None,
        )
    )
    # scroll returns (points, next_offset); one page then done
    store.client.scroll.side_effect = [(points, None)]

    docs = await store.list_docs()

    assert len(docs) == 2
    d1 = next(d for d in docs if d["doc_id"] == "d1")
    assert d1["filename"] == "a.pdf"
    assert d1["file_hash"] == "h1"
    assert d1["upload_time"] == "2026-01-01T00:00:00"
    assert d1["chunk_count"] == 3
    d2 = next(d for d in docs if d["doc_id"] == "d2")
    assert d2["chunk_count"] == 1


@pytest.mark.anyio
async def test_list_docs_paginates_scroll():
    store = _make_store()
    p1 = models.ScoredPoint(id="1", version=1, score=1.0, payload=dict(CHUNK), vector=None)
    p2 = models.ScoredPoint(id="2", version=1, score=1.0, payload=dict(CHUNK), vector=None)
    store.client.scroll.side_effect = [([p1], "cursor"), ([p2], None)]

    docs = await store.list_docs()

    assert store.client.scroll.await_count == 2
    assert len(docs) == 1
    assert docs[0]["chunk_count"] == 2


@pytest.mark.anyio
async def test_is_alive_true_when_healthy():
    store = _make_store()
    store.client.get_collections.return_value = object()

    assert await store.is_alive() is True


@pytest.mark.anyio
async def test_is_alive_false_on_exception():
    store = _make_store()
    store.client.get_collections.side_effect = RuntimeError("conn refused")

    assert await store.is_alive() is False
