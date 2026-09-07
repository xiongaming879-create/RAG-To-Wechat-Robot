"""Unit tests for hybrid retriever. vector_store / llm_client fully mocked — no real services."""
import pytest

from app.rag import retriever

pytestmark = pytest.mark.anyio


def patch_clients(monkeypatch, search_scores, rerank_scores):
    """search_scores: list of cosine scores for the 15-hit recall. rerank_scores: score per surviving doc."""
    calls = {"search": [], "rerank": [], "embed": []}

    async def fake_embed(texts):
        calls["embed"].append(texts)
        return [[0.1] * 4]

    async def fake_search(vec, top_k=15):
        calls["search"].append({"vec": vec, "top_k": top_k})
        return [
            {
                "id": f"p-{i}",
                "score": s,
                "payload": {
                    "text": f"chunk-{i}",
                    "filename": f"doc{i}.pdf",
                    "chunk_index": i,
                },
            }
            for i, s in enumerate(search_scores)
        ]

    async def fake_rerank(query, docs):
        calls["rerank"].append({"query": query, "docs": docs})
        return rerank_scores

    monkeypatch.setattr(retriever.llm_client, "embed", fake_embed)
    monkeypatch.setattr(retriever.vector_store, "search", fake_search)
    monkeypatch.setattr(retriever.llm_client, "rerank", fake_rerank)
    return calls


async def test_wide_recall_15_and_vector_threshold_filter(monkeypatch):
    """15 条召回中 3 条低于 0.60 被过滤，rerank 只收到剩余 12 条；search 用 top_k=15。"""
    scores = [0.95 - i * 0.01 for i in range(12)] + [0.59, 0.40, 0.10]
    calls = patch_clients(monkeypatch, scores, [0.9] * 12)

    result = await retriever.retrieve("q")
    assert calls["search"][0]["top_k"] == 15
    assert len(calls["rerank"][0]["docs"]) == 12
    assert calls["rerank"][0]["docs"][0] == "chunk-0"
    assert result is not None


async def test_rerank_drops_below_half_and_truncates_top4(monkeypatch):
    """重排 <0.5 丢弃，按得分降序取 Top 4。"""
    vec_scores = [0.9] * 12
    rerank_scores = [0.6, 0.2, 0.95, 0.4, 0.8, 0.1, 0.55, 0.3, 0.45, 0.7, 0.49, 0.51]
    patch_clients(monkeypatch, vec_scores, rerank_scores)

    result = await retriever.retrieve("q")
    assert result is not None
    assert [r["score"] for r in result] == [0.95, 0.8, 0.7, 0.6]  # 降序 Top4
    assert [r["text"] for r in result] == ["chunk-2", "chunk-4", "chunk-9", "chunk-0"]
    assert all(
        r["filename"] == f"doc{i}.pdf" and r["chunk_index"] == i
        for i, r in zip([2, 4, 9, 0], result)
    )


async def test_all_rerank_scores_below_threshold_returns_none(monkeypatch):
    """重排最高分 <0.5 → None。"""
    patch_clients(monkeypatch, [0.9, 0.8, 0.7], [0.3, 0.49, 0.1])
    assert await retriever.retrieve("q") is None


async def test_all_below_vector_threshold_returns_none(monkeypatch):
    """全部低于 0.60 → None，且不调 rerank。"""
    calls = patch_clients(monkeypatch, [0.5, 0.3], [0.9, 0.9])
    assert await retriever.retrieve("q") is None
    assert calls["rerank"] == []


async def test_empty_recall_returns_none_and_skips_rerank(monkeypatch):
    """空召回 → None，且不调 rerank。"""
    calls = patch_clients(monkeypatch, [], [0.9])
    assert await retriever.retrieve("q") is None
    assert calls["rerank"] == []
    assert calls["embed"] == [["q"]]
