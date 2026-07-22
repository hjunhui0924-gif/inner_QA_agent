"""Privacy-conscious JSONL traces for closing the RAG failure loop."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from filelock import FileLock
from langchain_core.documents import Document


def build_trace(
    *,
    trace_id: str,
    query: str,
    state: dict[str, Any],
) -> dict[str, Any]:
    """Build a bounded, serializable trace from final graph state."""

    documents = [
        item for item in state.get("retrieved_docs", []) if isinstance(item, Document)
    ]
    citations = state.get("citations", [])
    if not isinstance(citations, list):
        citations = []
    answer = str(state.get("answer", ""))
    return {
        "trace_id": trace_id,
        "created_at": datetime.now(UTC).isoformat(),
        "route": str(state.get("route", "")),
        "query": query[:4000],
        "answer": answer[:12000],
        "generation_error": str(state.get("generation_error", ""))[:500],
        "fallback_reason": str(state.get("fallback_reason", ""))[:80],
        "failure_type": classify_runtime_failure(state),
        "status_events": [str(item)[:300] for item in state.get("status_events", [])][-50:],
        "retrieved_chunks": [
            {
                "document_id": str(document.metadata.get("document_id", "")),
                "chunk_id": str(document.metadata.get("chunk_id", "")),
                "title": str(document.metadata.get("title", ""))[:500],
                "page": document.metadata.get("page"),
                "section": str(document.metadata.get("section", ""))[:300],
                "snippet": " ".join(document.page_content.split())[:1000],
            }
            for document in documents[:10]
        ],
        "citations": citations[:10],
    }


def classify_runtime_failure(state: dict[str, Any]) -> str:
    """Classify failures observable without access to benchmark gold labels."""

    route = str(state.get("route", ""))
    answer = str(state.get("answer", "")).strip()
    documents = state.get("retrieved_docs", [])
    citations = state.get("citations", [])
    if str(state.get("generation_error", "")).strip():
        return "generation_error"
    fallback_reason = str(state.get("fallback_reason", "")).strip()
    if fallback_reason == "retrieval_exhausted":
        return "retrieval_miss"
    if fallback_reason == "hallucination_exhausted":
        return "generation_error"
    if route == "rag" and not documents:
        return "retrieval_miss"
    if not answer:
        return "generation_error"
    if route == "rag" and documents and not citations and _is_abstention(answer):
        return "retrieval_miss"
    if route == "rag" and documents and not citations:
        return "citation_error"
    return "none"


def _is_abstention(answer: str) -> bool:
    lowered = answer.casefold()
    return ("没有" in lowered and "足够" in lowered) or any(
        phrase in lowered
        for phrase in (
            "信息不足",
            "未找到",
            "没有足够",
            "无法回答",
            "insufficient",
            "not enough information",
            "cannot answer",
        )
    )


def record_trace(
    trace: dict[str, Any],
    path: str | Path,
    *,
    max_bytes: int = 50 * 1024 * 1024,
    backup_count: int = 3,
) -> None:
    """Append one trace atomically across local worker processes."""

    if max_bytes < 3 or backup_count <= 0:
        raise ValueError("Trace max_bytes must be at least 3 and backup_count positive.")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(trace, ensure_ascii=False, separators=(",", ":"))
    if len(f"{line}\n".encode("utf-8")) > max_bytes:
        minimal = json.dumps(
            {
                "trace_id": str(trace.get("trace_id", ""))[:80],
                "failure_type": str(trace.get("failure_type", "unknown"))[:40],
                "truncated": True,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        line = minimal if len(f"{minimal}\n".encode("utf-8")) <= max_bytes else "{}"
    with FileLock(f"{target}.lock"):
        encoded_size = len(f"{line}\n".encode("utf-8"))
        if target.exists() and target.stat().st_size + encoded_size > max_bytes:
            _rotate_trace_files(target, backup_count)
        with target.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(f"{line}\n")
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.chmod(target, 0o600)
        except OSError:
            pass


def _rotate_trace_files(target: Path, backup_count: int) -> None:
    oldest = target.with_name(f"{target.name}.{backup_count}")
    oldest.unlink(missing_ok=True)
    for index in range(backup_count - 1, 0, -1):
        source = target.with_name(f"{target.name}.{index}")
        if source.exists():
            source.replace(target.with_name(f"{target.name}.{index + 1}"))
    if target.exists():
        target.replace(target.with_name(f"{target.name}.1"))
