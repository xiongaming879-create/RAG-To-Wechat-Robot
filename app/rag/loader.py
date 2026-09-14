"""MinerU 文档解析：md/txt 本地解码，其余走 MinerU 官方异步任务 API（mineru.net）。

流程（实测确认）：POST /api/v4/file-urls/batch 拿 batch_id + OSS 预签名 PUT 地址
→ PUT 文件字节（不带 Authorization）→ 轮询 /api/v4/extract-results/batch/{batch_id}
→ state=done 后下载 full_zip_url，取 zip 中最大的 .md。
"""

import asyncio
import io
import zipfile

import httpx

from app.config import settings


class ParseError(Exception):
    """MinerU 解析失败。reason 说明原因，本层不触碰向量库。"""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


# md/txt 纯文本本地解码（MinerU 不支持）；其余白名单走 MinerU
_TEXT_EXTS = {"md", "markdown", "txt"}
_MINERU_EXTS = {"pdf", "doc", "docx", "ppt", "pptx", "png", "jpg", "jpeg"}

_TIMEOUT = 10.0  # 单次 HTTP 请求超时（轮询响应很小，够用）
_UPLOAD_TIMEOUT = 120.0  # 文件 PUT 上传与 zip 下载，不受 10s 通用超时约束
_RETRIES = 2  # 失败后重试 2 次（共最多 3 次）
_POLL_INTERVAL = 2.0
_POLL_TIMEOUT = 120.0


def _decode_text(file_bytes: bytes) -> str:
    try:
        return file_bytes.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return file_bytes.decode("gbk")
        except UnicodeDecodeError:
            raise ParseError("md/txt 文件既不是 UTF-8 也不是 GBK 编码")


async def _send_with_retry(
    client: httpx.AsyncClient, method: str, url: str, **kwargs
) -> httpx.Response:
    """网络错误/5xx 重试 _RETRIES 次（沿用旧 loader 重试风格）；4xx 直接返回。"""
    last = "unknown"
    for _ in range(1 + _RETRIES):
        try:
            resp = await client.request(method, url, **kwargs)
            if resp.status_code < 500:
                return resp
            last = f"MinerU HTTP {resp.status_code}: {resp.text[:200]}"
        except httpx.HTTPError as e:
            last = f"MinerU 请求失败: {e}"
    raise ParseError(last)


def _auth_headers() -> dict:
    if not settings.MINERU_API_TOKEN:
        raise ParseError("MINERU_API_TOKEN 未配置")
    return {"Authorization": f"Bearer {settings.MINERU_API_TOKEN}"}


async def _create_batch(client: httpx.AsyncClient, filename: str) -> tuple[str, str]:
    """申请上传地址，返回 (batch_id, OSS 预签名 PUT URL)。"""
    url = f"{settings.MINERU_API_URL}/api/v4/file-urls/batch"
    payload = {
        "enable_formula": False,
        "language": "ch",
        "files": [{"name": filename, "is_ocr": False, "data": ""}],
    }
    resp = await _send_with_retry(
        client, "POST", url, json=payload, headers=_auth_headers()
    )
    if not resp.is_success:
        raise ParseError(f"MinerU HTTP {resp.status_code}: {resp.text[:200]}")
    body = resp.json()
    if body.get("code") != 0:
        # 业务错误（如 unsupported file type）重试无意义，直接失败
        raise ParseError(f"MinerU API code {body.get('code')}: {body.get('msg')}")
    data = body["data"]
    try:
        return data["batch_id"], data["file_urls"][0]
    except (KeyError, IndexError, TypeError):
        raise ParseError(f"MinerU 响应缺少 batch_id/file_urls: {str(data)[:200]}")


async def _wait_done(client: httpx.AsyncClient, batch_id: str) -> str:
    """轮询直到 done，返回结果 zip 下载地址。"""
    url = f"{settings.MINERU_API_URL}/api/v4/extract-results/batch/{batch_id}"
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _POLL_TIMEOUT
    last = "unknown"
    while loop.time() < deadline:
        try:
            resp = await client.get(url, headers=_auth_headers())
            if resp.is_success:
                body = resp.json()
                if body.get("code") != 0:
                    raise ParseError(
                        f"MinerU API code {body.get('code')}: {body.get('msg')}"
                    )
                items = body["data"]["extract_result"]
                if not items:
                    raise ParseError("MinerU 响应 extract_result 为空")
                item = items[0]  # 每次只提交一个文件
                state = item.get("state")
                if state == "done":
                    zip_url = item.get("full_zip_url")
                    if not zip_url:
                        raise ParseError("MinerU state=done 但缺少 full_zip_url")
                    return zip_url
                if state in ("failed", "error"):
                    raise ParseError(f"MinerU 解析失败: {item.get('err_msg') or state}")
                last = f"state={state}"
            else:
                last = f"MinerU HTTP {resp.status_code}"
        except (httpx.HTTPError, ValueError) as e:
            last = f"MinerU 请求失败: {e}"  # 轮询中的瞬时错误不终止，等下一轮
        await asyncio.sleep(_POLL_INTERVAL)
    raise ParseError(f"MinerU 解析超时（{_POLL_TIMEOUT}s）: {last}")


def _extract_md_from_zip(zip_bytes: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
            mds = [i for i in z.infolist() if i.filename.lower().endswith(".md")]
            if not mds:
                raise ParseError(
                    f"MinerU 结果 zip 中没有 .md 文件: {[i.filename for i in z.infolist()]}"
                )
            biggest = max(mds, key=lambda i: i.file_size)
            return z.read(biggest).decode("utf-8", "replace")
    except zipfile.BadZipFile as e:
        raise ParseError(f"MinerU 结果 zip 损坏: {e}")


async def parse_file(
    file_bytes: bytes, filename: str, client: httpx.AsyncClient | None = None
) -> str:
    """md/txt 直接解码；其余走 MinerU 异步任务 API，返回 markdown 文本。失败 raise ParseError。"""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in _TEXT_EXTS:
        return _decode_text(file_bytes)
    if ext not in _MINERU_EXTS:
        raise ParseError(f"不支持的文件类型: {filename}")

    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=_TIMEOUT)
    try:
        batch_id, upload_url = await _create_batch(client, filename)
        # OSS 预签名 PUT：不带 Authorization
        resp = await _send_with_retry(
            client, "PUT", upload_url, content=file_bytes, timeout=_UPLOAD_TIMEOUT
        )
        if not resp.is_success:
            raise ParseError(f"MinerU 文件上传失败 HTTP {resp.status_code}")
        zip_url = await _wait_done(client, batch_id)
        resp = await _send_with_retry(client, "GET", zip_url, timeout=_UPLOAD_TIMEOUT)
        if not resp.is_success:
            raise ParseError(f"MinerU 结果下载失败 HTTP {resp.status_code}")
        return _extract_md_from_zip(resp.content)
    except httpx.HTTPError as e:  # 兜底：未走 retry 路径的客户端级错误
        raise ParseError(f"MinerU 请求失败: {e}")
    finally:
        if own_client:
            await client.aclose()
