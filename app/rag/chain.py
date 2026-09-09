"""RAG 问答链：历史上下文辅助检索 → 无结果固定话术不调 chat → 组 Prompt 带来源 → chat → 写回上下文。"""
from app.context import context_store
from app.rag import retriever
from app.rag.llm_client import llm_client

# spec 565-571 行原文，硬编码
SYSTEM_PROMPT = """你是内部知识库问答助手，只能根据给定的知识库片段回答用户问题。
1. 严格仅使用提供的知识库内容作答
2. 如果知识库内容不足以回答，直接回复：【知识库暂无相关信息】
3. 禁止编造、拓展、联想外部知识
4. 回答简洁、准确、贴合原文"""

FALLBACK = "【知识库暂无相关信息】"


async def answer_with_sources(question: str, chat_id: str, user_id: str) -> dict:
    """返回 {"answer": str, "sources": [{"filename","chunk_index","score"}...]}。"""
    history = context_store.get(chat_id, user_id)
    # 检索前带历史上下文优化查询
    query = "\n".join(f"{m['role']}：{m['content']}" for m in history)
    query = f"{query}\n{question}" if query else question

    chunks = await retriever.retrieve(query)
    if not chunks:
        # 禁止编造：检索不到不调 chat
        return {"answer": FALLBACK, "sources": []}

    blocks = "\n\n".join(
        f"[来源：{c['filename']}（第 {c['chunk_index']} 块）]\n{c['text']}"
        for c in chunks
    )
    history_text = "\n".join(f"{m['role']}：{m['content']}" for m in history)
    user_msg = (
        (f"历史对话：\n{history_text}\n\n" if history_text else "")
        + f"知识库片段：\n{blocks}\n\n用户问题：{question}"
    )
    reply = await llm_client.chat(SYSTEM_PROMPT, user_msg)
    context_store.append(chat_id, user_id, question, reply)
    return {
        "answer": reply,
        "sources": [
            {
                "filename": c["filename"],
                "chunk_index": c["chunk_index"],
                "score": c["score"],
            }
            for c in chunks
        ],
    }


async def answer(question: str, chat_id: str, user_id: str) -> str:
    return (await answer_with_sources(question, chat_id, user_id))["answer"]
