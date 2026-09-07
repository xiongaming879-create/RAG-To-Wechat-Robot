"""知识库管理服务：上传/删除/列表（admin API 与群指令共用的三条路径）。

同名替换原子性：parse 新文件 → embed → delete_doc(旧) → upsert 新；
入库失败时用操作前读入内存的旧字节回滚；磁盘 uploads/ 仅在成功后写入。
"""
import hashlib
import logging
import os
import time
import uuid

from app.rag import loader, splitter
from app.rag.llm_client import llm_client
from app.rag.vector_store import vector_store

logger = logging.getLogger(__name__)

ALLOWED_EXTS = {"pdf", "docx", "md", "txt"}
MAX_FILE_SIZE = 20 * 1024 * 1024
UPLOAD_DIR = "uploads"  # ponytail: config 无 UPLOAD_DIR 字段，按项目约定用工作目录相对路径


class KbError(Exception):
    """知识库操作失败，reason 为中文原因。"""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


async def upload(file_bytes: bytes, filename: str) -> dict:
    ext = os.path.splitext(filename)[1].lstrip(".").lower()
    if ext not in ALLOWED_EXTS:
        raise KbError(f"不支持的文件格式: {ext or filename}，仅支持 {sorted(ALLOWED_EXTS)}")
    if len(file_bytes) > MAX_FILE_SIZE:
        raise KbError("文件超过 20MB 上限")
    file_hash = hashlib.md5(file_bytes).hexdigest()

    docs = await vector_store.list_docs()
    if any(d["file_hash"] == file_hash for d in docs):
        return {"status": "skipped"}

    # 同名旧文档：操作前把旧字节读入内存（回滚备用；磁盘在成功前不动）
    old_doc = next((d for d in docs if d["filename"] == filename), None)
    old_bytes = None
    if old_doc:
        old_path = os.path.join(UPLOAD_DIR, filename)
        if os.path.exists(old_path):
            with open(old_path, "rb") as f:
                old_bytes = f.read()

    doc_id = str(uuid.uuid4())
    try:
        text = await loader.parse_file(file_bytes, filename)
        chunks = splitter.make_chunks(text, doc_id, filename, file_hash, str(int(time.time())))
        vectors = await llm_client.embed([c["text"] for c in chunks])
    except Exception as e:
        raise KbError(f"解析失败: {e}") from e

    try:
        if old_doc:
            await vector_store.delete_doc(old_doc["doc_id"])
        await vector_store.upsert_chunks(chunks, vectors)
    except Exception as e:
        if old_doc:
            await _rollback(old_doc, filename, old_bytes)
        raise KbError(f"入库失败: {e}") from e

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    with open(os.path.join(UPLOAD_DIR, filename), "wb") as f:
        f.write(file_bytes)
    return {"status": "ok", "doc_id": doc_id}


async def _rollback(old_doc: dict, filename: str, old_bytes: bytes | None) -> None:
    """旧字节 re-parse → re-embed → re-upsert 回滚；失败只记日志，如实上抛。"""
    try:
        if old_bytes is None:
            # ponytail: 旧文件不在磁盘（如被手工删除），无法回滚，只能报错让管理员重传
            raise RuntimeError("旧文件字节不可用")
        text = await loader.parse_file(old_bytes, filename)
        chunks = splitter.make_chunks(
            text, old_doc["doc_id"], filename, old_doc["file_hash"], old_doc["upload_time"]
        )
        vectors = await llm_client.embed([c["text"] for c in chunks])
        await vector_store.upsert_chunks(chunks, vectors)
        with open(os.path.join(UPLOAD_DIR, filename), "wb") as f:
            f.write(old_bytes)
    except Exception:
        logger.exception("回滚失败，旧文档可能已丢失，请管理员重新上传原文件: %s", filename)


async def delete(doc_id: str) -> dict:
    try:
        await vector_store.delete_doc(doc_id)
    except Exception as e:
        raise KbError(f"删除失败: {e}") from e
    return {"status": "ok"}


async def list_docs() -> list[dict]:
    try:
        return await vector_store.list_docs()
    except Exception as e:
        raise KbError(f"查询文档列表失败: {e}") from e
