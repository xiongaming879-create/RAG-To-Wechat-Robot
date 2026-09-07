from app.config import Settings


def test_embedding_dim_defaults_to_1024(monkeypatch):
    monkeypatch.delenv("EMBEDDING_DIM", raising=False)
    assert Settings(_env_file=None).EMBEDDING_DIM == 1024


def test_wx_corp_id_roundtrips_from_env(monkeypatch):
    monkeypatch.setenv("WX_CORP_ID", "ww-test-corp")
    assert Settings(_env_file=None).WX_CORP_ID == "ww-test-corp"


def test_qdrant_url_defaults_when_unset(monkeypatch):
    monkeypatch.delenv("QDRANT_URL", raising=False)
    assert Settings(_env_file=None).QDRANT_URL == "http://qdrant:6333"
