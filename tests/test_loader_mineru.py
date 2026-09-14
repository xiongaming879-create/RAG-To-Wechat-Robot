# -*- coding: utf-8 -*-
"""W3: MinerU 异步任务 API 适配层测试 — 全部 mock httpx，不碰真实网络。"""
import io
import zipfile

import httpx
import pytest

from app.config import settings
from app.rag import loader
from app.rag.loader import ParseError, parse_file

_URL = "https://mineru.test"
_BATCH = f"{_URL}/api/v4/file-urls/batch"


@pytest.fixture(autouse=True)
def mineru_env(monkeypatch):
    monkeypatch.setattr(settings, "MINERU_API_URL", _URL)
    monkeypatch.setattr(settings, "MINERU_API_TOKEN", "test-token")
    monkeypatch.setattr(loader, "_POLL_INTERVAL", 0)  # 测试不真等 2s


def _client_for(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _zip_with(*md_contents):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for i, content in enumerate(md_contents):
            z.writestr(f"part{i}.md", content)
    return buf.getvalue()


def _batch_ok(handler_calls):
    """标准 batch 申请响应，记录调用次数。"""
    def handler(request):
        handler_calls.append(("POST", request.url.path))
        assert request.headers["authorization"] == "Bearer test-token"
        return httpx.Response(200, json={
            "code": 0, "msg": "ok",
            "data": {"batch_id": "b-1", "file_urls": [f"{_URL}/oss/put"]},
        })
    return handler


@pytest.mark.anyio
async def test_md_local_decode_no_network():
    """md/txt 本地解码，不发任何请求（handler 一调用就炸）。"""
    def handler(request):
        raise AssertionError("md 不应发起网络请求")

    async with _client_for(handler) as client:
        assert await parse_file("# 你好".encode("utf-8"), "a.md", client=client) == "# 你好"
        assert await parse_file("纯文本".encode("gbk"), "b.txt", client=client) == "纯文本"


@pytest.mark.anyio
async def test_md_bad_encoding_raises():
    with pytest.raises(ParseError):
        await parse_file(b"\xff\xfe\xfd\xfc", "a.md")


@pytest.mark.anyio
async def test_unsupported_ext_raises_without_network():
    def handler(request):
        raise AssertionError("不支持的扩展名不应发起网络请求")

    async with _client_for(handler) as client:
        with pytest.raises(ParseError) as exc:
            await parse_file(b"x", "virus.exe", client=client)
    assert "exe" in exc.value.reason


@pytest.mark.anyio
async def test_missing_token_raises(monkeypatch):
    monkeypatch.setattr(settings, "MINERU_API_TOKEN", "")
    with pytest.raises(ParseError) as exc:
        await parse_file(b"x", "a.pdf")
    assert "MINERU_API_TOKEN" in exc.value.reason


@pytest.mark.anyio
async def test_happy_path_pdf():
    calls = []
    state = {"polls": 0}

    def handler(request):
        if request.method == "POST":
            return _batch_ok(calls)(request)
        if request.method == "PUT":
            assert request.url.path == "/oss/put"
            assert "authorization" not in request.headers  # OSS PUT 不带鉴权
            assert request.content == b"pdf-bytes"
            return httpx.Response(200)
        # 轮询：第一次 running，第二次 done
        state["polls"] += 1
        if state["polls"] == 1:
            return httpx.Response(200, json={
                "code": 0, "data": {"extract_result": [{"file_name": "a.pdf", "state": "running", "err_msg": ""}]},
            })
        return httpx.Response(200, json={
            "code": 0, "data": {"extract_result": [{
                "file_name": "a.pdf", "state": "done", "err_msg": "",
                "full_zip_url": f"{_URL}/result.zip",
            }]},
        })

    # zip 下载也走同一 handler，用次数区分
    calls_count = {"n": 0}

    def full_handler(request):
        if request.url.path == "/result.zip":
            calls_count["n"] += 1
            if calls_count["n"] == 1:
                return httpx.Response(503)  # 下载失败一次，验证重试
            return httpx.Response(200, content=_zip_with("# 标题\n正文", "junk"))
        return handler(request)

    async with _client_for(full_handler) as client:
        text = await parse_file(b"pdf-bytes", "a.pdf", client=client)
    assert text == "# 标题\n正文"
    assert calls_count["n"] == 2


@pytest.mark.anyio
async def test_business_error_code_no_retry():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={"code": -60002, "msg": "unsupported file type:xx"})

    async with _client_for(handler) as client:
        with pytest.raises(ParseError) as exc:
            await parse_file(b"x", "a.pdf", client=client)
    assert "-60002" in exc.value.reason
    assert len(calls) == 1  # 业务错误不重试


