from app.rag.splitter import make_chunks, split_text


def _long_text(n=3000):
    # 长中文段落文本，保证被切成多块
    return "这是一段测试句子。" * n


def test_split_text_chunk_size_respected():
    chunks = split_text(_long_text())
    assert len(chunks) > 1
    for c in chunks:
        assert len(c) <= 500


def test_split_text_overlap():
    chunks = split_text(_long_text())
    # 相邻块有 overlap：后一块的开头出现在前一块的结尾附近
    a, b = chunks[0], chunks[1]
    assert a[-80:] in b or b[:80] in a or a[-20:] in b


def test_split_text_empty():
    assert split_text("") == []


def test_make_chunks_metadata_complete():
    chunks = make_chunks(
        _long_text(),
        doc_id="d1",
        filename="doc.pdf",
        file_hash="abc123",
        upload_time="2026-01-01T00:00:00",
    )
    assert len(chunks) > 1
    for i, c in enumerate(chunks):
        assert c["text"]
        assert c["doc_id"] == "d1"
        assert c["filename"] == "doc.pdf"
        assert c["file_hash"] == "abc123"
        assert c["chunk_index"] == i
        assert c["upload_time"] == "2026-01-01T00:00:00"
    assert set(chunks[0].keys()) == {
        "text", "doc_id", "filename", "file_hash", "chunk_index", "upload_time",
    }


def test_make_chunks_text_matches_split():
    text = _long_text()
    assert [c["text"] for c in make_chunks(text, "d", "f", "h", "t")] == split_text(text)
