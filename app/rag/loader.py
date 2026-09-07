"""MinerU 文档解析：所有格式统一走 MinerU API，不做本地解析。"""

import httpx

from app.config import settings


class ParseError(Exception):
    """MinerU 解析失败。reason 说明原因，本层不触碰向量库。"""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


# MinerU 响应里提取文本的可能字段（API 地址为占位，字段做兼容）
_TEXT_KEYS = ("text", "content", "markdown")

_TIMEOUT = 10.0
_RETRIES = 2  # 失败后重试 2 次（共最多 3 次）


def _extract_text(data) -> str:
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        for key in _TEXT_KEYS:
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value
    raise ParseError(f"MinerU 响应中未找到提取文本: {str(data)[:200]}")


async def parse_file(
    file_bytes: bytes, filename: str, client: httpx.AsyncClient | None = None
) -> str:
    """POST MinerU API（multipart 上传），返回提取的纯文本。失败 raise ParseError。"""
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=_TIMEOUT)
    try:
        last_reason = "unknown"
        for attempt in range(1 + _RETRIES):
            try:
                resp = await client.post(
                    settings.MINERU_API_URL,
                    files={"file": (filename, file_bytes)},
                )
                if resp.is_success:
                    return _extract_text(resp.json())
                last_reason = f"MinerU HTTP {resp.status_code}: {resp.text[:200]}"
            except (httpx.HTTPError, ValueError) as e:  # 网络/超时/JSON 解码
                last_reason = f"MinerU 请求失败: {e}"
        raise ParseError(last_reason)
    finally:
        if own_client:
            await client.aclose()
