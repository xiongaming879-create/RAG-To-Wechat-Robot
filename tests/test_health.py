import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.mark.filterwarnings("ignore::UserWarning")  # qdrant-client 版本探测警告，无碍
def test_health_reports_queue_and_vector_store():
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert isinstance(body["queue_size"], int)
    assert body["vector_store"] in {"ok", "unavailable"}
