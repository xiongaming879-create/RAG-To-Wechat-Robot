from fastapi import FastAPI

app = FastAPI(title="RAG WeChat Bot")


@app.get("/health")
def health():
    return {"status": "ok", "queue_size": 0, "vector_store": "unknown"}
