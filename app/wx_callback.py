# -*- coding: utf-8 -*-
"""企业微信回调通路: GET URL 校验 + POST 接收消息 + 秒回 200.

硬性规则: POST 不做任何 RAG 逻辑, 解析后交 message_handler 立即返回 200;
任何异常记日志, 绝不 5xx.
"""
import logging
import re
import xml.etree.ElementTree as ET
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Request, Response
from fastapi.responses import PlainTextResponse

from app.config import settings
from app.wx_crypto import WxCrypto

logger = logging.getLogger(__name__)
router = APIRouter()

# Task 15 接线: 完整消息流水线
from app.message_handler import handle_message

message_handler: Callable[[dict], Awaitable[None]] | None = handle_message

# 群聊 @ 字段在不同模式/版本下命名不一, 逐个尝试, 缺失容错为 []
_AT_FIELD_CANDIDATES = ("atuserlist", "AtUserIdList", "AtList", "at_list", "AtUsers")


def _parse_msg(xml_text: str) -> dict:
    root = ET.fromstring(xml_text)

    def _text(name: str, default: str = "") -> str:
        node = root.find(name)
        return node.text if node is not None and node.text else default

    at_userids: list[str] = []
    for field in _AT_FIELD_CANDIDATES:
        node = root.find(field)
        if node is not None and node.text:
            at_userids = [u for u in re.split(r"[,;\s|]+", node.text.strip()) if u]
            break

    return {
        "msgid": _text("MsgId"),
        "msg_type": _text("MsgType"),
        "from_user": _text("FromUserName"),
        "chat_id": _text("ChatId") or None,
        "content": _text("Content"),
        "at_userids": at_userids,
        # 群文件消息专用 (其余消息为 "")
        "media_id": _text("MediaId"),
        "file_name": _text("FileName"),
    }


@router.get("/wx/callback")
def wx_callback_verify(msg_signature: str = "", timestamp: str = "", nonce: str = "", echostr: str = ""):
    # 明文模式: 企业微信直接原样返回 echostr
    if not settings.WX_AES_KEY:
        return PlainTextResponse(echostr)
    crypto = WxCrypto(settings.WX_TOKEN, settings.WX_AES_KEY)
    if not crypto.verify_signature(timestamp, nonce, echostr, msg_signature):
        logger.warning("wx callback GET: bad signature, remote=%s", nonce)
        return PlainTextResponse("bad signature", status_code=403)
    return PlainTextResponse(crypto.decrypt(echostr))


@router.post("/wx/callback")
async def wx_callback_receive(request: Request):
    try:
        body = (await request.body()).decode("utf-8", errors="replace")
        xml_text = body
        if settings.WX_AES_KEY:
            crypto = WxCrypto(settings.WX_TOKEN, settings.WX_AES_KEY)
            encrypt = ET.fromstring(body).findtext("Encrypt") or ""
            msg_signature = request.query_params.get("msg_signature", "")
            timestamp = request.query_params.get("timestamp", "")
            nonce = request.query_params.get("nonce", "")
            if not crypto.verify_signature(timestamp, nonce, encrypt, msg_signature):
                logger.warning("wx callback POST: bad signature, nonce=%s", nonce)
                return Response(status_code=403)
            xml_text = crypto.decrypt(encrypt)
        msg = _parse_msg(xml_text)
        logger.info(
            "wx callback: msgid=%s type=%s from=%s chat=%s",
            msg["msgid"], msg["msg_type"], msg["from_user"], msg["chat_id"],
        )
        if message_handler:
            await message_handler(msg)
    except Exception:
        logger.exception("wx callback POST failed")
    return Response(status_code=200)
