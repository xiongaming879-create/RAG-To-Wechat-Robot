# -*- coding: utf-8 -*-
"""消息处理流水线: 去重 → @判定 → 限流 → 管理员指令 / 引导语 / 问答入队.

回调侧只做轻量路由, 问答的重活 (chain.answer) 在队列 worker 里跑 (process_question).
"""
import asyncio
import logging
import time
from collections import deque

from app.message_parser import extract_question, is_admin, is_at_me, msgid_dedup, parse_command
from app.queue.task_queue import task_queue
from app.rag import chain, kb_service
from app.rag.llm_client import LLMClientError
from app.wx_sender import wx_sender

logger = logging.getLogger(__name__)

# 限流: 单用户 5 次/分钟, 单群 2 条/秒 (内存滑动窗口)
USER_LIMIT, USER_WINDOW = 5, 60.0
CHAT_LIMIT, CHAT_WINDOW = 2, 1.0

PENDING_TTL = 60  # 管理员待传文件缓存 60s


class SlidingWindowLimiter:
    def __init__(self, limit: int, window: float, now=time.monotonic):
        self._limit = limit
        self._window = window
        self._now = now
        self._hits: dict[str, deque[float]] = {}

    def allow(self, key: str, charge: bool = True) -> bool:
        """charge=False 为只探测不记账（多限流维度统一记账用）。"""
        now = self._now()
        hits = self._hits.setdefault(key, deque())
        while hits and now - hits[0] >= self._window:
            hits.popleft()
        if len(hits) >= self._limit:
            return False
        if charge:
            hits.append(now)
        return True


user_limiter = SlidingWindowLimiter(USER_LIMIT, USER_WINDOW)
chat_limiter = SlidingWindowLimiter(CHAT_LIMIT, CHAT_WINDOW)

# 管理员待传文件: key=from_user -> {media_id, file_name, ts}
pending_uploads: dict[str, dict] = {}


def stash_pending_file(user: str, media_id: str, file_name: str) -> None:
    pending_uploads[user] = {"media_id": media_id, "file_name": file_name, "ts": time.time()}


def pop_pending_file(user: str) -> dict | None:
    item = pending_uploads.pop(user, None)
    if item is None or time.time() - item["ts"] > PENDING_TTL:
        return None
    return item


async def _chat_id(msg: dict) -> str:
    return msg.get("chat_id") or msg.get("from_user") or ""


async def _run_command(msg: dict, command: dict) -> None:
    """管理员 #kb 指令 → kb_service → @管理员回执结果."""
    chat_id = await _chat_id(msg)
    user = msg.get("from_user") or ""
    action, arg = command["action"], command["arg"]
    try:
        if action == "list":
            docs = await kb_service.list_docs()
            text = "\n".join(f"{d['filename']}（{d.get('chunk_count', '?')} 块）" for d in docs) or "知识库为空"
        elif action == "delete":
            if not arg:
                text = "用法: #kb:delete 文件名"
            else:
                docs = await kb_service.list_docs()
                doc = next((d for d in docs if d["filename"] == arg), None)
                if doc is None:
                    text = f"未找到文件: {arg}"
                else:
                    await kb_service.delete(doc["doc_id"])
                    text = f"已删除: {arg}"
        elif action == "upload":
            pending = pop_pending_file(user)
            if pending is None:
                text = "请先发送要上传的文件"
            else:
                file_bytes = await wx_sender.download_media(pending["media_id"])
                await kb_service.upload(file_bytes, pending["file_name"])
                text = f"已上传: {pending['file_name']}"
        else:  # pragma: no cover - parse_command 只产出以上三种
            text = f"未知指令: {action}"
    except kb_service.KbError as e:
        text = f"操作失败: {e.reason}"
    except Exception:
        logger.exception("kb 指令执行失败 action=%s arg=%s user=%s", action, arg, user)
        text = "操作失败，请查看服务日志"
    await wx_sender.send_text(chat_id, text, at_userids=[user] if user else None)


async def handle_message(msg: dict) -> None:
    """回调注入的完整流水线入口；任何异常只记日志，绝不向外抛."""
    try:
        msgid = msg.get("msgid") or ""
        if not msgid or not msgid_dedup.dedup(msgid):
            return

        # 群文件消息: 管理员的存待传缓存, 其余丢弃; 一律不回复
        if msg.get("msg_type") == "file":
            if msg.get("media_id") and is_admin(msg.get("from_user") or ""):
                stash_pending_file(msg["from_user"], msg["media_id"], msg.get("file_name") or "")
            return

        if not await is_at_me(msg):
            return

        # 限流: 超限丢弃不回复; 先探测后记账, 任一维度超限则两侧都不扣额度
        user = msg.get("from_user") or ""
        chat_key = msg.get("chat_id") or user
        if not (user_limiter.allow(user, charge=False) and chat_limiter.allow(chat_key, charge=False)):
            logger.info("限流丢弃 user=%s chat=%s msgid=%s", user, chat_key, msgid)
            return
        user_limiter.allow(user)
        chat_limiter.allow(chat_key)

        question = extract_question(msg)
        command = parse_command(question)
        if command:
            if not is_admin(user):
                return  # 非管理员指令静默丢弃
            await _run_command(msg, command)
            return

        if not question:
            await wx_sender.send_text(await _chat_id(msg), "请问有什么可以帮您？")
            return

        result = await task_queue.put(process_question, msg)
        if not result.get("ok"):
            # 队列满(积压保护): 按 spec 回复当前咨询量话术
            logger.warning("队列积压拒绝 msgid=%s: %s", msgid, result.get("msg"))
            await wx_sender.send_text(await _chat_id(msg), result.get("msg") or "当前咨询量较大，请稍后再试")
    except Exception:
        logger.exception("handle_message 处理失败 msg=%r", msg)


async def process_question(msg: dict) -> None:
    """队列 worker 执行: chain.answer → send_text 分片发送.

    LLM 失败向上抛 → 队列重试, 重试耗尽后 on_queue_failure 回"服务繁忙";
    向量库等其他失败 → 直接回"知识库维护中"。
    """
    chat_id = await _chat_id(msg)
    question = extract_question(msg)
    try:
        reply = await chain.answer(question, chat_id, msg.get("from_user") or "")
    except LLMClientError:
        # LLM 失败向上抛 → 队列重试, 耗尽后 on_queue_failure 回"服务繁忙"
        logger.warning("LLM 调用失败 chat=%s question=%r", chat_id, question[:50])
        raise
    except Exception:
        # 向量库等其他失败: 不重试, 直接回维护中
        logger.exception("问答失败 chat=%s question=%r", chat_id, question[:50])
        await wx_sender.send_text(chat_id, "知识库维护中")
        return
    await wx_sender.send_text(chat_id, reply)


# 防 fire-and-forget task 被 GC（官方推荐模式）
_background_tasks: set[asyncio.Task] = set()


def on_queue_failure(payload: dict, reason: str) -> None:
    """队列 on_failure 同步回调: 回"服务繁忙，请稍后再试"."""
    logger.warning("任务最终失败 reason=%s payload=%r", reason, payload)

    async def _reply():
        try:
            chat_id = payload.get("chat_id") or payload.get("from_user") or ""
            await wx_sender.send_text(chat_id, "服务繁忙，请稍后再试")
        except Exception:
            logger.exception("回复服务繁忙失败 payload=%r", payload)

    task = asyncio.get_running_loop().create_task(_reply())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


task_queue.on_failure = on_queue_failure
