"""FastAPI routes and SSE streaming."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from langchain_core.messages import HumanMessage

from backend.agent.memory import (
    add_knowledge_record,
    append_chat_message,
    delete_chat_session,
    extract_document_from_upload,
    list_knowledge_records,
    list_chat_sessions,
    load_chat_messages,
    save_uploaded_file,
)
from backend.config import settings
from backend.observability.tracing import build_trace, record_trace


router = APIRouter()

NODE_STATUS_MESSAGES = {
    "inject_memory": "正在读取用户偏好",
    "route_query": "正在判断路由",
    "retrieve": "正在检索知识库",
    "grade_documents": "正在评估文档相关性",
    "rewrite_query": "正在优化检索问题",
    "tool_executor": "正在调用工具",
    "generate": "正在生成回答",
    "check_hallucination": "正在验证回答质量",
    "update_memory": "正在更新用户偏好",
}


class ChatRequest(BaseModel):
    """Incoming chat request payload."""

    message: str = Field(min_length=1)
    user_id: str = Field(default="user_001")
    session_id: str = Field(default="session_default")


def _ingest_uploaded_file(
    filename: str,
    raw_bytes: bytes,
    title: str,
    source: str,
) -> dict[str, object]:
    """Parse and persist an upload outside the event-loop thread."""

    extracted = extract_document_from_upload(filename, raw_bytes)
    saved_path = save_uploaded_file(filename, raw_bytes)
    try:
        record = add_knowledge_record(
            title=title or Path(filename).stem,
            content=extracted.content,
            source=source,
            original_filename=saved_path.name,
            segments=extracted.segments,
        )
    except Exception:
        saved_path.unlink(missing_ok=True)
        raise
    if record.get("deduplicated"):
        saved_path.unlink(missing_ok=True)
    return record


def _sse_event(payload: dict[str, object]) -> str:
    """Format a payload as one SSE data event."""

    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _extract_node_name(event: dict[str, object]) -> str:
    """Best-effort extraction of the current LangGraph node name."""

    metadata = event.get("metadata") or {}
    if isinstance(metadata, dict):
        value = metadata.get("langgraph_node")
        if isinstance(value, str):
            return value
    name = event.get("name")
    return name if isinstance(name, str) else ""


def _extract_token(event: dict[str, object]) -> str:
    """Extract streamed text from a model stream event."""

    data = event.get("data") or {}
    if not isinstance(data, dict):
        return ""
    chunk = data.get("chunk")
    if chunk is None:
        return ""
    content = getattr(chunk, "content", chunk)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("text", "")))
        return "".join(parts)
    return str(content)


def _answer_chunks(answer: str, chunk_size: int = 80) -> list[str]:
    """Split only the validated final answer for SSE delivery."""

    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive.")
    return [answer[index : index + chunk_size] for index in range(0, len(answer), chunk_size)]


async def _stream_graph(request: Request, payload: ChatRequest) -> AsyncIterator[str]:
    """Run the graph and emit SSE events."""

    graph = getattr(request.app.state, "graph", None)
    if graph is None:
        raise HTTPException(status_code=503, detail="Graph is not initialized.")

    thread_id = f"{payload.user_id}_{payload.session_id}"
    trace_id = str(uuid.uuid4())
    graph_input = {
        "messages": [HumanMessage(content=payload.message)],
        "query": payload.message,
        "user_id": payload.user_id,
        "session_id": payload.session_id,
        "trace_id": trace_id,
        "retrieval_retry_count": 0,
        "hallucination_retry_count": 0,
        "status_events": [],
    }
    config = {"configurable": {"thread_id": thread_id}}
    current_node = ""
    assistant_text = ""
    current_generation_text = ""
    final_state: dict[str, object] = dict(graph_input)
    observed_status_events: list[str] = []
    disconnected = False

    try:
        async for event in graph.astream_events(
            graph_input,
            config=config,
            version="v2",
        ):
            if await request.is_disconnected():
                disconnected = True
                break

            event_name = str(event.get("event", ""))
            node_name = _extract_node_name(event)

            if event_name == "on_chain_end":
                data = event.get("data") or {}
                output = data.get("output") if isinstance(data, dict) else None
                if isinstance(output, dict):
                    final_state.update(output)

            if event_name == "on_chain_start" and node_name in NODE_STATUS_MESSAGES:
                current_node = node_name
                if node_name == "generate":
                    current_generation_text = ""
                observed_status_events.append(node_name)
                yield _sse_event(
                    {
                        "type": "status",
                        "node": node_name,
                        "content": NODE_STATUS_MESSAGES[node_name],
                    }
                )
                continue

            if event_name == "on_chain_end" and node_name == current_node:
                if node_name == "generate":
                    assistant_text = current_generation_text
                current_node = ""
                continue

            if event_name == "on_chat_model_stream" and current_node == "generate":
                token = _extract_token(event)
                if token:
                    current_generation_text += token

        if disconnected:
            return
        final_state["status_events"] = observed_status_events
        final_answer = str(final_state.get("answer", "")).strip() or assistant_text
        if not final_answer:
            final_answer = "未返回有效内容。"
        trace = build_trace(
            trace_id=trace_id,
            query=payload.message,
            state=final_state,
        )
        if settings.trace_enabled:
            try:
                await asyncio.to_thread(
                    record_trace,
                    trace,
                    settings.trace_log_path,
                    max_bytes=settings.trace_max_bytes,
                    backup_count=settings.trace_backup_count,
                )
            except Exception:
                pass

        await append_chat_message(
            user_id=payload.user_id,
            session_id=payload.session_id,
            role="user",
            content=payload.message,
        )
        await append_chat_message(
            user_id=payload.user_id,
            session_id=payload.session_id,
            role="assistant",
            content=final_answer,
        )
        for chunk in _answer_chunks(final_answer):
            yield _sse_event({"type": "token", "content": chunk})
        yield _sse_event(
            {
                "type": "result",
                "content": final_answer,
                "citations": trace["citations"],
                "trace_id": trace_id,
                "failure_type": trace["failure_type"],
            }
        )
        yield _sse_event({"type": "done", "trace_id": trace_id})
    except Exception as exc:
        error_state = {
            **final_state,
            "answer": "",
            "status_events": [*final_state.get("status_events", []), f"error:{exc}"],
        }
        trace = build_trace(trace_id=trace_id, query=payload.message, state=error_state)
        trace["failure_type"] = "generation_error"
        if settings.trace_enabled:
            try:
                await asyncio.to_thread(
                    record_trace,
                    trace,
                    settings.trace_log_path,
                    max_bytes=settings.trace_max_bytes,
                    backup_count=settings.trace_backup_count,
                )
            except Exception:
                pass
        yield _sse_event(
            {
                "type": "status",
                "node": "error",
                "content": f"运行失败：{exc}",
            }
        )
        yield _sse_event({"type": "done", "trace_id": trace_id})


@router.post("/chat/stream")
async def chat_stream(request: Request, payload: ChatRequest) -> StreamingResponse:
    """Stream a single chat turn as SSE."""

    return StreamingResponse(
        _stream_graph(request, payload),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@router.get("/chat/sessions/{user_id}")
async def chat_sessions(user_id: str) -> dict[str, object]:
    """List chat sessions for one user."""

    return {"items": await list_chat_sessions(user_id)}


@router.get("/chat/history/{user_id}/{session_id}")
async def chat_history(user_id: str, session_id: str) -> dict[str, object]:
    """Load message history for one session."""

    return {"items": await load_chat_messages(user_id, session_id)}


@router.delete("/chat/session/{user_id}/{session_id}")
async def delete_session(user_id: str, session_id: str) -> dict[str, str]:
    """Delete one chat session and its messages."""

    await delete_chat_session(user_id, session_id)
    return {"message": "会话已删除。"}


@router.get("/knowledge/records")
async def knowledge_records() -> dict[str, object]:
    """List indexed knowledge records."""

    return {"items": list_knowledge_records()}


@router.post("/knowledge/upload")
async def upload_knowledge_file(
    file: UploadFile = File(...),
    title: str = Form(default=""),
    source: str = Form(default="upload"),
) -> dict[str, object]:
    """Upload a file and write it into the knowledge base."""

    filename = file.filename or "untitled.txt"
    raw_bytes = await file.read(settings.max_upload_bytes + 1)
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="上传文件为空。")
    if len(raw_bytes) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"上传文件不能超过 {settings.max_upload_bytes // (1024 * 1024)} MB。",
        )

    try:
        record = await asyncio.to_thread(
            _ingest_uploaded_file,
            filename,
            raw_bytes,
            title,
            source,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="JSON 文件解析失败。") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"知识库入库失败：{exc}") from exc

    return {
        "message": (
            "文件已成功入库。"
            if not record.get("deduplicated")
            else (
                "检测到完全重复内容，已跳过入库。"
                if record.get("dedup_type") == "exact"
                else "检测到高相似内容，已跳过入库。"
            )
        ),
        "record": {
            "title": record["title"],
            "source": record["source"],
            "original_filename": record["original_filename"],
            "content_length": len(record["content"]),
            "deduplicated": bool(record.get("deduplicated")),
            "dedup_type": str(record.get("dedup_type", "none")),
            "similarity": record.get("similarity"),
        },
    }
