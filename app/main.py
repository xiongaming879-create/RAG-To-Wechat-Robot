import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.admin import router as admin_router
from app.api.chat import router as chat_router
from app.queue.task_queue import task_queue
from app.rag.vector_store import vector_store
from app.wx_callback import router as wx_callback_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        await vector_store.ensure_collection()
    except Exception:
        # Qdrant 未就绪不阻断启动，/health 会如实上报 unavailable
        logger.exception("启动时 ensure_collection 失败")
    await task_queue.start()
    yield
    await task_queue.stop()


app = FastAPI(title="RAG WeChat Bot", lifespan=lifespan)
app.include_router(wx_callback_router)
app.include_router(admin_router)
app.include_router(chat_router)

_STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(_STATIC_DIR / "chat.html")


@app.get("/health")
async def health():
    alive = await vector_store.is_alive()
    return {
        "status": "ok",
        "queue_size": task_queue.qsize(),
        "vector_store": "ok" if alive else "unavailable",
    }
