"""Privacy-conscious JSONL traces for closing the RAG failure loop."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
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
    include_content = bool(state.get("trace_include_content", False))
    trace_metadata = state.get("retrieval_metadata", {})
    if not isinstance(trace_metadata, dict):
        trace_metadata = {}
    retrieved_chunks: list[dict[str, Any]] = []
    for document in documents[:10]:
        metadata = document.metadata
        item: dict[str, Any] = {
            "source_id": str(
                metadata.get("source_id", metadata.get("document_id", ""))
            ),
            "document_id": str(metadata.get("document_id", "")),
            "chunk_id": str(metadata.get("chunk_id", "")),
            "title": str(metadata.get("title", ""))[:500],
            "source": str(metadata.get("source", ""))[:200],
            "department": str(metadata.get("department", ""))[:100],
            "version": str(metadata.get("version", ""))[:100],
            "status": str(metadata.get("status", ""))[:50],
            "effective_from": metadata.get("effective_from"),
            "effective_to": metadata.get("effective_to"),
            "page": metadata.get("page"),
            "section": str(metadata.get("section", ""))[:300],
        }
        if include_content:
            item["snippet"] = " ".join(document.page_content.split())[:1000]
        retrieved_chunks.append(item)
    trace_citations = citations[:10]
    if not include_content:
        trace_citations = [
            {
                key: value
                for key, value in citation.items()
                if key != "quote"
            }
            | {"quote": "[redacted]"}
            if isinstance(citation, dict)
            else {"citation": "[redacted]"}
            for citation in trace_citations
        ]
    return {
        "trace_id": trace_id,
        "created_at": datetime.now(UTC).isoformat(),
        "turn_id": str(state.get("turn_id", "")),
        "route": str(state.get("route", "")),
        "query": query[:4000] if include_content else _redact_text(query),
        "answer": answer[:12000] if include_content else _redact_text(answer),
        "generation_error": str(state.get("generation_error", ""))[:500],
        "fallback_reason": str(state.get("fallback_reason", ""))[:80],
        "failure_type": classify_runtime_failure(state),
        "failure_stage": state.get("failure_stage"),
        "failure_reason": str(state.get("failure_reason") or "")[:500],
        "attempt_history": [
            {
                "stage": str(item.get("stage", ""))[:40],
                "passed": bool(item.get("passed", False)),
                "reason": str(item.get("reason", ""))[:500],
                "retry_count": int(item.get("retry_count", 0)),
            }
            for item in state.get("attempt_history", [])[-10:]
            if isinstance(item, dict)
        ],
        "request_call_count": int(state.get("request_call_count", 0)),
        "model_call_count": int(state.get("model_call_count", 0)),
        "tool_call_count": int(state.get("tool_call_count", 0)),
        "total_latency_ms": float(state.get("total_latency_ms", 0.0)),
        "status_events": [str(item)[:300] for item in state.get("status_events", [])][-50:],
        "retrieval": trace_metadata,
        "retrieved_chunks": retrieved_chunks,
        "citations": trace_citations,
        "content_recording": "enabled" if include_content else "metadata_only",
        "retention_days": int(state.get("trace_retention_days", 30)),
    }


def _redact_text(value: str) -> str:
    """Keep only a short fingerprint when trace content capture is disabled."""

    import hashlib

    clean = " ".join(value.split())
    if not clean:
        return ""
    return f"[redacted len={len(clean)} sha256={hashlib.sha256(clean.encode('utf-8')).hexdigest()[:16]}]"


def classify_runtime_failure(state: dict[str, Any]) -> str:
    """Classify failures observable without access to benchmark gold labels."""

    route = str(state.get("route", ""))
    answer = str(state.get("answer", "")).strip()
    documents = state.get("retrieved_docs", [])
    citations = state.get("citations", [])
    if str(state.get("generation_error", "")).strip():
        return "generation_error"
    failure_stage = str(state.get("failure_stage", "")).strip()
    if failure_stage in {"retrieval", "relevance", "evidence"}:
        return "retrieval_miss"
    if failure_stage == "citation":
        return "citation_error"
    if failure_stage in {"generation", "hallucination", "tool", "runtime"}:
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
    retention_days: int = 30,
) -> None:
    """Append one trace atomically across local worker processes."""

    if max_bytes < 3 or backup_count <= 0 or retention_days <= 0:
        raise ValueError(
            "Trace max_bytes, backup_count, and retention_days must be positive."
        )
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
        _purge_expired_trace_files(target, retention_days)


def _rotate_trace_files(target: Path, backup_count: int) -> None:
    oldest = target.with_name(f"{target.name}.{backup_count}")
    oldest.unlink(missing_ok=True)
    for index in range(backup_count - 1, 0, -1):
        source = target.with_name(f"{target.name}.{index}")
        if source.exists():
            source.replace(target.with_name(f"{target.name}.{index + 1}"))
    if target.exists():
        target.replace(target.with_name(f"{target.name}.1"))


def _purge_expired_trace_files(target: Path, retention_days: int) -> None:
    cutoff = datetime.now(UTC).timestamp() - timedelta(days=retention_days).total_seconds()
    candidates = [
        target,
        *(
            candidate
            for candidate in target.parent.glob(f"{target.name}.*")
            if candidate.name.rsplit(".", 1)[-1].isdigit()
        ),
    ]
    for candidate in candidates:
        try:
            if candidate.exists() and candidate.stat().st_mtime < cutoff:
                candidate.unlink()
        except OSError:
            continue
