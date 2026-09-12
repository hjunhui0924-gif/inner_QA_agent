"""FastAPI routes and SSE streaming."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from langchain_core.messages import SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field, field_validator

from backend.agent.memory import (
    add_knowledge_record,
    append_chat_message,
    extract_document_from_upload,
    get_knowledge_record,
    list_knowledge_records,
    load_completed_turn,
    list_chat_sessions,
    list_session_title_candidates,
    load_chat_messages,
    persist_completed_turn,
    save_uploaded_file,
    update_chat_session_title,
    upsert_chat_session,
)
from backend.agent.conversation import estimate_text_tokens
from backend.agent.sessions import (
    create_turn_state,
    delete_session_completely,
    session_operation,
    thread_id_for,
)
from backend.auth.access import (
    KnowledgeAccessPolicy,
    ServerAccessContext,
    development_access_context,
    require_access_context,
    require_knowledge_admin,
    resolve_access_context,
)
from backend.config import settings
from backend.observability.tracing import build_trace, record_trace
from backend.observability.metrics import record_call_stats
from backend.observability.safe_errors import RUNTIME_ERROR_CODE
from backend.observability.budget import (
    BudgetExceededError,
    RequestBudget,
    RunContext,
    budget_deadline,
)


router = APIRouter()
_TITLE_REFRESH_ATTEMPTED_USERS: set[str] = set()

NODE_STATUS_MESSAGES = {
    "manage_conversation_context": "正在整理会话上下文",
    "inject_memory": "正在读取用户偏好",
    "route_query": "正在判断路由",
    "retrieve": "正在检索知识库",
    "grade_documents": "正在评估文档相关性",
    "rewrite_query": "正在优化检索问题",
    "tool_executor": "正在调用工具",
    "generate": "正在生成回答",
    "check_hallucination": "正在验证回答质量",
    "commit_answer": "正在提交经过验证的回答",
    "update_memory": "正在更新用户偏好",
}


class ChatRequest(BaseModel):
    """Incoming chat request payload."""

    message: str = Field(min_length=1)
    user_id: str = Field(default="user_001")
    session_id: str = Field(default="session_default")
    mode: Literal["knowledge", "general"] = "knowledge"
    web_search: bool = False
    turn_id: str | None = Field(default=None, max_length=128)

    @field_validator("message")
    @classmethod
    def validate_message_budget(cls, value: str) -> str:
        estimated = estimate_text_tokens(value) + 4
        available = (
            settings.conversation_token_budget
            - settings.conversation_summary_target_tokens
        )
        if estimated > available:
            raise ValueError(
                "Message exceeds the configured conversation token budget."
            )
        return value

    @field_validator("turn_id")
    @classmethod
    def validate_turn_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized or any(ord(char) < 32 for char in normalized):
            raise ValueError("turn_id must be a non-empty printable identifier.")
        return normalized


def _request_turn_id(request: Request, payload: ChatRequest) -> str | None:
    """Resolve a replayable turn identity from the body or idempotency header."""

    body_value = payload.turn_id
    headers = getattr(request, "headers", {})
    header_value = headers.get("Idempotency-Key") if hasattr(headers, "get") else None
    candidates = [str(value).strip() for value in (body_value, header_value) if value]
    if not candidates:
        return None
    if len(set(candidates)) > 1:
        raise HTTPException(
            status_code=400,
            detail="turn_id and Idempotency-Key must identify the same turn.",
        )
    resolved = candidates[0]
    if len(resolved) > 128 or any(ord(char) < 32 for char in resolved):
        raise HTTPException(status_code=400, detail="Invalid turn identity.")
    return resolved


@router.get("/health")
def health() -> dict[str, str]:
    """Expose a minimal frontend readiness contract."""

    return {
        "status": "ok",
        "model": settings.model_name,
        "retrieval_strategy": settings.retrieval_strategy,
    }


def _ingest_uploaded_file(
    filename: str,
    raw_bytes: bytes,
    title: str,
    source: str,
    department: str,
    version: str,
    status: str,
    effective_from: str,
    effective_to: str,
    owner: str,
    access_scope: str,
    required_scopes: str,
    allowed_roles: str,
    allowed_departments: str,
    denied_roles: str,
    denied_departments: str,
    denied_scopes: str,
    public_internal: bool | None,
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
            source_type=source,
            department=department,
            version=version,
            status=status,
            effective_from=effective_from,
            effective_to=effective_to or None,
            owner=owner,
            access_scope=access_scope,
            required_scopes=required_scopes,
            allowed_roles=allowed_roles,
            allowed_departments=allowed_departments,
            denied_roles=denied_roles,
            denied_departments=denied_departments,
            denied_scopes=denied_scopes,
            public_internal=public_internal,
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


def _request_budget() -> RequestBudget:
    """Create one mutable budget for one HTTP turn."""

    return RequestBudget(
        max_model_calls=settings.request_max_model_calls,
        max_tool_calls=settings.request_max_tool_calls,
        max_total_seconds=settings.request_max_total_seconds,
        input_price_per_1k=settings.model_input_price_per_1k,
        output_price_per_1k=settings.model_output_price_per_1k,
        max_input_tokens=settings.request_max_input_tokens,
        max_output_tokens=settings.request_max_output_tokens,
        max_estimated_cost=settings.request_max_estimated_cost,
    )


def _request_access_context(
    request: Request,
    access_context: ServerAccessContext | None = None,
) -> ServerAccessContext:
    """Resolve identity from the server seam, never from the request body."""

    if isinstance(access_context, ServerAccessContext):
        return access_context
    request_state = getattr(request, "state", None)
    stored = getattr(request_state, "access_context", None)
    if isinstance(stored, ServerAccessContext):
        return stored
    return resolve_access_context(request)


def _assert_user_path(
    requested_user_id: str,
    access_context: ServerAccessContext,
) -> None:
    if requested_user_id != access_context.user_id:
        raise HTTPException(status_code=403, detail="Access denied.")


def _is_real_http_request(request: object) -> bool:
    """Keep direct Python helper calls backwards-compatible without weakening HTTP routes."""

    return hasattr(request, "headers") and hasattr(request, "state")


def _accessible_records(
    access_context: ServerAccessContext,
) -> list[dict[str, object]]:
    policy = KnowledgeAccessPolicy()
    return [
        record
        for record in list_knowledge_records()
        if policy.can_access(access_context, record)
    ]


def _authorized_knowledge_record(
    request: Request,
    source_id: str,
) -> tuple[ServerAccessContext, dict[str, object]]:
    """Resolve a source and authorize it without revealing existence."""

    access_context = _request_access_context(request)
    record = get_knowledge_record(source_id)
    if record is None or not KnowledgeAccessPolicy().can_access(access_context, record):
        raise HTTPException(status_code=404, detail="Knowledge source not found.")
    return access_context, record


def _public_knowledge_record(
    record: dict[str, object],
    *,
    include_content: bool,
) -> dict[str, object]:
    """Expose only bounded, authorized source fields."""

    payload: dict[str, object] = {
        "source_id": str(record.get("id", "")).strip(),
        "title": str(record.get("title", "")).strip(),
        "source": str(record.get("source", "")).strip(),
        "source_type": str(record.get("source_type", "")).strip(),
        "department": str(record.get("department", "")).strip(),
        "version": str(record.get("version", "")).strip(),
        "status": str(record.get("status", "")).strip(),
        "effective_from": record.get("effective_from"),
        "effective_to": record.get("effective_to"),
        "owner": str(record.get("owner", "")).strip(),
        "original_filename": str(record.get("original_filename", "")).strip(),
        "content_checksum": str(record.get("content_checksum", "")).strip(),
    }
    if include_content:
        payload["content"] = str(record.get("content", ""))
    return payload


async def _stream_graph(
    request: Request,
    payload: ChatRequest,
    *,
    access_context: ServerAccessContext | None = None,
) -> AsyncIterator[str]:
    """Serialize one session while its graph run and history writes complete."""

    resolved_access_context = _request_access_context(request, access_context)
    effective_payload = payload.model_copy(
        update={"user_id": resolved_access_context.user_id}
    )
    budget = _request_budget()
    try:
        thread_id = thread_id_for(effective_payload.user_id, effective_payload.session_id)
        async with session_operation(thread_id):
            async for event in _stream_graph_unlocked(
                request,
                effective_payload,
                budget=budget,
                access_context=resolved_access_context,
            ):
                yield event
    except HTTPException:
        raise
    except Exception:
        async for event in _safe_runtime_failure_events(effective_payload, budget=budget):
            yield event


async def _safe_runtime_failure_events(
    payload: ChatRequest,
    *,
    budget: RequestBudget | None = None,
) -> AsyncIterator[str]:
    """Emit a generic failure response without crossing exception details."""

    trace_id = str(uuid.uuid4())
    state: dict[str, object] = {
        "answer": "",
        "failure_stage": "runtime",
        "failure_reason": RUNTIME_ERROR_CODE,
        "generation_error": "",
        "fallback_reason": "unknown",
        "status_events": [f"error:{RUNTIME_ERROR_CODE}"],
        "request_call_count": 0,
        "model_call_count": 0,
        "tool_call_count": 0,
        "total_latency_ms": 0.0,
        "trace_include_content": settings.trace_include_content,
        "trace_retention_days": settings.trace_retention_days,
        "budget_snapshot": budget.snapshot() if budget is not None else None,
    }
    try:
        trace = build_trace(trace_id=trace_id, query=payload.message, state=state)
        trace["failure_type"] = "generation_error"
        if settings.trace_enabled:
            await asyncio.to_thread(
                record_trace,
                trace,
                settings.trace_log_path,
                max_bytes=settings.trace_max_bytes,
                backup_count=settings.trace_backup_count,
                retention_days=settings.trace_retention_days,
            )
    except Exception:
        pass
    yield _sse_event(
        {
            "type": "status",
            "node": "error",
            "content": "运行失败：服务暂时不可用，请稍后重试。",
        }
    )
    yield _sse_event({"type": "done", "trace_id": trace_id})


async def _stream_graph_unlocked(
    request: Request,
    payload: ChatRequest,
    *,
    budget: RequestBudget | None = None,
    access_context: ServerAccessContext | None = None,
) -> AsyncIterator[str]:
    """Keep setup and replay failures inside the same safe stream boundary."""

    request_budget = budget or _request_budget()
    try:
        async for event in _stream_graph_unlocked_core(
            request,
            payload,
            budget=request_budget,
            access_context=access_context,
        ):
            yield event
    except HTTPException:
        raise
    except Exception:
        async for event in _safe_runtime_failure_events(
            payload,
            budget=request_budget,
        ):
            yield event


async def _stream_graph_unlocked_core(
    request: Request,
    payload: ChatRequest,
    *,
    budget: RequestBudget,
    access_context: ServerAccessContext | None = None,
) -> AsyncIterator[str]:
    """Run the graph and emit SSE events."""

    resolved_access_context = _request_access_context(request, access_context)
    if _is_real_http_request(request):
        _assert_user_path(payload.user_id, resolved_access_context)
    thread_id = thread_id_for(payload.user_id, payload.session_id)
    trace_id = str(uuid.uuid4())
    request_started_at = time.perf_counter()
    turn_id = _request_turn_id(request, payload)
    if turn_id:
        completed_turn = await load_completed_turn(
            payload.user_id,
            payload.session_id,
            turn_id,
        )
        if completed_turn is not None:
            if completed_turn.get("user_content") not in {"", payload.message}:
                raise HTTPException(
                    status_code=409,
                    detail="turn_id is already associated with a different message.",
                )
            replay_answer = str(completed_turn.get("content", "")).strip()
            replay_citations = completed_turn.get("citations", [])
            if not isinstance(replay_citations, list):
                replay_citations = []
            await append_chat_message(
                user_id=payload.user_id,
                session_id=payload.session_id,
                role="user",
                content=payload.message,
                turn_id=turn_id,
                message_id=f"{turn_id}:user",
            )
            await append_chat_message(
                user_id=payload.user_id,
                session_id=payload.session_id,
                role="assistant",
                content=replay_answer,
                citations=replay_citations,
                turn_id=turn_id,
                message_id=f"{turn_id}:assistant",
            )
            for chunk in _answer_chunks(replay_answer):
                yield _sse_event({"type": "token", "content": chunk})
            yield _sse_event(
                {
                    "type": "result",
                    "content": replay_answer,
                    "citations": replay_citations,
                    "trace_id": trace_id,
                    "turn_id": turn_id,
                    "failure_type": "none",
                    "failure_stage": None,
                    "failure_reason": None,
                    "replayed": True,
                }
            )
            yield _sse_event({"type": "done", "trace_id": trace_id})
            return
    graph = getattr(request.app.state, "graph", None)
    if graph is None:
        raise RuntimeError("graph_unavailable")
    graph_input = create_turn_state(
        message=payload.message,
        user_id=payload.user_id,
        session_id=payload.session_id,
        trace_id=trace_id,
        mode=payload.mode,
        web_search=payload.web_search,
        turn_id=turn_id,
    )
    config = {"configurable": {"thread_id": thread_id}}
    current_node = ""
    final_state: dict[str, object] = dict(graph_input)
    observed_status_events: list[str] = []
    disconnected = False
    run_context = RunContext(
        budget=budget,
        access_context=resolved_access_context,
    )

    try:
        async for event in graph.astream_events(
            graph_input,
            config=config,
            version="v2",
            context=run_context,
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
                current_node = ""
                continue

        if disconnected:
            return
        final_state["status_events"] = observed_status_events
        final_state["budget_snapshot"] = budget.snapshot()
        final_answer = str(final_state.get("answer", "")).strip()
        if not final_answer:
            raise RuntimeError("Graph completed without a committed answer.")
        response_citations = final_state.get("citations", [])
        if not isinstance(response_citations, list):
            response_citations = []
        completed_turn = await persist_completed_turn(
            user_id=payload.user_id,
            session_id=payload.session_id,
            turn_id=str(graph_input["turn_id"]),
            request_message=payload.message,
            answer=final_answer,
            citations=response_citations,
        )
        if completed_turn.get("user_content") != payload.message:
            raise HTTPException(
                status_code=409,
                detail="turn_id is already associated with a different message.",
            )
        final_answer = str(completed_turn["content"]).strip()
        response_citations = completed_turn.get("citations", [])
        if not isinstance(response_citations, list):
            response_citations = []

        existing_sessions = await list_chat_sessions(payload.user_id)
        is_new_session = not any(
            item.get("session_id") == payload.session_id for item in existing_sessions
        )
        if is_new_session and settings.dashscope_api_key:
            title_call_started_at = time.perf_counter()
            title_call_reserved = False
            try:
                budget.consume_model_call("session_title")
                title_call_reserved = True
                title_model = ChatOpenAI(
                    model=settings.model_name,
                    api_key=settings.dashscope_api_key,
                    base_url=settings.dashscope_base_url,
                    temperature=0,
                    max_tokens=24,
                    timeout=10,
                    max_retries=0,
                    stream_usage=True,
                    extra_body={"enable_thinking": False},
                )
                async with budget_deadline(budget, "session_title"):
                    title_response = await title_model.ainvoke([
                        SystemMessage(content=(
                            "请把用户的提问概括成一个简短会话标题。只返回标题本身，中文，"
                            "不超过12个字，不要标点，不要解释。\n用户提问：" + payload.message
                        ))
                    ])
                budget.record_response(title_response)
                generated_title = str(getattr(title_response, "content", "")).strip()[:24]
                if generated_title:
                    await upsert_chat_session(payload.user_id, payload.session_id, generated_title)
            except BudgetExceededError:
                pass
            except Exception:
                pass
            title_stats = record_call_stats(
                final_state,
                model_calls=1 if title_call_reserved else 0,
                started_at=title_call_started_at,
            )
            final_state.update(title_stats)
            final_state["budget_snapshot"] = budget.snapshot()

        await append_chat_message(
            user_id=payload.user_id,
            session_id=payload.session_id,
            role="user",
            content=payload.message,
            turn_id=str(graph_input["turn_id"]),
            message_id=f"{graph_input['turn_id']}:user",
        )
        await append_chat_message(
            user_id=payload.user_id,
            session_id=payload.session_id,
            role="assistant",
            content=final_answer,
            citations=response_citations,
            turn_id=str(graph_input["turn_id"]),
            message_id=f"{graph_input['turn_id']}:assistant",
        )
        for chunk in _answer_chunks(final_answer):
            yield _sse_event({"type": "token", "content": chunk})

        final_state["total_latency_ms"] = (
            time.perf_counter() - request_started_at
        ) * 1000
        final_state["request_call_count"] = (
            int(final_state.get("model_call_count", 0))
            + int(final_state.get("tool_call_count", 0))
        )
        result_trace = build_trace(
            trace_id=trace_id,
            query=payload.message,
            state={
                **final_state,
                "trace_include_content": settings.trace_include_content,
                "trace_retention_days": settings.trace_retention_days,
            },
        )
        yield _sse_event(
            {
                "type": "result",
                "content": final_answer,
                "citations": response_citations,
                "trace_id": trace_id,
                "turn_id": str(graph_input["turn_id"]),
                "failure_type": result_trace["failure_type"],
                "failure_stage": result_trace.get("failure_stage"),
                "failure_reason": result_trace.get("failure_reason"),
                "budget_snapshot": budget.snapshot(),
            }
        )
        yield _sse_event({"type": "done", "trace_id": trace_id})

        final_state["total_latency_ms"] = (
            time.perf_counter() - request_started_at
        ) * 1000
        final_state["request_call_count"] = (
            int(final_state.get("model_call_count", 0))
            + int(final_state.get("tool_call_count", 0))
        )
        try:
            final_trace = build_trace(
                trace_id=trace_id,
                query=payload.message,
                state={
                    **final_state,
                    "trace_include_content": settings.trace_include_content,
                    "trace_retention_days": settings.trace_retention_days,
                },
            )
            if settings.trace_enabled:
                await asyncio.to_thread(
                    record_trace,
                    final_trace,
                    settings.trace_log_path,
                    max_bytes=settings.trace_max_bytes,
                    backup_count=settings.trace_backup_count,
                    retention_days=settings.trace_retention_days,
                )
        except Exception:
            # The authoritative result and done event have already been sent.
            # Trace persistence must not append a second error stream.
            pass
    except Exception:
        error_state = {
            **final_state,
            "answer": "",
            "total_latency_ms": (time.perf_counter() - request_started_at) * 1000,
            "failure_stage": "runtime",
            "failure_reason": RUNTIME_ERROR_CODE,
            "status_events": [
                *final_state.get("status_events", []),
                f"error:{RUNTIME_ERROR_CODE}",
            ],
        }
        error_state["request_call_count"] = (
            int(error_state.get("model_call_count", 0))
            + int(error_state.get("tool_call_count", 0))
        )
        error_state["trace_include_content"] = settings.trace_include_content
        error_state["trace_retention_days"] = settings.trace_retention_days
        error_state["budget_snapshot"] = budget.snapshot()
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
                    retention_days=settings.trace_retention_days,
                )
            except Exception:
                pass
        yield _sse_event(
            {
                "type": "status",
                "node": "error",
                "content": "运行失败：服务暂时不可用，请稍后重试。",
            }
        )
        yield _sse_event({"type": "done", "trace_id": trace_id})


@router.post("/chat/stream", dependencies=[Depends(require_access_context)])
async def chat_stream(request: Request, payload: ChatRequest) -> StreamingResponse:
    """Stream a single chat turn as SSE."""

    return StreamingResponse(
        _stream_graph(request, payload),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@router.get("/chat/sessions/{user_id}", dependencies=[Depends(require_access_context)])
async def chat_sessions(request: Request, user_id: str) -> dict[str, object]:
    """List chat sessions for one user."""

    access_context = _request_access_context(request)
    if _is_real_http_request(request):
        _assert_user_path(user_id, access_context)
    budget = _request_budget()
    if user_id not in _TITLE_REFRESH_ATTEMPTED_USERS and settings.dashscope_api_key:
        _TITLE_REFRESH_ATTEMPTED_USERS.add(user_id)
        candidates = await list_session_title_candidates(user_id)
        async def refresh_title(candidate: dict[str, str]) -> None:
            try:
                budget.consume_model_call("session_title_refresh")
                title_model = ChatOpenAI(
                    model=settings.model_name,
                    api_key=settings.dashscope_api_key,
                    base_url=settings.dashscope_base_url,
                    temperature=0,
                    max_tokens=24,
                    timeout=10,
                    max_retries=0,
                    stream_usage=True,
                    extra_body={"enable_thinking": False},
                )
                async with budget_deadline(budget, "session_title_refresh"):
                    response = await title_model.ainvoke([
                        SystemMessage(content=(
                            "根据用户第一次提问生成一个能概括会话主题的一句话标题。"
                            "只返回标题，不超过12个汉字，不要引号、标点或解释。\n"
                            f"第一次提问：{candidate['first_question']}"
                        ))
                    ])
                budget.record_response(response)
                title = str(getattr(response, "content", "")).strip()
                if title:
                    await update_chat_session_title(
                        user_id, candidate["session_id"], title
                    )
            except Exception:
                return
        await asyncio.gather(*(refresh_title(candidate) for candidate in candidates))
    return {"items": await list_chat_sessions(user_id)}


@router.get("/chat/history/{user_id}/{session_id}", dependencies=[Depends(require_access_context)])
async def chat_history(request: Request, user_id: str, session_id: str) -> dict[str, object]:
    """Load message history for one session."""

    access_context = _request_access_context(request)
    if _is_real_http_request(request):
        _assert_user_path(user_id, access_context)
    return {"items": await load_chat_messages(user_id, session_id)}


@router.delete("/chat/session/{user_id}/{session_id}", dependencies=[Depends(require_access_context)])
async def delete_session(
    request: Request,
    user_id: str,
    session_id: str,
) -> dict[str, str]:
    """Delete visible history and the corresponding LangGraph thread."""

    checkpointer = getattr(request.app.state, "checkpointer", None)
    if checkpointer is None:
        raise HTTPException(status_code=503, detail="Checkpointer is not initialized.")
    access_context = _request_access_context(request)
    if _is_real_http_request(request):
        _assert_user_path(user_id, access_context)
    await delete_session_completely(
        user_id=user_id,
        session_id=session_id,
        checkpointer=checkpointer,
    )
    return {"message": "会话已删除。"}


@router.get("/knowledge/records", dependencies=[Depends(require_access_context)])
async def knowledge_records(request: Request) -> dict[str, object]:
    """List indexed knowledge records."""

    return {"items": _accessible_records(_request_access_context(request))}


@router.get(
    "/knowledge/records/{source_id}",
    dependencies=[Depends(require_access_context)],
)
async def knowledge_record(request: Request, source_id: str) -> dict[str, object]:
    """Return one source body only after server-side ACL evaluation."""

    _, record = _authorized_knowledge_record(request, source_id)
    return {"record": _public_knowledge_record(record, include_content=True)}


@router.get(
    "/knowledge/records/{source_id}/download",
    dependencies=[Depends(require_access_context)],
)
async def download_knowledge_record(request: Request, source_id: str) -> FileResponse:
    """Download an uploaded source only after the same ACL evaluation."""

    _, record = _authorized_knowledge_record(request, source_id)
    filename = Path(str(record.get("original_filename", "")).replace("\\", "/")).name
    if not filename or filename in {".", ".."}:
        raise HTTPException(status_code=404, detail="Knowledge source not found.")
    upload_root = Path(settings.upload_dir).resolve()
    target = (upload_root / filename).resolve()
    try:
        target.relative_to(upload_root)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Knowledge source not found.") from exc
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Knowledge source not found.")
    return FileResponse(path=target, filename=filename)


@router.post("/knowledge/upload")
async def upload_knowledge_file(
    file: UploadFile = File(...),
    title: str = Form(default=""),
    source: str = Form(default="upload"),
    department: str = Form(default="unknown"),
    version: str = Form(default="v1"),
    status: str = Form(default="active"),
    effective_from: str = Form(default="1970-01-01"),
    effective_to: str = Form(default=""),
    owner: str = Form(default="未指定"),
    access_scope: str = Form(default="internal"),
    required_scopes: str = Form(default=""),
    allowed_roles: str = Form(default=""),
    allowed_departments: str = Form(default=""),
    denied_roles: str = Form(default=""),
    denied_departments: str = Form(default=""),
    denied_scopes: str = Form(default=""),
    public_internal: bool | None = Form(default=None),
    access_context: ServerAccessContext | None = Depends(require_knowledge_admin),
) -> dict[str, object]:
    """Upload a file and write it into the knowledge base."""

    direct_python_call = not isinstance(access_context, ServerAccessContext)
    if direct_python_call:
        # Direct Python callers in local tests do not execute FastAPI
        # dependencies; real HTTP requests always receive the server context.
        access_context = development_access_context()
    policy = KnowledgeAccessPolicy()
    acl_metadata = {
        "access_scope": access_scope,
        "required_scopes": required_scopes,
        "allowed_roles": allowed_roles,
        "allowed_departments": allowed_departments,
        "denied_roles": denied_roles,
        "denied_departments": denied_departments,
        "denied_scopes": denied_scopes,
        "public_internal": public_internal,
        "department": department,
    }
    if not direct_python_call and not policy.can_upload(access_context, acl_metadata):
        raise HTTPException(status_code=403, detail="Upload ACL is not permitted.")

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
            department,
            version,
            status,
            effective_from,
            effective_to,
            access_context.user_id,
            access_scope,
            required_scopes,
            allowed_roles,
            allowed_departments,
            denied_roles,
            denied_departments,
            denied_scopes,
            public_internal,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="JSON 文件解析失败。") from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="知识库入库失败，请稍后重试。",
        ) from exc

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
