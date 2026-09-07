"""混合检索：向量宽召回 Top15 → 余弦阈值 0.60 → rerank 精筛 → Top 4。

来源标注用 filename + chunk_index（payload 暂无 page 字段，接 MinerU 页码时再加）。
"""
from app.rag.llm_client import llm_client
from app.rag.vector_store import vector_store

VECTOR_THRESHOLD = 0.60
RERANK_THRESHOLD = 0.5
RECALL_TOP_K = 15
FINAL_TOP_K = 4


async def retrieve(question: str) -> list[dict] | None:
    """返回 [{"text", "filename", "chunk_index", "score"}] 按重排分降序，最多 4 条；无相关内容返回 None。"""
    vec = (await llm_client.embed([question]))[0]
    hits = [h for h in await vector_store.search(vec, top_k=RECALL_TOP_K)
            if h["score"] >= VECTOR_THRESHOLD]
    if not hits:
        return None
    rerank_scores = await llm_client.rerank(
        question, [h["payload"]["text"] for h in hits]
    )
    ranked = sorted(
        zip(rerank_scores, hits), key=lambda pair: pair[0], reverse=True
    )
    if ranked[0][0] < RERANK_THRESHOLD:
        return None
    return [
        {
            "text": h["payload"]["text"],
            "filename": h["payload"]["filename"],
            "chunk_index": h["payload"]["chunk_index"],
            "score": s,
        }
        for s, h in ranked
        if s >= RERANK_THRESHOLD
    ][:FINAL_TOP_K]
