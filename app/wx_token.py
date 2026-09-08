# -*- coding: utf-8 -*-
"""企业微信 access_token 内存缓存：提前 10 分钟刷新，全局锁防并发重复请求."""
import asyncio
import time

import httpx

from app.config import settings

_GETTOKEN_URL = "https://qyapi.weixin.qq.com/cgi-bin/gettoken"


class WxAccessToken:
    def __init__(
        self,
        corp_id: str,
        secret: str,
        client: httpx.AsyncClient | None = None,
        backoff: tuple[float, ...] = (1, 2),
    ):
        self._corp_id = corp_id
        self._secret = secret
        self._client = client
        self._backoff = backoff
        self._lock = asyncio.Lock()
        self._token = ""
        # 提前 10 分钟刷新：expire_at = now + (expires_in - 600)
        self._expire_at = 0.0

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=10)
        return self._client

    async def _fetch(self) -> tuple[str, float]:
        resp = await self._ensure_client().get(
            _GETTOKEN_URL,
            params={"corpid": self._corp_id, "corpsecret": self._secret},
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("errcode", 0) != 0:
            raise RuntimeError(f"gettoken 失败: errcode={data.get('errcode')} errmsg={data.get('errmsg')}")
        return data["access_token"], data.get("expires_in", 7200)

    def invalidate(self) -> None:
        """使缓存的 token 立即失效（errcode 40014 时调用），下次 get() 重新拉取."""
        self._expire_at = 0.0

    async def get(self) -> str:
        async with self._lock:
            if self._token and time.monotonic() < self._expire_at:
                return self._token
            last_err: Exception | None = None
            for attempt in range(3):
                try:
                    token, expires_in = await self._fetch()
                    self._token = token
                    self._expire_at = time.monotonic() + max(expires_in - 600, 0)
                    return self._token
                except Exception as e:  # noqa: BLE001 - 网络/HTTP/errcode 均重试
                    last_err = e
                    if attempt < len(self._backoff):
                        await asyncio.sleep(self._backoff[attempt])
            raise RuntimeError(f"获取 access_token 失败（已重试 2 次）: {last_err}")


wx_access_token = WxAccessToken(settings.WX_CORP_ID, settings.WX_SECRET)
