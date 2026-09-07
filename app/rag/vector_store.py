"""Qdrant 向量库封装：建集合 / 批量写入 / 按 doc_id 删除 / 检索 / 健康检查."""
import uuid

from qdrant_client import AsyncQdrantClient, models

from app.config import settings


class VectorStore:
    def __init__(self, url: str | None = None, collection: str | None = None):
        self.url = url or settings.QDRANT_URL
        self.collection = collection or settings.QDRANT_COLLECTION_NAME
        self.client = AsyncQdrantClient(url=self.url)

    async def ensure_collection(self) -> None:
        """幂等建集合（1024 维 / Cosine / HNSW 默认）+ doc_id keyword payload 索引."""
        if await self.client.collection_exists(self.collection):
            return
        await self.client.create_collection(
            collection_name=self.collection,
            vectors_config=models.VectorParams(
                size=settings.EMBEDDING_DIM,
                distance=models.Distance.COSINE,
            ),
        )
        await self.client.create_payload_index(
            collection_name=self.collection,
            field_name="doc_id",
            field_schema=models.PayloadSchemaType.KEYWORD,
        )

    async def upsert_chunks(self, chunks: list[dict], vectors: list[list[float]]) -> None:
        """批量 upsert；chunks 与 vectors 按同一顺序一一对应."""
        if len(chunks) != len(vectors):
            raise ValueError(
                f"chunks({len(chunks)}) 与 vectors({len(vectors)}) 数量不一致"
            )
        points = [
            models.PointStruct(id=str(uuid.uuid4()), vector=v, payload=c)
            for c, v in zip(chunks, vectors)
        ]
        await self.client.upsert(collection_name=self.collection, points=points, wait=True)

    async def delete_doc(self, doc_id: str) -> None:
        """按 doc_id payload 过滤删除（走 keyword 索引）."""
        await self.client.delete(
            collection_name=self.collection,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="doc_id", match=models.MatchValue(value=doc_id)
                        )
                    ]
                )
            ),
        )

    async def search(self, query_vec: list[float], top_k: int = 15) -> list[dict]:
        res = await self.client.query_points(
            collection_name=self.collection,
            query=query_vec,
            limit=top_k,
            with_payload=True,
        )
        return [{"payload": p.payload, "score": p.score, "id": p.id} for p in res.points]

    async def list_docs(self) -> list[dict]:
        """按 doc_id 聚合 → [{doc_id, filename, file_hash, upload_time, chunk_count}]."""
        docs: dict[str, dict] = {}
        offset = None
        while True:
            points, offset = await self.client.scroll(
                collection_name=self.collection,
                with_payload=True,
                limit=256,
                offset=offset,
            )
            for p in points:
                pl = p.payload
                d = docs.setdefault(
                    pl["doc_id"],
                    {
                        "doc_id": pl["doc_id"],
                        "filename": pl["filename"],
                        "file_hash": pl["file_hash"],
                        "upload_time": pl["upload_time"],
                        "chunk_count": 0,
                    },
                )
                d["chunk_count"] += 1
            if offset is None:
                break
        return list(docs.values())

    async def is_alive(self) -> bool:
        try:
            await self.client.get_collections()
            return True
        except Exception:
            return False


vector_store = VectorStore()
