import asyncio

import httpx
import pytest

from app.wx_token import WxAccessToken

@pytest.fixture
def token_factory():
    def _make(backoff=(0, 0)):
        counter = []

        def route(request):
            counter.append(request)
            return httpx.Response(
                200,
                json={"errcode": 0, "access_token": f"tok-{len(counter)}", "expires_in": 7200},
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(route))
        return WxAccessToken("ww-test", "secret-test", client=client, backoff=backoff), counter

    return _make


@pytest.mark.anyio
async def test_first_get_fetches_second_get_cached(anyio_backend, token_factory):
    t, counter = token_factory()
    assert await t.get() == "tok-1"
    assert await t.get() == "tok-1"
    assert len(counter) == 1


@pytest.mark.anyio
async def test_refresh_after_expire(anyio_backend, token_factory):
    t, counter = token_factory()
    await t.get()
    t._expire_at = 0.0  # 强制到达提前刷新点
    assert await t.get() == "tok-2"
    assert len(counter) == 2


@pytest.mark.anyio
async def test_concurrent_get_single_request(anyio_backend, token_factory):
    async def slow_route(request):
        counter.append(request)
        await asyncio.sleep(0.05)
        return httpx.Response(
            200, json={"errcode": 0, "access_token": "tok-x", "expires_in": 7200}
        )

    counter = []
    client = httpx.AsyncClient(transport=httpx.MockTransport(slow_route))
    t = WxAccessToken("ww-test", "secret-test", client=client, backoff=(0, 0))
    tokens = await asyncio.gather(*[t.get() for _ in range(10)])
    assert len(counter) == 1
    assert all(tok == "tok-x" for tok in tokens)


@pytest.mark.anyio
async def test_retry_succeeds_third_attempt(anyio_backend, token_factory):
    counter = []

    def route(request):
        counter.append(request)
        if len(counter) < 3:
            return httpx.Response(200, json={"errcode": 40013, "errmsg": "bad"})
        return httpx.Response(
            200, json={"errcode": 0, "access_token": "tok-ok", "expires_in": 7200}
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(route))
    t = WxAccessToken("ww-test", "secret-test", client=client, backoff=(0, 0))
    assert await t.get() == "tok-ok"
    assert len(counter) == 3


@pytest.mark.anyio
async def test_all_retries_fail_raises(anyio_backend, token_factory):
    counter = []

    def route(request):
        counter.append(request)
        return httpx.Response(500)

    client = httpx.AsyncClient(transport=httpx.MockTransport(route))
    t = WxAccessToken("ww-test", "secret-test", client=client, backoff=(0, 0))
    with pytest.raises(RuntimeError):
        await t.get()
    assert len(counter) == 3
