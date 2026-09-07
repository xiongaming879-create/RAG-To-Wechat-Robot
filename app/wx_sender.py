# -*- coding: utf-8 -*-
"""企业微信 message/send 封装：>1800 字符按段落分片（不拆句子）、失败重试、token 失效刷新."""
import asyncio
import re

import httpx

from app.config import settings
from app.wx_token import wx_access_token

_SEND_URL = "https://qyapi.weixin.qq.com/cgi-bin/message/send"

# 句末标点（句号/感叹/问号/分号），分片只落在这些边界后
_SENTENCE_RE = re.compile(r"(?<=[。！？!?；;])")


def _split_long_paragraph(para: str, max_len: int) -> list[str]:
    """单个段落超长时按句子边界拆；单句仍超长才硬切."""
    pieces: list[str] = []
    buf = ""
    for sent in _SENTENCE_RE.split(para):
        if not sent:
            continue
        if len(sent) > max_len:
            # ponytail: 无标点超长句硬切，聊天场景几乎不会出现
            if buf:
                pieces.append(buf)
                buf = ""
            for i in range(0, len(sent), max_len):
                pieces.append(sent[i:i + max_len])
        elif len(buf) + len(sent) <= max_len:
            buf += sent
        else:
            pieces.append(buf)
            buf = sent
    if buf:
        pieces.append(buf)
    return pieces


def split_content(content: str, max_len: int = 1800) -> list[str]:
    """按段落（\\n）贪心装箱为 ≤max_len 的分片；多片时追加 (1/2) 序号标记."""
    if len(content) <= max_len:
        return [content]
    pieces: list[str] = []
    for para in content.split("\n"):
        if len(para) <= max_len:
            pieces.append(para)
        else:
            pieces.extend(_split_long_paragraph(para, max_len))
    chunks: list[str] = []
    buf = ""
    for piece in pieces:
        if not buf:
            buf = piece
        elif len(buf) + 1 + len(piece) <= max_len:
            buf += "\n" + piece
        else:
            chunks.append(buf)
            buf = piece
    if buf:
        chunks.append(buf)
    n = len(chunks)
    return [f"{c}({i + 1}/{n})" for i, c in enumerate(chunks)]


class WxSender:
    def __init__(
        self,
        token_getter=None,
        client: httpx.AsyncClient | None = None,
        backoff: tuple[float, ...] = (1, 2),
        max_len: int = 1800,
    ):
        self._get_token = token_getter or wx_access_token.get
        self._client = client
        self._backoff = backoff
        self._max_len = max_len

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=10)
        return self._client

    async def _post(self, chat_id: str, content: str, at_userids: list[str] | None, token: str) -> int:
        """发送一条，返回 errcode（网络/HTTP 异常向上抛）."""
        text: dict = {"content": content}
        if at_userids:
            text["mentioned_list"] = at_userids
        resp = await self._ensure_client().post(
            _SEND_URL,
            params={"access_token": token},
            json={"chatid": chat_id, "msgtype": "text", "text": text},
        )
        resp.raise_for_status()
        return resp.json().get("errcode", 0)

    def _force_token_refresh(self) -> None:
        """40014 时让下次 get() 真正重新拉取（绕过未过期的本地缓存）."""
        holder = getattr(self._get_token, "__self__", None)
        if holder is not None and hasattr(holder, "_expire_at"):
            holder._expire_at = 0.0

    async def _send_one(self, chat_id: str, content: str, at_userids: list[str] | None) -> bool:
        """发送单条：首次 + 重试 2 次；40014 刷新 token 后重发一次."""
        for attempt in range(3):
            try:
                token = await self._get_token()
                errcode = await self._post(chat_id, content, at_userids, token)
            except Exception:  # noqa: BLE001 - 网络/HTTP 异常计入重试
                errcode = -1
            if errcode == 0:
                return True
            if errcode == 40014:
                self._force_token_refresh()
                try:
                    token = await self._get_token()
                    return await self._post(chat_id, content, at_userids, token) == 0
                except Exception:  # noqa: BLE001
                    return False
            if attempt < len(self._backoff):
                await asyncio.sleep(self._backoff[attempt])
        return False

    async def send_text(self, chat_id: str, content: str, at_userids: list[str] | None = None) -> bool:
        """发送文本；>1800 自动分片逐条发送，返回是否全部成功."""
        for chunk in split_content(content, self._max_len):
            if not await self._send_one(chat_id, chunk, at_userids):
                return False
        return True


wx_sender = WxSender()
