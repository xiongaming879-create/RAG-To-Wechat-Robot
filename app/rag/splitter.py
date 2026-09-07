"""文本分块：RecursiveCharacterTextSplitter 500/80，块附强制五元数据。"""

from langchain_text_splitters import RecursiveCharacterTextSplitter

_splitter = RecursiveCharacterTextSplitter(
    chunk_size=500,
    chunk_overlap=80,
    separators=["\n\n", "\n", "。", "！", "？", ".", "!", "?", " ", ""],
)


def split_text(text: str) -> list[str]:
    return _splitter.split_text(text)


def make_chunks(
    text: str, doc_id: str, filename: str, file_hash: str, upload_time: str
) -> list[dict]:
    """每个块 dict：text + 五元数据（doc_id/filename/file_hash/chunk_index/upload_time）。"""
    return [
        {
            "text": chunk,
            "doc_id": doc_id,
            "filename": filename,
            "file_hash": file_hash,
            "chunk_index": i,
            "upload_time": upload_time,
        }
        for i, chunk in enumerate(split_text(text))
    ]
