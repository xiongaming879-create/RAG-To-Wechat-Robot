# -*- coding: utf-8 -*-
"""消息解析: @判定 + 幂等去重 + #kb 指令识别."""
import re
import time

from app.config import settings

# @ 标签形如 "@昵称 "（企业微信把 at 的人以 "@名字 " 插入 content），剔除后 strip；
# 仅剥行首/空白后的 @（避免误伤 "a@b.com" 邮箱），连续多个 @ 标签一次性剥掉
_AT_TAG = re.compile(r"(^|\s)(?:@\S+\s*)+")

# 指令前缀 -> action；匹配要求前缀后为串尾或空格，避免 "#kb:listxxx" 误判
_COMMANDS = {
    "#kb:upload": "upload",
    "#kb:delete": "delete",
    "#kb:list": "list",
}


async def is_at_me(msg: dict) -> bool:
    """基于 at_userids 字段列表判定是否 @ 了机器人（禁止按机器人名称做字符串匹配）。

    WX_BOT_USERID 为空且无 @all 时返回 False。结果写入 msg["is_at_me"]。
    """
    bot = settings.WX_BOT_USERID
    content = msg.get("content") or ""
    # @all 需独立成词："@ally"、"邮箱a@all.com" 不算
    at_all = re.search(r"(^|\s)@all(\s|$)", content) is not None
    result = (bool(bot) and bot in (msg.get("at_userids") or [])) or at_all
    msg["is_at_me"] = result
    return result


def extract_question(msg: dict) -> str:
    """剔除 @ 标签与首尾空格，返回用户实际问题文本。"""
    return _AT_TAG.sub(r"\1", msg.get("content") or "").strip()


class MsgIdDedup:
    """MsgId 内存幂等去重，TTL 默认 10 分钟。首次 True=放行，TTL 内重复 False=拦截。"""

    def __init__(self, ttl: int = 600, now=time.time):
        self._ttl = ttl
        self._now = now
        self._seen: dict[str, float] = {}

    def dedup(self, msgid: str) -> bool:
        now = self._now()
        # ponytail: 全量扫过期条目，O(n) 每次；量级小够用，量大换 heapq
        expired = [k for k, ts in self._seen.items() if now - ts >= self._ttl]
        for k in expired:
            del self._seen[k]
        if msgid in self._seen:
            return False
        self._seen[msgid] = now
        return True


msgid_dedup = MsgIdDedup()


def parse_command(content: str) -> dict | None:
    """识别 #kb 指令，返回 {"action": "upload"|"delete"|"list", "arg": str}，非指令返回 None。"""
    if not content:
        return None
    text = content.strip()
    for prefix, action in _COMMANDS.items():
        if text == prefix:
            return {"action": action, "arg": ""}
        if text.startswith(prefix + " "):
            return {"action": action, "arg": text[len(prefix):].strip()}
    return None


def is_admin(userid: str) -> bool:
    return userid in settings.ADMIN_USER_LIST
