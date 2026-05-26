"""SQLite memory and knowledge base helpers."""

from __future__ import annotations

import asyncio
import csv
import hashlib
import json
import math
import re
from collections.abc import Iterable
from io import BytesIO, StringIO
from pathlib import Path
from typing import Any, Protocol

import aiosqlite
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings

try:
    from pypdf import PdfReader
except ImportError:  # pragma: no cover
    PdfReader = None

try:
    from docx import Document as DocxDocument
except ImportError:  # pragma: no cover
    DocxDocument = None

try:
    from langchain_chroma import Chroma
except ImportError:  # pragma: no cover
    try:
        from langchain_community.vectorstores import Chroma
    except ImportError:  # pragma: no cover
        Chroma = None  # type: ignore[assignment]

from backend.config import settings


class VectorStoreLike(Protocol):
    """Minimal vector store interface used by the app."""

    def add_documents(self, documents: list[Document]) -> Any:
        """Add documents to the index."""

    def similarity_search(self, query: str, k: int = 4) -> list[Document]:
        """Run a similarity search."""


_VECTORSTORE: VectorStoreLike | None = None


class HashingEmbeddings(Embeddings):
    """Dependency-light embeddings for local retrieval."""

    def __init__(self, dimensions: int = 256) -> None:
        self.dimensions = dimensions

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        tokens = _tokenize_text(text)
        if not tokens:
            return vector

        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
            index = int(digest[:8], 16) % self.dimensions
            vector[index] += 1.0

        norm = math.sqrt(sum(value * value for value in vector))
        if norm:
            vector = [value / norm for value in vector]
        return vector


def _is_cjk_char(char: str) -> bool:
    """Return whether one character is a CJK ideograph."""

    if not char:
        return False
    code = ord(char)
    return 0x4E00 <= code <= 0x9FFF


def _tokenize_text(text: str) -> list[str]:
    """Tokenize mixed Chinese and ASCII text for hashing and lexical overlap."""

    lowered = text.lower()
    tokens: list[str] = []
    buffer: list[str] = []
    cjk_run: list[str] = []

    def flush_buffer() -> None:
        if buffer:
            tokens.append("".join(buffer))
            buffer.clear()

    def flush_cjk_run() -> None:
        if not cjk_run:
            return
        run = "".join(cjk_run)
        tokens.extend(cjk_run)
        if len(run) >= 2:
            tokens.extend(run[index : index + 2] for index in range(len(run) - 1))
        if len(run) >= 3:
            tokens.extend(run[index : index + 3] for index in range(len(run) - 2))
        cjk_run.clear()

    for char in lowered:
        if char.isascii() and (char.isalnum() or char == "_"):
            flush_cjk_run()
            buffer.append(char)
            continue
        flush_buffer()
        if _is_cjk_char(char):
            cjk_run.append(char)
            continue
        flush_cjk_run()

    flush_buffer()
    flush_cjk_run()
    return tokens


class _LocalVectorStore:
    """In-memory fallback when Chroma dependencies are unavailable."""

    def __init__(self, embeddings: Embeddings, documents: list[Document]) -> None:
        self._embeddings = embeddings
        self._documents = documents
        self._vectors = embeddings.embed_documents(
            [document.page_content for document in documents]
        )

    def add_documents(self, documents: list[Document]) -> None:
        self._documents.extend(documents)
        self._vectors.extend(
            self._embeddings.embed_documents(
                [document.page_content for document in documents]
            )
        )

    def similarity_search(self, query: str, k: int = 4) -> list[Document]:
        query_vector = self._embeddings.embed_query(query)
        scored: list[tuple[float, Document]] = []
        for vector, document in zip(self._vectors, self._documents, strict=False):
            score = sum(a * b for a, b in zip(query_vector, vector, strict=False))
            scored.append((score, document))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [document for _, document in scored[:k]]


def ensure_data_directories() -> None:
    """Ensure local data directories exist."""

    Path(settings.chroma_persist_dir).mkdir(parents=True, exist_ok=True)
    Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
    Path(settings.sqlite_db_path).parent.mkdir(parents=True, exist_ok=True)
    Path(settings.knowledge_base_path).parent.mkdir(parents=True, exist_ok=True)


def set_vectorstore(vectorstore: VectorStoreLike) -> None:
    """Register the shared vector store instance."""

    global _VECTORSTORE
    _VECTORSTORE = vectorstore


def get_vectorstore() -> VectorStoreLike:
    """Return the shared vector store instance."""

    if _VECTORSTORE is None:
        raise RuntimeError("Vector store has not been initialized yet.")
    return _VECTORSTORE


async def ensure_user_memory_db(db_path: str | Path) -> None:
    """Create the application tables when needed."""

    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(path) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_sessions (
                user_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, session_id)
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await db.commit()


