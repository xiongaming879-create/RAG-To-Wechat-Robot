import httpx
import pytest

from app.config import settings
from app.rag.loader import ParseError, parse_file

_URL = "http://mineru.test/parse"


@pytest.fixture(autouse=True)
def mineru_url(monkeypatch):
    monkeypatch.setattr(settings, "MINERU_API_URL", _URL)


def _client_for(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.anyio
async def test_parse_file_returns_text_on_200():
    def handler(request):
        assert request.url.path == "/parse"
        assert "multipart/form-data" in request.headers["content-type"]
        return httpx.Response(200, json={"text": "解析出来的文本"})

    async with _client_for(handler) as client:
        text = await parse_file(b"file-bytes", "doc.pdf", client=client)
    assert text == "解析出来的文本"


@pytest.mark.anyio
async def test_parse_file_500_raises_parse_error():
    def handler(request):
        return httpx.Response(500, text="boom")

    async with _client_for(handler) as client:
        with pytest.raises(ParseError) as exc:
            await parse_file(b"x", "doc.pdf", client=client)
    assert "500" in exc.value.reason


@pytest.mark.anyio
async def test_parse_file_network_error_raises_parse_error():
    def handler(request):
        raise httpx.ConnectError("conn refused")

    async with _client_for(handler) as client:
        with pytest.raises(ParseError):
            await parse_file(b"x", "doc.pdf", client=client)


@pytest.mark.anyio
async def test_parse_file_retries_then_succeeds():
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) < 3:  # 失败 2 次，第 3 次成功（重试 2 次）
            return httpx.Response(502)
        return httpx.Response(200, json={"text": "ok"})

    async with _client_for(handler) as client:
        text = await parse_file(b"x", "doc.pdf", client=client)
    assert text == "ok"
    assert len(calls) == 3
