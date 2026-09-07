from fastapi import FastAPI

from app.wx_callback import router as wx_callback_router

app = FastAPI(title="RAG WeChat Bot")
app.include_router(wx_callback_router)


@app.get("/health")
def health():
    return {"status": "ok", "queue_size": 0, "vector_store": "unknown"}
