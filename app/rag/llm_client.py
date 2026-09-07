"""硅基流动客户端：embeddings / rerank / chat，OpenAI 兼容格式，重试 2 次 / 超时 10s。"""
import httpx

from app.config import settings

TIMEOUT = 10.0
MAX_ATTEMPTS = 3  # 首次 + 重试 2 次


class LLMClientError(RuntimeError):
    """硅基流动请求失败（网络错误或重试耗尽）。"""


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
        return [item["embedding"] for item in data["data"]]

    async def rerank(self, query: str, docs: list[str]) -> list[float]:
        """重排，单次 ≤20 docs。按 results[].index 还原为输入顺序的 score 列表。"""
        data = await self._post(
            "/rerank", {"model": settings.RERANKER_MODEL, "query": query, "documents": docs}
        )
        scores = [0.0] * len(docs)
        for r in data["results"]:
            scores[r["index"]] = r["score"]
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
        return data["choices"][0]["message"]["content"]


llm_client = SiliconFlowClient()
