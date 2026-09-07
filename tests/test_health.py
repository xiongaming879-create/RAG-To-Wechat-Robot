from fastapi.testclient import TestClient

from app.main import app


def test_health_returns_fixed_shape():
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"status": "ok", "queue_size": 0, "vector_store": "unknown"}