def _derive_session_title(message: str) -> str:
    """Create a short session title from the first user message."""

    normalized = " ".join(message.strip().split())
    if not normalized:
        return "新会话"
    return normalized[:24]


async def upsert_chat_session(
    user_id: str,
    session_id: str,
    title: str = "",
) -> None:
    """Create or update one chat session record."""

    clean_title = title.strip()
    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        await db.execute(
            """
            INSERT INTO chat_sessions (user_id, session_id, title, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id, session_id) DO UPDATE SET
                title = CASE
                    WHEN excluded.title <> '' THEN excluded.title
                    ELSE chat_sessions.title
                END,
                updated_at = CURRENT_TIMESTAMP
            """,
            (user_id, session_id, clean_title),
        )
        await db.commit()


async def append_chat_message(
    user_id: str,
    session_id: str,
    role: str,
    content: str,
) -> None:
    """Append one chat message and touch its session."""

    clean_content = content.strip()
    if not clean_content:
        return

    await upsert_chat_session(
        user_id=user_id,
        session_id=session_id,
        title=_derive_session_title(clean_content) if role == "user" else "",
    )
    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        await db.execute(
            """
            INSERT INTO chat_messages (user_id, session_id, role, content)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, session_id, role, clean_content),
        )
        await db.execute(
            """
            UPDATE chat_sessions
            SET updated_at = CURRENT_TIMESTAMP
            WHERE user_id = ? AND session_id = ?
            """,
            (user_id, session_id),
        )
        await db.commit()


async def list_chat_sessions(user_id: str) -> list[dict[str, Any]]:
    """Return chat sessions for one user ordered by recent activity."""

    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        cursor = await db.execute(
            """
            SELECT s.session_id, s.title, s.created_at, s.updated_at,
                   COALESCE(
                       (
                           SELECT m.content
                           FROM chat_messages m
                           WHERE m.user_id = s.user_id
                             AND m.session_id = s.session_id
                           ORDER BY m.id DESC
                           LIMIT 1
                       ),
                       ''
                   ) AS last_message
            FROM chat_sessions s
            WHERE s.user_id = ?
            ORDER BY s.updated_at DESC
            """,
            (user_id,),
        )
        rows = await cursor.fetchall()
        await cursor.close()

    return [
        {
            "session_id": row[0],
            "title": row[1] or "新会话",
            "created_at": row[2],
            "updated_at": row[3],
            "last_message": row[4],
        }
        for row in rows
    ]


async def load_chat_messages(user_id: str, session_id: str) -> list[dict[str, str]]:
    """Return chat messages for one session."""

    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        cursor = await db.execute(
            """
            SELECT role, content
            FROM chat_messages
            WHERE user_id = ? AND session_id = ?
            ORDER BY id ASC
            """,
            (user_id, session_id),
        )
        rows = await cursor.fetchall()
        await cursor.close()

    return [{"role": row[0], "content": row[1]} for row in rows]


async def delete_chat_session(user_id: str, session_id: str) -> None:
    """Delete one session and all of its messages."""

    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        await db.execute(
            """
            DELETE FROM chat_messages
            WHERE user_id = ? AND session_id = ?
            """,
            (user_id, session_id),
        )
        await db.execute(
            """
            DELETE FROM chat_sessions
            WHERE user_id = ? AND session_id = ?
            """,
            (user_id, session_id),
        )
        await db.commit()


def _chunk_text(text: str, chunk_size: int = 500, chunk_overlap: int = 50) -> list[str]:
    """Split text into overlapping chunks."""

    stripped = text.strip()
    if len(stripped) <= chunk_size:
        return [stripped]

    chunks: list[str] = []
    start = 0
    while start < len(stripped):
        end = min(len(stripped), start + chunk_size)
        chunks.append(stripped[start:end])
        if end >= len(stripped):
            break
        start = max(0, end - chunk_overlap)
    return chunks


def _load_json_file(path: Path) -> list[dict[str, Any]]:
    """Load JSON array content from disk."""

    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, list) else []


def _save_json_file(path: Path, data: list[dict[str, Any]]) -> None:
    """Persist JSON array content to disk."""

    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _normalize_text_for_dedup(text: str) -> str:
    """Normalize content before duplicate comparison."""

    return " ".join(text.split()).strip()


def _content_fingerprint(text: str) -> str:
    """Build a stable fingerprint for one knowledge entry."""

    normalized = _normalize_text_for_dedup(text)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _cosine_similarity(vec1: list[float], vec2: list[float]) -> float:
    """Compute cosine similarity for normalized vectors."""

    if not vec1 or not vec2:
        return 0.0
    return sum(a * b for a, b in zip(vec1, vec2, strict=False))


def _find_near_duplicate(
    candidate_text: str,
    records: list[dict[str, Any]],
    threshold: float,
) -> dict[str, Any] | None:
    """Find a semantically similar existing knowledge record."""

    embeddings = HashingEmbeddings(dimensions=settings.embedding_dim)
    candidate_vector = embeddings.embed_query(candidate_text)
    best_match: dict[str, Any] | None = None
    best_score = -1.0

    for existing in records:
        existing_content = str(existing.get("content", "")).strip()
        if not existing_content:
            continue
        score = _cosine_similarity(
            candidate_vector,
            embeddings.embed_query(existing_content),
        )
        if score > best_score:
            best_score = score
            best_match = {
                "title": str(existing.get("title", "")).strip(),
                "content": existing_content,
                "source": str(existing.get("source", "seed")).strip() or "seed",
                "original_filename": str(existing.get("original_filename", "")).strip(),
                "similarity": score,
            }

    if best_match and best_score >= threshold:
        return best_match
    return None


def _load_knowledge_base_records() -> list[dict[str, str]]:
    """Load the raw knowledge base JSON records."""

    path = Path(settings.knowledge_base_path)
    records = _load_json_file(path)
    normalized: list[dict[str, str]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        title = str(record.get("title", "")).strip()
        content = str(record.get("content", "")).strip()
        source = str(record.get("source", "seed")).strip() or "seed"
        if title and content:
            normalized.append(
                {
                    "title": title,
                    "content": content,
                    "source": source,
                }
            )
    return normalized


def _chunk_documents_from_record(
    title: str,
    content: str,
    source: str,
) -> list[Document]:
    """Create chunked documents from one record."""

    documents: list[Document] = []
    for index, chunk in enumerate(_chunk_text(content)):
        documents.append(
            Document(
                page_content=chunk,
                metadata={
                    "title": title,
                    "source": source,
                    "chunk_index": index,
                },
            )
        )
    return documents


def _build_documents() -> list[Document]:
    """Chunk all knowledge base records into documents."""

    documents: list[Document] = []
    for record in _load_knowledge_base_records():
        documents.extend(
            _chunk_documents_from_record(
                title=record["title"],
                content=record["content"],
                source=record["source"],
            )
        )
    return documents


def _build_local_vectorstore() -> _LocalVectorStore:
    """Create the fallback in-memory vector store."""

    return _LocalVectorStore(
        embeddings=HashingEmbeddings(dimensions=settings.embedding_dim),
        documents=_build_documents(),
    )


def _create_vectorstore_sync() -> VectorStoreLike:
    """Build or open the local persistent vector store."""

    if Chroma is None:
        return _build_local_vectorstore()

    persist_dir = Path(settings.chroma_persist_dir)
    persist_dir.mkdir(parents=True, exist_ok=True)
    embeddings = HashingEmbeddings(dimensions=settings.embedding_dim)
    vectorstore = Chroma(
        collection_name="enterprise_knowledge_base",
        embedding_function=embeddings,
        persist_directory=str(persist_dir),
    )

    try:
        count = vectorstore._collection.count()
    except Exception:
        count = 0

    if count == 0:
        vectorstore.add_documents(_build_documents())
    return vectorstore


async def ensure_vectorstore() -> VectorStoreLike:
    """Create the vector store without blocking the event loop."""

    ensure_data_directories()
    vectorstore = await asyncio.to_thread(_create_vectorstore_sync)
    set_vectorstore(vectorstore)
    return vectorstore


def search_knowledge_base_text(query: str, top_k: int = 4) -> str:
    """Search the enterprise knowledge base and return a compact summary."""

    try:
        vectorstore = get_vectorstore()
    except RuntimeError:
        return "知识库尚未初始化。"

    try:
        docs = vectorstore.similarity_search(query, k=top_k)
    except Exception as exc:
        return f"知识库检索失败：{exc}"

    if not docs:
        return "未找到匹配的知识库内容。"

    lines: list[str] = []
    for idx, doc in enumerate(docs, start=1):
        title = str(doc.metadata.get("title", "未命名条目"))
        source = str(doc.metadata.get("source", "unknown"))
        snippet = doc.page_content.replace("\n", " ").strip()
        lines.append(f"{idx}. {title} [{source}]：{snippet[:200]}")
    return "\n".join(lines)


async def search_documents(query: str, top_k: int = 4) -> list[Document]:
    """Retrieve the most relevant documents from the vector store."""

    vectorstore = get_vectorstore()
    return await asyncio.to_thread(vectorstore.similarity_search, query, top_k)


def keyword_overlap_score(query: str, texts: Iterable[str]) -> float:
    """Return a simple lexical overlap score."""

    query_tokens = set(_tokenize_text(query))
    if not query_tokens:
        return 0.0

    combined_tokens: set[str] = set()
    for text in texts:
        combined_tokens.update(_tokenize_text(text))
    if not combined_tokens:
        return 0.0
    return len(query_tokens & combined_tokens) / len(query_tokens)


def latest_user_text(messages: list[Any]) -> str:
    """Extract the latest human message content."""

    for message in reversed(messages):
        if getattr(message, "type", "") == "human":
            content = getattr(message, "content", "")
            return content if isinstance(content, str) else str(content)
    return ""


def extract_text_from_upload(filename: str, content: bytes) -> str:
    """Extract plain text from a supported uploaded file."""

    suffix = Path(filename).suffix.lower()
    if suffix in {".txt", ".md", ".py", ".log"}:
        return content.decode("utf-8", errors="ignore")
    if suffix == ".csv":
        text = content.decode("utf-8", errors="ignore")
        rows = list(csv.reader(StringIO(text)))
        return "\n".join(" | ".join(cell.strip() for cell in row) for row in rows)
    if suffix == ".json":
        obj = json.loads(content.decode("utf-8", errors="ignore"))
        return json.dumps(obj, ensure_ascii=False, indent=2)
    if suffix == ".pdf":
        if PdfReader is None:
            raise ValueError("当前环境未安装 pypdf，无法解析 .pdf 文件。")
        reader = PdfReader(BytesIO(content))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    if suffix == ".docx":
        if DocxDocument is None:
            raise ValueError("当前环境未安装 python-docx，无法解析 .docx 文件。")
        document = DocxDocument(BytesIO(content))
        return "\n".join(paragraph.text for paragraph in document.paragraphs)
    raise ValueError("仅支持 .txt .md .csv .json .pdf .docx .py .log 文件。")


def save_uploaded_file(filename: str, content: bytes) -> Path:
    """Persist the uploaded original file locally."""

    ensure_data_directories()
    target = Path(settings.upload_dir) / filename
    stem = target.stem
    suffix = target.suffix
    counter = 1
    while target.exists():
        target = Path(settings.upload_dir) / f"{stem}_{counter}{suffix}"
        counter += 1
    target.write_bytes(content)
    return target


def add_knowledge_record(
    title: str,
    content: str,
    source: str,
    original_filename: str,
) -> dict[str, Any]:
    """Append one record to the knowledge base and index it immediately."""

    clean_title = title.strip() or Path(original_filename).stem
    clean_content = content.strip()
    if not clean_content:
        raise ValueError("上传文件解析后内容为空，无法入库。")

    kb_path = Path(settings.knowledge_base_path)
    records = _load_json_file(kb_path)
    candidate_fingerprint = _content_fingerprint(clean_content)

    for existing in records:
        existing_content = str(existing.get("content", "")).strip()
        if not existing_content:
            continue
        if _content_fingerprint(existing_content) == candidate_fingerprint:
            return {
                "title": str(existing.get("title", "")).strip() or clean_title,
                "content": existing_content,
                "source": str(existing.get("source", "seed")).strip() or "seed",
                "original_filename": str(existing.get("original_filename", "")).strip(),
                "deduplicated": True,
                "dedup_type": "exact",
            }

    near_duplicate = _find_near_duplicate(
        candidate_text=clean_content,
        records=records,
        threshold=settings.knowledge_dedup_similarity_threshold,
    )
    if near_duplicate is not None:
        return {
            "title": near_duplicate["title"] or clean_title,
            "content": near_duplicate["content"],
            "source": near_duplicate["source"],
            "original_filename": near_duplicate["original_filename"],
            "deduplicated": True,
            "dedup_type": "similar",
            "similarity": near_duplicate["similarity"],
        }

    record = {
        "title": clean_title,
        "content": clean_content,
        "source": source,
        "original_filename": original_filename,
        "content_fingerprint": candidate_fingerprint,
        "deduplicated": False,
        "dedup_type": "none",
    }
    records.append(record)
    _save_json_file(kb_path, records)

    get_vectorstore().add_documents(
        _chunk_documents_from_record(
            title=clean_title,
            content=clean_content,
            source=source,
        )
    )
    return record


def list_knowledge_records() -> list[dict[str, Any]]:
    """Return a compact list of knowledge records."""

    records = _load_json_file(Path(settings.knowledge_base_path))
    items: list[dict[str, Any]] = []
    for index, record in enumerate(records, start=1):
        content = str(record.get("content", "")).strip()
        items.append(
            {
                "id": index,
                "title": str(record.get("title", "")).strip(),
                "source": str(record.get("source", "seed")).strip() or "seed",
                "original_filename": str(record.get("original_filename", "")).strip(),
                "preview": content[:120],
                "content_fingerprint": str(record.get("content_fingerprint", "")).strip(),
            }
        )
    return items
