"""硅基流动客户端：embeddings / rerank / chat，OpenAI 兼容格式，重试 2 次 / 超时 10s。"""
import httpx

from app.config import settings

TIMEOUT = 10.0
MAX_ATTEMPTS = 3  # 首次 + 重试 2 次


class LLMClientError(RuntimeError):
    """硅基流动请求失败（网络错误、重试耗尽或响应格式异常）。"""


def _field(container, key: str, what: str):
    """从 200 响应中取字段；畸形响应抛 LLMClientError 而非 KeyError/TypeError
    （否则下游 process_question 会误标"知识库维护中"而非"服务繁忙"）."""
    try:
        return container[key]
    except (KeyError, IndexError, TypeError) as e:
        raise LLMClientError(f"响应格式异常: {what} 缺少 {key}: {str(container)[:200]}") from e


def _list_field(data: dict, key: str, what: str) -> list:
    items = data.get(key)
    if not isinstance(items, list):
        raise LLMClientError(f"响应格式异常: {what} 缺少 {key} 或非数组: {str(data)[:200]}")
    return items


class SiliconFlowClient:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        client: httpx.AsyncClient | None = None,
    ):
        self._api_key = api_key if api_key is not None else settings.LLM_API_KEY
        self._base_url = (base_url or settings.LLM_BASE_URL).rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=TIMEOUT)

    async def _post(self, path: str, payload: dict) -> dict:
        headers = {"Authorization": f"Bearer {self._api_key}"}
        last_err: Exception | None = None
        for _ in range(MAX_ATTEMPTS):
            try:
                resp = await self._client.post(
                    f"{self._base_url}{path}", json=payload, headers=headers
                )
                resp.raise_for_status()
                return resp.json()
            except (httpx.HTTPError, ValueError) as e:
                last_err = e
        raise LLMClientError(f"{path} failed after {MAX_ATTEMPTS} attempts: {last_err}") from last_err

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """批量向量化。单条输入 ≤8192 token（600 字块安全）。"""
        data = await self._post("/embeddings", {"model": settings.EMBEDDING_MODEL, "input": texts})
        return [_field(item, "embedding", "embeddings") for item in _list_field(data, "data", "embeddings")]

    async def rerank(self, query: str, docs: list[str]) -> list[float]:
        """重排，单次 ≤20 docs。按 results[].index 还原为输入顺序的 score 列表。"""
        data = await self._post(
            "/rerank", {"model": settings.RERANKER_MODEL, "query": query, "documents": docs}
        )
        scores = [0.0] * len(docs)
        for r in _list_field(data, "results", "rerank"):
            try:
                scores[r["index"]] = r["score"]
            except (KeyError, TypeError, ValueError, IndexError) as e:
                raise LLMClientError(f"响应格式异常: rerank 结果缺少 index/score: {str(r)[:200]}") from e
        return scores

    async def chat(self, system: str, user: str) -> str:
        data = await self._post(
            "/chat/completions",
            {
                "model": settings.LLM_MODEL,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
        )
        choices = _list_field(data, "choices", "chat")
        message = _field(_field(choices, 0, "chat"), "message", "chat")  # choices 空数组同样走畸形分支
        return _field(message, "content", "chat")


llm_client = SiliconFlowClient()
