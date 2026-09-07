"""Unit tests for SiliconFlow client. All HTTP calls go through httpx.MockTransport — no real network."""
import json

import httpx
import pytest

from app.config import settings
from app.rag.llm_client import LLMClientError, SiliconFlowClient

pytestmark = pytest.mark.anyio

BASE_URL = "https://api.siliconflow.cn/v1"


@pytest.fixture(autouse=True)
def known_models(monkeypatch):
    monkeypatch.setattr(settings, "EMBEDDING_MODEL", "test-embed-model")
    monkeypatch.setattr(settings, "RERANKER_MODEL", "test-rerank-model")
    monkeypatch.setattr(settings, "LLM_MODEL", "test-llm-model")


def make_client(handler):
    return SiliconFlowClient(
        api_key="test-key",
        base_url=BASE_URL,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


async def test_embed_request_and_shape():
    bodies = []

    async def handler(request):
        bodies.append(request.read())
        return httpx.Response(200, json={"data": [{"embedding": [0.1, 0.2]}, {"embedding": [0.3, 0.4]}]})

    client = make_client(handler)
    result = await client.embed(["hello", "world"])
    assert result == [[0.1, 0.2], [0.3, 0.4]]
    body = json.loads(bodies[0])
    assert body["model"] == "test-embed-model"
    assert body["input"] == ["hello", "world"]


async def test_embed_auth_header():
    captured = {}

    async def handler(request):
        captured["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"data": [{"embedding": [1.0]}]})

    client = make_client(handler)
    await client.embed(["hi"])
    assert captured["auth"] == "Bearer test-key"


async def test_rerank_restores_input_order_by_index():
    bodies = []

    async def handler(request):
        bodies.append(request.read())
        return httpx.Response(
            200,
            json={"results": [{"index": 1, "score": 0.9}, {"index": 0, "score": 0.5}]},
        )

    client = make_client(handler)
    scores = await client.rerank("query", ["doc-a", "doc-b"])
    assert scores == [0.5, 0.9]
    body = json.loads(bodies[0])
    assert body["model"] == "test-rerank-model"
    assert body["query"] == "query"
    assert body["documents"] == ["doc-a", "doc-b"]


async def test_chat_messages_structure_and_content():
    bodies = []

    async def handler(request):
        bodies.append(request.read())
        return httpx.Response(200, json={"choices": [{"message": {"content": "answer"}}]})

    client = make_client(handler)
    content = await client.chat("sys prompt", "user prompt")
    assert content == "answer"
    body = json.loads(bodies[0])
    assert body["model"] == "test-llm-model"
    assert body["messages"] == [
        {"role": "system", "content": "sys prompt"},
        {"role": "user", "content": "user prompt"},
    ]


async def test_retry_succeeds_on_third_attempt():
    calls = []

    async def handler(request):
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(500, json={"error": "boom"})
        return httpx.Response(200, json={"data": [{"embedding": [1.0]}]})

    client = make_client(handler)
    result = await client.embed(["hi"])
    assert result == [[1.0]]
    assert len(calls) == 3


async def test_retry_exhausted_raises():
    calls = []

    async def handler(request):
        calls.append(1)
        return httpx.Response(500, json={"error": "boom"})

    client = make_client(handler)
    with pytest.raises(LLMClientError):
        await client.embed(["hi"])
    assert len(calls) == 3