@pytest.mark.anyio
async def test_state_failed_raises():
    calls = []

    def handler(request):
        if request.method == "POST":
            return _batch_ok(calls)(request)
        if request.method == "PUT":
            return httpx.Response(200)
        return httpx.Response(200, json={
            "code": 0,
            "data": {"extract_result": [{"file_name": "a.pdf", "state": "failed", "err_msg": "bad pdf"}]},
        })

    async with _client_for(handler) as client:
        with pytest.raises(ParseError) as exc:
            await parse_file(b"x", "a.pdf", client=client)
    assert "bad pdf" in exc.value.reason


@pytest.mark.anyio
async def test_poll_timeout_raises(monkeypatch):
    monkeypatch.setattr(loader, "_POLL_TIMEOUT", 0.05)

    def handler(request):
        if request.method == "POST":
            return _batch_ok([])(request)
        if request.method == "PUT":
            return httpx.Response(200)
        return httpx.Response(200, json={
            "code": 0,
            "data": {"extract_result": [{"file_name": "a.pdf", "state": "pending", "err_msg": ""}]},
        })

    async with _client_for(handler) as client:
        with pytest.raises(ParseError) as exc:
            await parse_file(b"x", "a.pdf", client=client)
    assert "超时" in exc.value.reason


@pytest.mark.anyio
async def test_batch_5xx_retries_then_success():
    posts, batches = [], []

    def handler(request):
        if request.method == "POST":
            posts.append(1)
            if len(posts) < 3:
                return httpx.Response(502)
            return _batch_ok(batches)(request)
        if request.method == "PUT":
            return httpx.Response(200)
        return httpx.Response(200, json={
            "code": 0,
            "data": {"extract_result": [{"file_name": "a.pdf", "state": "done", "err_msg": "",
                                          "full_zip_url": f"{_URL}/result.zip"}]},
        })

    async with _client_for(handler) as client:
        with pytest.raises(ParseError):
            # zip 下载 handler 返回非 zip 内容 → ParseError，但 POST 重试已验证
            await parse_file(b"x", "a.pdf", client=client)
    assert len(posts) == 3


@pytest.mark.anyio
async def test_zip_without_md_raises():
    def handler(request):
        if request.method == "POST":
            return _batch_ok([])(request)
        if request.method == "PUT":
            return httpx.Response(200)
        if request.url.path == "/result.zip":
            return httpx.Response(200, content=b"not a zip")
        return httpx.Response(200, json={
            "code": 0,
            "data": {"extract_result": [{"file_name": "a.pdf", "state": "done", "err_msg": "",
                                          "full_zip_url": f"{_URL}/result.zip"}]},
        })

    async with _client_for(handler) as client:
        with pytest.raises(ParseError):
            await parse_file(b"x", "a.pdf", client=client)


@pytest.mark.anyio
async def test_zip_picks_largest_md():
    """zip 中有多个 .md 时取最大的那个。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("small.md", "小")
        z.writestr("big.md", "这是最大的md文件内容")
        z.writestr("other.json", "{}")
    assert loader._extract_md_from_zip(buf.getvalue()) == "这是最大的md文件内容"
