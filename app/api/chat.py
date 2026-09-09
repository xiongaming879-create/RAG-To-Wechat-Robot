# -*- coding: utf-8 -*-
"""Web 问答 API：本地测试检索效果，固定 chat_id/user_id="web"，同步等 LLM，无鉴权无限流。"""
import logging
import time

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.rag import chain
from app.rag.llm_client import LLMClientError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")

CHAT_ID = "web"
USER_ID = "web"


@router.post("/chat")
async def chat(body: dict) -> JSONResponse:
    question = str(body.get("question") or "").strip() if isinstance(body, dict) else ""
    if not question:
        return JSONResponse(status_code=400, content={"error": "question 不能为空"})

    start = time.perf_counter()
    try:
        result = await chain.answer_with_sources(question, CHAT_ID, USER_ID)
    except LLMClientError as e:
        logger.warning("web chat LLM 失败: %s", e)
        return JSONResponse(status_code=502, content={"error": "服务繁忙，请稍后再试"})
    except Exception:
        logger.exception("web chat 未预期异常")
        return JSONResponse(status_code=500, content={"error": "服务器内部错误"})

    elapsed_ms = int((time.perf_counter() - start) * 1000)
    return JSONResponse(
        content={
            "answer": result["answer"],
            "sources": result["sources"],
            "elapsed_ms": elapsed_ms,
        }
    )
