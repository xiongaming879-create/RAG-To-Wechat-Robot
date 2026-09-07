# -*- coding: utf-8 -*-
"""管理 API：Token 鉴权 + 知识库上传/删除/列表。

鉴权：Header X-Admin-Token 常量时间比对 settings.ADMIN_API_TOKEN，无效 401。
错误带具体中文原因（KbError.reason）；未捕获异常 500 + 日志，不静默。
"""
import hmac
import logging

from fastapi import APIRouter, File, Header, HTTPException, Query, UploadFile

from app.config import settings
from app.rag import kb_service
from app.rag.kb_service import KbError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin")


def _require(token: str | None) -> None:
    configured = settings.ADMIN_API_TOKEN
    if not configured or not token or not hmac.compare_digest(configured, token):
        raise HTTPException(status_code=401, detail="无效的管理 Token")


@router.post("/upload")
async def upload(
    file: UploadFile = File(...),
    x_admin_token: str | None = Header(default=None),
) -> dict:
    _require(x_admin_token)
    data = await file.read()
    try:
        return await kb_service.upload(data, file.filename or "")
    except KbError as e:
        raise HTTPException(status_code=400, detail=e.reason) from e
    except Exception:
        logger.exception("上传处理失败: %s", file.filename)
        raise HTTPException(status_code=500, detail="服务器内部错误") from None


@router.delete("/doc/{doc_id}")
async def delete_doc(
    doc_id: str, x_admin_token: str | None = Header(default=None)
) -> dict:
    _require(x_admin_token)
    docs = await _list_docs_or_400()
    if not any(d["doc_id"] == doc_id for d in docs):
        raise HTTPException(status_code=404, detail=f"文档不存在: {doc_id}")
    try:
        return await kb_service.delete(doc_id)
    except KbError as e:
        raise HTTPException(status_code=400, detail=e.reason) from e
    except Exception:
        logger.exception("删除文档失败: %s", doc_id)
        raise HTTPException(status_code=500, detail="服务器内部错误") from None


@router.get("/docs")
async def list_docs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1),
    x_admin_token: str | None = Header(default=None),
) -> dict:
    _require(x_admin_token)
    docs = await _list_docs_or_400()
    start = (page - 1) * page_size
    return {
        "items": docs[start : start + page_size],
        "total": len(docs),
        "page": page,
        "page_size": page_size,
    }


async def _list_docs_or_400() -> list[dict]:
    try:
        return await kb_service.list_docs()
    except KbError as e:
        raise HTTPException(status_code=400, detail=e.reason) from e
    except Exception:
        logger.exception("查询文档列表失败")
        raise HTTPException(status_code=500, detail="服务器内部错误") from None
