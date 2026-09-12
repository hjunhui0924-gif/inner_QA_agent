"""Async LangGraph node implementations."""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from collections.abc import Sequence
from typing import Any, Literal

from langchain_core.documents import Document
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
)
from langchain_openai import ChatOpenAI
from langgraph.runtime import Runtime

from backend.auth.access import KnowledgeAccessPolicy, ServerAccessContext
from backend.config import settings
from backend.agent.citations import (
    sanitize_answer_citations,
    build_citations,
    citation_markers,
    format_documents_for_prompt,
    validate_citation_claim_alignment,
)
from backend.agent.conversation import (
    merge_fallback_summary,
    plan_conversation_window,
    render_messages,
    truncate_to_token_budget,
)
from backend.agent.memory import (
    get_retriever,
    keyword_overlap_score,
    latest_user_text,
    search_documents,
    search_documents_with_metadata,
)
from backend.agent.state import AgentState
from backend.agent.tools import (
    ToolResult,
    get_current_time_result,
    normalize_tool_result,
    render_tool_output,
    search_knowledge_base_result,
    search_web_result,
    tool_failure,
)
from backend.observability.metrics import record_call_stats
from backend.observability.budget import (
    BUDGET_EXHAUSTED_CODE,
    BudgetExceededError,
    RequestBudget,
    RunContext,
    budget_deadline,
    extract_usage,
)
from backend.observability.safe_errors import (
    GENERATION_ERROR_CODE,
    INTERNAL_ERROR_CODE,
    RETRIEVAL_ERROR_CODE,
    safe_diagnostic,
)
from backend.retrieval.engine import RetrievalFilter


class _RouteDecision(dict):
    """Tiny helper type for JSON parsing."""


def _budget_for(runtime: Runtime[RunContext] | None) -> RequestBudget | None:
    """Return the request budget from runtime context, if this is a budgeted run."""

    context = getattr(runtime, "context", None)
    return context.budget if isinstance(context, RunContext) else None


def _budget_fields(runtime: Runtime[RunContext] | None) -> dict[str, object]:
    budget = _budget_for(runtime)
    return {"budget_snapshot": budget.snapshot()} if budget is not None else {}


def _reserve_model_call(
    runtime: Runtime[RunContext] | None,
    node: str,
) -> bool:
    budget = _budget_for(runtime)
    if budget is None:
        return False
    budget.consume_model_call(node)
    return True


def _reserve_tool_call(
    runtime: Runtime[RunContext] | None,
    node: str,
) -> bool:
    budget = _budget_for(runtime)
    if budget is None:
        return False
    budget.consume_tool_call(node)
    return True


def _finish_model_call(
    runtime: Runtime[RunContext] | None,
    response: object,
) -> None:
    budget = _budget_for(runtime)
    if budget is None:
        return
    budget.record_response(response)
    budget.ensure_available()


def _finish_stream_call(
    runtime: Runtime[RunContext] | None,
    usage: tuple[int, int, float | None] | None,
) -> None:
    budget = _budget_for(runtime)
    if budget is None:
        return
    if usage is not None:
        budget.record_usage(
            input_tokens=usage[0],
            output_tokens=usage[1],
            cost=usage[2],
        )
    budget.ensure_available()


def _budget_failure(
    runtime: Runtime[RunContext] | None,
    *,
    node: str,
    state: AgentState,
    model_call_reserved: bool = False,
    tool_call_reserved: bool = False,
    started_at: float | None = None,
) -> dict[str, object]:
    """Return a safe state update when a request budget blocks progress."""

    result: dict[str, object] = {
        "failure_stage": "runtime",
        "failure_reason": BUDGET_EXHAUSTED_CODE,
        "fallback_reason": "budget_exhausted",
        "generation_error": BUDGET_EXHAUSTED_CODE,
        "status_events": [f"{node}:budget_exhausted"],
        **_budget_fields(runtime),
    }
    result.update(
        record_call_stats(
            state,
            model_calls=1 if model_call_reserved else 0,
            tool_calls=1 if tool_call_reserved else 0,
            started_at=started_at,
        )
    )
    return result


async def manage_conversation_context(
    state: AgentState,
    runtime: Runtime[RunContext] | None = None,
) -> dict[str, Any]:
    """Summarize old turns and remove them from persistent graph messages."""

    messages = list(state.get("messages", []))
    existing_summary = state.get("conversation_summary", "").strip()
    plan = plan_conversation_window(
        messages,
        summary=existing_summary,
        trigger_tokens=settings.conversation_summary_trigger_tokens,
        token_budget=settings.conversation_token_budget,
        summary_target_tokens=settings.conversation_summary_target_tokens,
        recent_turns=settings.conversation_recent_turns,
    )
    if plan is None:
        return {
            "status_events": ["manage_conversation_context:unchanged"],
            **_budget_fields(runtime),
        }

    old_conversation = render_messages(plan.messages_to_summarize)
    prompt = (
        "你负责压缩企业知识助手的旧对话。请只返回简洁的结构化摘要，不要回答当前问题。\n"
        "保留用户目标、明确的实体/制度名称、金额、时间、限制条件、版本（旧版/现行版）、"
        "来源状态、已做决定、尚未解决的问题和代词指向。不要把助手的推测写成已确认事实。\n"
        f"已有摘要：\n{existing_summary or '无'}\n"
        f"待压缩旧对话：\n{old_conversation}\n"
    )
    call_started_at = time.perf_counter()
    model_call_reserved = False
    try:
        model_call_reserved = _reserve_model_call(runtime, "manage_conversation_context")
        model = _build_model(
            temperature=0,
            max_tokens=settings.conversation_summary_target_tokens,
        )
        async with budget_deadline(_budget_for(runtime), "manage_conversation_context"):
            response = await model.ainvoke([SystemMessage(content=prompt)])
        _finish_model_call(runtime, response)
        summary = _as_text(response).strip()
        if not summary:
            raise ValueError("Conversation summarizer returned empty content.")
    except BudgetExceededError:
        return {
            **_budget_failure(
                runtime,
                node="manage_conversation_context",
                state=state,
                model_call_reserved=model_call_reserved,
                started_at=call_started_at,
            ),
        }
    except Exception:
        summary = merge_fallback_summary(
            existing_summary,
            old_conversation,
            settings.conversation_summary_target_tokens,
        )

    summary = truncate_to_token_budget(
        summary,
        settings.conversation_summary_target_tokens,
        keep_recent=False,
    )

    removals = [
        RemoveMessage(id=message.id)
        for message in plan.messages_to_summarize
        if message.id
    ]
    return {
        "conversation_summary": summary,
        "messages": removals,
        "status_events": ["manage_conversation_context:summarized"],
        **record_call_stats(state, model_calls=1, started_at=call_started_at),
        **_budget_fields(runtime),
    }


def _build_model(
    temperature: float = 0.2,
    *,
    model_name: str | None = None,
    max_tokens: int | None = None,
) -> ChatOpenAI:
    """Create the Qwen model configured through DashScope."""

    if not settings.dashscope_api_key:
        raise RuntimeError(
            "DASHSCOPE_API_KEY is not set. Configure it in .env before running the model."
        )
    selected_model = settings.model_name if model_name is None else model_name.strip()
    if not selected_model:
        raise RuntimeError("Configured model name must not be empty.")
    model_options: dict[str, Any] = {
        "model": selected_model,
        "api_key": settings.dashscope_api_key,
        "base_url": settings.dashscope_base_url,
        "temperature": temperature,
        "streaming": True,
        "stream_usage": True,
        "max_retries": 0,
        "extra_body": {"enable_thinking": settings.qwen_enable_thinking},
    }
    if max_tokens is not None:
        if max_tokens <= 0:
            raise ValueError("max_tokens must be positive when configured.")
        model_options["max_tokens"] = max_tokens
    return ChatOpenAI(
        **model_options,
    )


def _extract_json_object(text: str) -> dict[str, Any]:
    """Extract the first JSON object from a model response."""

    cleaned = text.strip().strip("`")
    match = re.search(r"\{.*\}", cleaned, flags=re.S)
    if match:
        cleaned = match.group(0)
    data = json.loads(cleaned)
    if not isinstance(data, dict):
        raise ValueError("Model response is not a JSON object.")
    return data


def _as_text(value: Any) -> str:
    """Convert a LangChain message or chunk to plain text."""

    if value is None:
        return ""
    content = getattr(value, "content", value)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("text", "")))
            else:
                parts.append(str(item))
        return "".join(parts)
    return str(content)


def _format_documents(documents: Sequence[Document]) -> str:
    """Render retrieved documents for prompting."""

    return format_documents_for_prompt(documents)


def _append_attempt(
    state: AgentState,
    *,
    stage: str,
    passed: bool,
    reason: str = "",
) -> list[dict[str, object]]:
    """Keep a bounded, serializable record of validation attempts."""

    history = list(state.get("attempt_history", []))
    history.append(
        {
            "stage": stage,
            "passed": passed,
            "reason": reason[:500],
            "retry_count": int(state.get("hallucination_retry_count", 0)),
        }
    )
    return history[-10:]


def _heuristic_route(query: str) -> Literal["rag", "tool_call", "direct"]:
    """Fallback router when the LLM is unavailable."""

    lowered = query.lower()
    if any(
        keyword in lowered
        for keyword in [
            "时间",
            "几点",
            "日期",
            "today",
            "now",
            "current time",
        ]
    ):
        return "tool_call"
    if any(
        keyword in lowered
        for keyword in [
            "制度",
            "流程",
            "规范",
            "政策",
            "知识库",
            "报销",
            "请假",
            "审批",
            "合同",
            "采购",
            "财务",
            "人事",
            "内部",
        ]
    ):
        return "rag"
    return "direct"


def _looks_like_web_query(query: str) -> bool:
    return any(keyword in query.casefold() for keyword in [
        "联网", "新闻", "最新", "实时", "网页", "搜索", "网上", "天气", "股价",
        "news", "latest", "search", "weather",
    ])


def _looks_like_contextual_follow_up(query: str) -> bool:
    normalized = "".join(query.split())
    return len(normalized) <= 40 and any(
        marker in normalized
        for marker in ["那", "这个", "那个", "它", "上述", "超过", "呢", "怎么办"]
    )


def _previous_user_text(messages: list[BaseMessage], current_query: str) -> str:
    human_texts = [
        _as_text(message).strip()
        for message in messages
        if getattr(message, "type", "") == "human" and _as_text(message).strip()
    ]
    if human_texts and human_texts[-1] == current_query.strip():
        human_texts.pop()
    return human_texts[-1] if human_texts else ""


def _context_anchor_terms(text: str) -> set[str]:
    terms = {
        token.casefold()
        for token in re.findall(r"[a-zA-Z0-9_]{3,}", text)
    }
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        if len(run) == 1:
            terms.add(run)
        else:
            terms.update(run[index : index + 2] for index in range(len(run) - 1))
    return terms


_GENERIC_CONTEXT_TERMS = {
    "用户",
    "正在",
    "了解",
    "公司",
    "制度",
    "问题",
    "什么",
    "如何",
    "超过",
    "金额",
    "审批",
    "流程",
    "规定",
    "需要",
    "继续",
    "查询",
    "要求",
}


def _topic_anchor_terms(text: str) -> set[str]:
    return {
        term
        for term in _context_anchor_terms(text)
        if not any(character.isdigit() for character in term)
        and term not in _GENERIC_CONTEXT_TERMS
        and term not in {"元", "万元", "日期", "时间"}
    }


def _rewrite_preserves_context(candidate: str, context: str) -> bool:
    required_terms = _topic_anchor_terms(context)
    candidate_terms = _topic_anchor_terms(candidate)
    return bool(required_terms & candidate_terms)


def _previous_assistant_citations(messages: Sequence[BaseMessage]) -> list[dict[str, Any]]:
    """Read structured citations from the latest committed assistant message."""

    for message in reversed(messages):
        if getattr(message, "type", "") != "ai":
            continue
        additional_kwargs = getattr(message, "additional_kwargs", {})
        if not isinstance(additional_kwargs, dict):
            continue
        citations = additional_kwargs.get("citations")
        if isinstance(citations, list):
            return [item for item in citations if isinstance(item, dict)]
    return []


def _contextual_retrieval_filter(
    messages: Sequence[BaseMessage],
    query: str,
    conversation_summary: str = "",
) -> dict[str, object] | None:
    """Carry the previous answer's source/version constraints into a follow-up."""

    previous_user_query = _previous_user_text(messages, query)
    explicit_filter = _explicit_version_filter(query)
    if explicit_filter is not None:
        return explicit_filter
    if not _looks_like_contextual_follow_up(query):
        return None
    citations = _previous_assistant_citations(messages)
    if not citations:
        return _explicit_version_filter(
            f"{conversation_summary}\n{previous_user_query}"
        )
    source_ids = sorted(
        {
            str(item.get("source_id", "")).strip()
            for item in citations
            if str(item.get("source_id", "")).strip()
        }
    )
    versions = sorted(
        {
            str(item.get("version", "")).strip()
            for item in citations
            if str(item.get("version", "")).strip()
        }
    )
    statuses = sorted(
        {
            str(item.get("status", "")).strip()
            for item in citations
            if str(item.get("status", "")).strip()
        }
    )
    result: dict[str, object] = {}
    if source_ids:
        result["source_ids"] = source_ids
    if versions:
        result["versions"] = versions
    if statuses:
        result["statuses"] = statuses
    return result or None


def _explicit_version_filter(query: str) -> dict[str, object] | None:
    """Turn explicit current/old version language into retrieval constraints."""

    normalized_query = "".join(query.casefold().split())
    if any(
        marker in normalized_query
        for marker in ["现行", "当前", "最新", "目前", "现在", "有效", "生效"]
    ):
        return {"statuses": ["active"], "prefer_current": True}
    if any(
        marker in normalized_query
        for marker in ["旧版", "历史版本", "过期版", "之前版本"]
    ):
        return {"statuses": ["deprecated"], "prefer_current": False}
    version_match = re.search(r"(?<![a-z])v(\d+)(?!\d)", normalized_query)
    if version_match:
        return {"versions": [f"v{version_match.group(1)}"], "prefer_current": False}
    return None


def _access_context_for(runtime: Runtime[RunContext] | None) -> ServerAccessContext | None:
    context = getattr(runtime, "context", None)
    access_context = getattr(context, "access_context", None)
    return access_context if isinstance(access_context, ServerAccessContext) else None


def _authorization_filter(
    runtime: Runtime[RunContext] | None,
) -> RetrievalFilter | None:
    """Compile the server ACL before dense/BM25 fusion for this request."""

    access_context = _access_context_for(runtime)
    if access_context is None:
        return None
    try:
        documents = get_retriever().documents
    except Exception:
        # An unavailable authorization index must not become an allow-all.
        return RetrievalFilter(allowed_document_ids=frozenset())
    return KnowledgeAccessPolicy().retrieval_filter(access_context, documents)


def _retrieval_filter_from_state(
    state: AgentState,
    runtime: Runtime[RunContext] | None = None,
) -> RetrievalFilter | None:
    raw = state.get("retrieval_filter")
    if not isinstance(raw, dict):
        return _authorization_filter(runtime)

    def values(name: str) -> frozenset[str] | None:
        value = raw.get(name)
        if not isinstance(value, (list, tuple, set, frozenset)):
            return None
        clean = {str(item).strip() for item in value if str(item).strip()}
        return frozenset(clean) if clean else None

    state_filter = RetrievalFilter(
        source_ids=values("source_ids"),
        versions=values("versions"),
        departments=values("departments"),
        statuses=values("statuses"),
        access_scopes=values("access_scopes"),
        as_of=raw.get("as_of"),
        prefer_current=raw.get("prefer_current")
        if isinstance(raw.get("prefer_current"), bool)
        else None,
    )
    authorization_filter = _authorization_filter(runtime)
    if authorization_filter is None:
        return state_filter
    if state_filter is None:
        return authorization_filter
    return RetrievalFilter(
        source_ids=state_filter.source_ids,
        versions=state_filter.versions,
        departments=state_filter.departments,
        statuses=state_filter.statuses,
        access_scopes=state_filter.access_scopes,
        allowed_document_ids=authorization_filter.allowed_document_ids,
        denied_document_ids=authorization_filter.denied_document_ids,
        as_of=state_filter.as_of,
        prefer_current=state_filter.prefer_current,
    )


async def route_query(
    state: AgentState,
    runtime: Runtime[RunContext] | None = None,
) -> dict[str, Any]:
    """Use the LLM to select the execution route."""

    query = state.get("query") or latest_user_text(state.get("messages", []))
    conversation_summary = state.get("conversation_summary", "").strip()
    recent_conversation = render_messages(list(state.get("messages", [])))
    should_rewrite_query = _looks_like_contextual_follow_up(query)
    retrieval_filter = _contextual_retrieval_filter(
        list(state.get("messages", [])),
        query,
        conversation_summary,
    )
    mode = state.get("mode", "knowledge")
    prompt = (
        "你是一个企业内部知识助手的路由器。请仅返回JSON对象，不要输出多余文字。\n"
        "可选路由：\n"
        '1. "rag"：问题应通过企业内部知识库检索回答。\n'
        '2. "tool_call"：问题需要调用工具，例如当前时间。\n'
        '3. "direct"：可以直接回答，不需要检索或工具。\n'
        "返回格式：{\"route\": \"rag|tool_call|direct\", \"reason\": \"简短原因\"}\n"
        "会话摘要和最近对话只用于理解当前问题的指代与所属主题。\n"
        f"会话摘要：\n{conversation_summary or '无'}\n"
        f"最近对话：\n{recent_conversation or '无'}\n"
        f"当前用户问题：{query}\n"
        f"当前模式：{mode}。knowledge 模式只能使用企业知识库，general 模式可直接回答开放问题。\n"
    )

    heuristic_input = query
    if _looks_like_contextual_follow_up(query):
        heuristic_input = f"{conversation_summary}\n{recent_conversation}\n{query}"
    route = _heuristic_route(heuristic_input)
    if mode == "general":
        if state.get("web_search"):
            route = "tool_call"
        else:
            route = "tool_call" if _looks_like_web_query(heuristic_input) else "direct"
    elif mode == "knowledge" and route == "direct":
        route = "rag"
    call_started_at = time.perf_counter()
    model_call_reserved = False
    try:
        model_call_reserved = _reserve_model_call(runtime, "route_query")
        model = _build_model(temperature=0)
        async with budget_deadline(_budget_for(runtime), "route_query"):
            response = await model.ainvoke([SystemMessage(content=prompt)])
        _finish_model_call(runtime, response)
        parsed = _extract_json_object(_as_text(response))
        candidate = str(parsed.get("route", "")).strip()
        if candidate in {"rag", "tool_call", "direct"} and mode == "general":
            route = "tool_call" if state.get("web_search") or candidate == "tool_call" or _looks_like_web_query(heuristic_input) else "direct"
        elif candidate in {"rag", "tool_call", "direct"} and mode == "knowledge":
            route = "tool_call" if candidate == "tool_call" else "rag"
    except BudgetExceededError:
        return {
            "route": route,
            "should_rewrite_query": should_rewrite_query,
            "retrieval_filter": retrieval_filter,
            **_budget_failure(
                runtime,
                node="route_query",
                state=state,
                model_call_reserved=model_call_reserved,
            ),
        }
    except Exception:
        pass

    return {
        "route": route,
        "should_rewrite_query": should_rewrite_query,
        "retrieval_filter": retrieval_filter,
        "status_events": [f"route_query:{route}"],
        **record_call_stats(state, model_calls=1, started_at=call_started_at),
        **_budget_fields(runtime),
    }


async def retrieve(
    state: AgentState,
    runtime: Runtime[RunContext] | None = None,
) -> dict[str, Any]:
    """Fetch the most relevant documents from Chroma."""

    query = state.get("rewritten_query") or state.get("query") or latest_user_text(
        state.get("messages", [])
    )
    try:
        budget = _budget_for(runtime)
        if budget is not None:
            budget.ensure_available()
    except BudgetExceededError:
        return _budget_failure(
            runtime,
            node="retrieve",
            state=state,
        )
    try:
        async with budget_deadline(_budget_for(runtime), "retrieve"):
            retrieval = await search_documents_with_metadata(
                query,
                top_k=settings.retrieval_top_k,
                filters=_retrieval_filter_from_state(state, runtime),
            )
        documents = retrieval.documents
        retrieval_metadata = {
            "strategy": retrieval.strategy,
            "dense_candidates": retrieval.dense_candidates,
            "lexical_candidates": retrieval.lexical_candidates,
            "fused_candidates": retrieval.fused_candidates,
            "filtered_candidate_count": retrieval.filtered_candidate_count,
            "rerank_used": retrieval.rerank_used,
            "degraded_reason": retrieval.degraded_reason,
            "latency_ms": retrieval.latency_ms,
            "applied_filter": retrieval.applied_filter,
        }
        budget = _budget_for(runtime)
        if budget is not None:
            budget.ensure_available()
    except BudgetExceededError:
        return _budget_failure(
            runtime,
            node="retrieve",
            state=state,
        )
    except Exception:
        documents = []
        retrieval_metadata = {
            "strategy": settings.retrieval_strategy,
            "runtime_error": RETRIEVAL_ERROR_CODE,
        }
        return {
            "retrieved_docs": documents,
            "retrieval_metadata": retrieval_metadata,
            "failure_stage": "retrieval",
            "failure_reason": "知识库检索调用失败。",
            "status_events": ["retrieve:error"],
            **_budget_fields(runtime),
        }
    return {
        "retrieved_docs": documents,
        "retrieval_metadata": retrieval_metadata,
        "failure_stage": None,
        "failure_reason": None,
        "status_events": ["retrieve"],
        **_budget_fields(runtime),
    }


async def grade_documents(
    state: AgentState,
    runtime: Runtime[RunContext] | None = None,
) -> dict[str, Any]:
    """Judge whether the retrieved documents are relevant to the query."""

    query = state.get("rewritten_query") or state.get("query", "")
    documents = state.get("retrieved_docs", [])
    if not documents:
        previous_stage = state.get("failure_stage")
        previous_reason = state.get("failure_reason")
        return {
            "is_relevant": False,
            "failure_stage": (
                previous_stage
                if previous_stage in {"retrieval", "evidence"}
                else "evidence"
            ),
            "failure_reason": previous_reason or "检索没有返回候选文档。",
            "status_events": ["grade_documents:no_docs"],
            **_budget_fields(runtime),
        }

    prompt = (
        "你是一个企业内部知识库相关性判断器。请仅返回JSON对象。\n"
        "返回格式：{\"is_relevant\": true|false, \"reason\": \"简短原因\"}\n"
        f"用户问题：{query}\n"
        f"候选知识：\n{_format_documents(documents)}"
    )
    score = keyword_overlap_score(query, [doc.page_content for doc in documents])
    is_relevant = score >= 0.08
    call_started_at = time.perf_counter()
    model_call_reserved = False
    try:
        model_call_reserved = _reserve_model_call(runtime, "grade_documents")
        model = _build_model(temperature=0)
        async with budget_deadline(_budget_for(runtime), "grade_documents"):
            response = await model.ainvoke([SystemMessage(content=prompt)])
        _finish_model_call(runtime, response)
        parsed = _extract_json_object(_as_text(response))
        candidate = parsed.get("is_relevant")
        if isinstance(candidate, bool):
            is_relevant = candidate
    except BudgetExceededError:
        return {
            "is_relevant": False,
            **_budget_failure(
                runtime,
                node="grade_documents",
                state=state,
                model_call_reserved=model_call_reserved,
            ),
        }
    except Exception:
        pass

    return {
        "is_relevant": is_relevant,
        "failure_stage": None if is_relevant else "relevance",
        "failure_reason": None if is_relevant else "检索候选与当前问题不够相关。",
        "status_events": [f"grade_documents:{is_relevant}"],
        **record_call_stats(state, model_calls=1, started_at=call_started_at),
        **_budget_fields(runtime),
    }


async def rewrite_query(
    state: AgentState,
    runtime: Runtime[RunContext] | None = None,
) -> dict[str, Any]:
    """Use conversation context to produce a standalone retrieval query."""

    query = state.get("query", "")
    contextual_rewrite = bool(state.get("should_rewrite_query"))
    retry_count = state.get("retrieval_retry_count", 0) + (
        0 if contextual_rewrite else 1
    )
    rewritten_query = query
    conversation_summary = state.get("conversation_summary", "").strip()
    messages = list(state.get("messages", []))
    recent_conversation = render_messages(messages)
    previous_user_query = _previous_user_text(messages, query)
    contextual_source = "\n".join(
        part for part in [conversation_summary, previous_user_query] if part
    )
    contextual_fallback = " ".join(
        part
        for part in [
            conversation_summary[:400],
            previous_user_query[-300:],
            query,
        ]
        if part
    )

    prompt = (
        "请结合会话摘要、最近对话和当前问题，将用户追问改写成能够独立理解、"
        "适合企业内部知识库检索的短查询，仅返回JSON。\n"
        "需要消除‘这个、那个、它、超过该金额’等指代，并补全历史中明确出现的"
        "制度名称、业务对象和限制条件。不得回答问题，不得添加历史中没有的信息。\n"
        "输出必须是完整检索问题，不能只返回金额、日期或单个关键词。示例：历史讨论"
        "差旅报销制度，当前问‘超过5000元呢？’，应改写为‘差旅报销金额超过5000元"
        "时的审批流程’，不能只返回‘5000’。\n"
        '返回格式：{"rewritten_query": "..." }\n'
        f"会话摘要：\n{conversation_summary or '无'}\n"
        f"最近对话：\n{recent_conversation or '无'}\n"
        f"当前用户问题：{query}\n"
    )
    call_started_at = time.perf_counter()
    model_call_reserved = False
    try:
        model_call_reserved = _reserve_model_call(runtime, "rewrite_query")
        model = _build_model(temperature=0)
        async with budget_deadline(_budget_for(runtime), "rewrite_query"):
            response = await model.ainvoke([SystemMessage(content=prompt)])
        _finish_model_call(runtime, response)
        parsed = _extract_json_object(_as_text(response))
        candidate = str(parsed.get("rewritten_query", "")).strip()
        if candidate:
            if (
                _looks_like_contextual_follow_up(query)
                and contextual_source
                and not _rewrite_preserves_context(
                    candidate,
                    contextual_source,
                )
            ):
                rewritten_query = contextual_fallback
            else:
                rewritten_query = candidate
    except BudgetExceededError:
        return {
            "rewritten_query": rewritten_query,
            "should_rewrite_query": False,
            "retrieval_retry_count": retry_count,
            **_budget_failure(
                runtime,
                node="rewrite_query",
                state=state,
                model_call_reserved=model_call_reserved,
            ),
        }
    except Exception:
        if _looks_like_contextual_follow_up(query) and contextual_source:
            rewritten_query = contextual_fallback
        elif "报销" in query or "费用" in query:
            rewritten_query = f"{query} 报销制度 财务流程"
        elif "请假" in query or "休假" in query:
            rewritten_query = f"{query} 请假制度 人事流程"
        elif "合同" in query or "审批" in query:
            rewritten_query = f"{query} 合同审批规范"

    return {
        "rewritten_query": rewritten_query,
        "should_rewrite_query": False,
        "retrieval_retry_count": retry_count,
        "failure_stage": None,
        "failure_reason": None,
        "status_events": [f"rewrite_query:{retry_count}"],
        **record_call_stats(state, model_calls=1, started_at=call_started_at),
        **_budget_fields(runtime),
    }


async def fallback_answer(
    state: AgentState,
    runtime: Runtime[RunContext] | None = None,
) -> dict[str, Any]:
    """Return a safe fallback answer when retrieval fails."""

    failure_stage = state.get("failure_stage")
    if state.get("generation_error") == BUDGET_EXHAUSTED_CODE or (
        state.get("failure_reason") == BUDGET_EXHAUSTED_CODE
    ):
        answer = "本次请求已达到资源限制，未提交未经验证的回答，请稍后重试。"
        fallback_reason = "budget_exhausted"
        failure_stage = "runtime"
        failure_reason = BUDGET_EXHAUSTED_CODE
    elif state.get("generation_error") or failure_stage == "generation":
        answer = "回答生成服务暂时不可用，当前无法可靠作答，请稍后重试。"
        fallback_reason = "generation_error"
        failure_stage = "generation"
        failure_reason = "回答生成失败，无法交付未经验证的模型输出。"
    elif failure_stage == "tool":
        answer = "当前工具暂时不可用，无法可靠获取所需信息，请稍后重试。"
        fallback_reason = "tool_error"
        failure_reason = safe_diagnostic(
            state.get("failure_reason") or "工具调用失败。",
            fallback=INTERNAL_ERROR_CODE,
        )
    elif state.get("is_relevant") is False or not state.get("retrieved_docs"):
        answer = (
            "这个问题不属于当前知识问答模式的企业知识范围。"
            "我只能依据企业知识库回答；如需开放问答，请切换到通用模式。"
            if state.get("mode", "knowledge") == "knowledge"
            else "我没有在企业内部知识库中找到足够相关的信息。请补充更具体的制度名称、流程名称或部门信息，我再继续检索。"
        )
        fallback_reason = "retrieval_exhausted"
        failure_stage = state.get("failure_stage") or "retrieval"
        failure_reason = safe_diagnostic(
            state.get("failure_reason")
            or "检索重试耗尽，仍未找到足够相关的企业知识证据。",
            fallback=INTERNAL_ERROR_CODE,
        )
    elif state.get("hallucination_pass") is False:
        answer = "我没有在企业内部知识库中找到足够支持该回答的证据。请补充更具体的制度名称、流程名称或部门信息，我再继续检索。"
        fallback_reason = "hallucination_exhausted"
        failure_stage = state.get("failure_stage") or "hallucination"
        failure_reason = safe_diagnostic(
            state.get("failure_reason")
            or state.get("hallucination_reason")
            or "回答未通过证据一致性校验。",
            fallback=INTERNAL_ERROR_CODE,
        )
    else:
        answer = "当前无法生成经过验证的回答，请稍后重试。"
        fallback_reason = "unknown"
        failure_stage = "runtime"
        failure_reason = "流程未生成可提交的正式答案。"
    return {
        "candidate_answer": answer,
        "candidate_citations": [],
        "answer_disposition": "fallback",
        "generation_instruction": "",
        "attempt_history": list(state.get("attempt_history", []))[-10:],
        "status_events": ["fallback_answer"],
        "hallucination_pass": False,
        "citations": [],
        "fallback_reason": fallback_reason,
        "failure_stage": failure_stage,
        "failure_reason": failure_reason,
        **_budget_fields(runtime),
    }


async def commit_answer(
    state: AgentState,
    runtime: Runtime[RunContext] | None = None,
) -> dict[str, Any]:
    """Persist the only answer that is allowed into conversation history."""

    disposition = state.get("answer_disposition", "pending")
    candidate = str(state.get("candidate_answer", "")).strip()
    if (
        disposition not in {"accepted", "fallback"}
        or not candidate
        or (disposition == "accepted" and str(state.get("generation_error", "")).strip())
        or (disposition == "accepted" and state.get("hallucination_pass") is not True)
    ):
        return {
            "status_events": ["commit_answer:pending"],
            **_budget_fields(runtime),
        }

    turn_id = str(state.get("turn_id", "")).strip() or str(uuid.uuid4())
    citations = state.get("candidate_citations", []) if disposition == "accepted" else []
    if not isinstance(citations, list):
        citations = []
    result: dict[str, Any] = {
        "turn_id": turn_id,
        "answer": candidate,
        "citations": citations,
        "messages": [
            AIMessage(
                content=candidate,
                id=f"{turn_id}:assistant",
                additional_kwargs={"citations": citations},
            )
        ],
        "candidate_answer": "",
        "candidate_citations": [],
        "generation_instruction": "",
        "attempt_history": list(state.get("attempt_history", []))[-10:],
        "status_events": ["commit_answer"],
        **_budget_fields(runtime),
    }
    if disposition == "accepted":
        result.update(
            {
                "hallucination_pass": True,
                "hallucination_reason": "",
                "failure_stage": None,
                "failure_reason": None,
                "generation_error": "",
                "fallback_reason": "",
            }
        )
    return result


def _choose_tool_result(
    query: str,
    mode: str = "knowledge",
    web_search: bool = False,
    retrieval_filter: RetrievalFilter | None = None,
) -> ToolResult:
    """Pick the most suitable tool and return its structured result."""

    lowered = query.lower()
    if any(
        keyword in lowered
        for keyword in ["时间", "几点", "日期", "today", "now", "current time"]
    ):
        return get_current_time_result()
    if mode == "general" and (web_search or _looks_like_web_query(query)):
        return search_web_result(query)
    return search_knowledge_base_result(query, filters=retrieval_filter)


def _choose_tool_output(
    query: str,
    mode: str = "knowledge",
    web_search: bool = False,
) -> str:
    """Keep the legacy string-only tool selection contract."""

    return render_tool_output(_choose_tool_result(query, mode, web_search))


async def tool_executor(
    state: AgentState,
    runtime: Runtime[RunContext] | None = None,
) -> dict[str, Any]:
    """Execute the selected tool and store the output."""

    query = state.get("query") or latest_user_text(state.get("messages", []))
    call_started_at = time.perf_counter()
    tool_call_reserved = False
    retrieval_filter = _retrieval_filter_from_state(state, runtime)
    try:
        tool_call_reserved = _reserve_tool_call(runtime, "tool_executor")
        async with budget_deadline(_budget_for(runtime), "tool_executor"):
            tool_result = await asyncio.to_thread(
                _choose_tool_result,
                query,
                state.get("mode", "knowledge"),
                bool(state.get("web_search")),
                retrieval_filter,
            )
        budget = _budget_for(runtime)
        if budget is not None:
            budget.ensure_available()
    except BudgetExceededError:
        return {
            **_budget_failure(
                runtime,
                node="tool_executor",
                state=state,
                tool_call_reserved=tool_call_reserved,
            ),
            "tool_result": None,
            "tool_output": "",
        }
    except Exception:
        tool_result = tool_failure("tool_unavailable")
    tool_result = normalize_tool_result(tool_result)
    tool_output = render_tool_output(tool_result)
    tool_failed = not bool(tool_result.get("ok"))
    failure_reason = str(tool_result.get("error") or "工具调用失败。")[:500]
    return {
        "tool_result": tool_result,
        "tool_output": tool_output,
        "failure_stage": "tool" if tool_failed else None,
        "failure_reason": failure_reason if tool_failed else None,
        "status_events": ["tool_executor"],
        **record_call_stats(state, tool_calls=1, started_at=call_started_at),
        **_budget_fields(runtime),
    }


async def generate(
    state: AgentState,
    runtime: Runtime[RunContext] | None = None,
) -> dict[str, Any]:
    """Generate an answer candidate without mutating formal conversation history."""

    budget = _budget_for(runtime)
    if budget is not None and budget.exhausted_reason is not None:
        return {
            "candidate_answer": "",
            "candidate_citations": [],
            "answer_disposition": "pending",
            **_budget_failure(
                runtime,
                node="generate",
                state=state,
            ),
        }
    query = state.get("query") or latest_user_text(state.get("messages", []))
    docs = state.get("retrieved_docs", [])
    tool_output = state.get("tool_output", "")
    route = state.get("route", "direct")
    conversation_summary = state.get("conversation_summary", "").strip()
    mode = state.get("mode", "knowledge")
    generation_instruction = state.get("generation_instruction", "").strip()
    mode_instructions = (
        "当前是知识问答模式：只能依据企业知识库证据回答。若问题与企业知识无关，明确拒答，并建议用户切换到通用模式。"
        if mode == "knowledge"
        else "当前是通用模式：可以回答开放问题；需要实时信息时使用联网工具结果。没有工具结果时不要伪造已联网或来源。"
    )

    system_prompt = (
        "你是企业内部知识助手。你的职责是基于企业内部制度、流程、规范和文档回答问题。\n"
        "请用中文回答，语气自然、专业、简洁。\n"
        "你面向企业内部员工，主要支持制度查询、流程说明、审批规则、合同与法务、财务与人事等内部事务。\n"
        f"{mode_instructions}\n"
        "回答时聚焦企业内部知识，不要主动扩展到无关主题。\n"
        "如果知识依据不足，不要臆测，直接说明信息不足并指出建议补充的信息。\n"
        "较早对话摘要只用于理解用户指代和连续意图，不是企业知识证据；RAG 回答仍只能使用检索原文。\n"
        "RAG 路由必须只使用检索证据回答。每个事实、数字、日期或规则后都要添加对应的 [C1]、[C2] 引用标记。\n"
        "引用编号只能使用下方证据已有的编号；不得编造编号，不得用常识补充证据中没有的信息，也不要自行计算证据未直接给出的结果。\n"
        "优先用一至三句话直接回答问题；除非问题明确要求，不要扩展背景、建议、示例、法律后果或证据没有明示的推论。\n"
        "不得扩展解释证据未直接写明的救济、责任或程序结论，也不要添加证据未写明的条款号。\n"
        "回答到问题所需的最小充分信息后立即停止，不要解释该规则意味着什么。\n"
        "只回答问题明确询问的事实；不得补充未被询问的岗位资质、一般义务、背景知识或关联规则。\n"
        "如果一句话已经完整回答，必须在第一句话后停止，不得追加第二句说明。\n"
        "检索到文档不代表文档包含答案；如果没有原文直接回答问题，只输出：检索到的知识未包含该问题的答案。不要添加引用或背景解释。\n"
        "优先遵守以下信息：\n"
        f"较早对话摘要：{conversation_summary or '无'}\n"
        f"路由类型：{route}\n"
        f"工具结果：{tool_output or '无'}\n"
        f"检索到的知识：\n{_format_documents(docs)}\n"
        "如果用户只是打招呼或询问你能做什么，请简要介绍你支持的企业内部知识能力。"
    )
    if generation_instruction:
        system_prompt += (
            "\n上一轮校验反馈（仅用于修正回答，不是新的证据）：\n"
            f"{generation_instruction}\n"
            "请删除反馈指出的无证据内容；如果证据仍不足，直接安全拒答。"
        )

    prompt_messages: list[BaseMessage] = [SystemMessage(content=system_prompt)]
    prompt_messages.extend(state.get("messages", []))
    if not prompt_messages or not isinstance(prompt_messages[-1], HumanMessage):
        prompt_messages.append(HumanMessage(content=query))

    answer = ""
    generation_error = ""
    call_started_at = time.perf_counter()
    model_call_reserved = False
    stream_usage: tuple[int, int, float | None] | None = None
    try:
        model_call_reserved = _reserve_model_call(runtime, "generate")
        model = _build_model(temperature=0 if route == "rag" else 0.2)
        async with budget_deadline(_budget_for(runtime), "generate"):
            async for chunk in model.astream(prompt_messages):
                answer += _as_text(chunk)
                usage = extract_usage(chunk)
                if usage[0] or usage[1] or usage[2] is not None:
                    stream_usage = usage
        _finish_stream_call(runtime, stream_usage)
    except BudgetExceededError:
        return {
            "candidate_answer": "",
            "candidate_citations": [],
            "answer_disposition": "pending",
            **_budget_failure(
                runtime,
                node="generate",
                state=state,
                model_call_reserved=model_call_reserved,
            ),
        }
    except Exception:
        generation_error = GENERATION_ERROR_CODE
        answer = ""

    if route == "rag" and not generation_error:
        answer = sanitize_answer_citations(answer, docs, query=query)
        citations = build_citations(docs, answer, query=query)
    else:
        citations = []
    return {
        "candidate_answer": answer,
        "candidate_citations": citations,
        "answer_disposition": "pending",
        "generation_error": generation_error,
        "status_events": ["generate"],
        **record_call_stats(state, model_calls=1, started_at=call_started_at),
        **_budget_fields(runtime),
    }


async def check_hallucination(
    state: AgentState,
    runtime: Runtime[RunContext] | None = None,
) -> dict[str, Any]:
    """Validate that the answer is grounded in the retrieved evidence."""

    route = state.get("route", "direct")
    answer = str(state.get("candidate_answer", state.get("answer", "")))
    docs = state.get("retrieved_docs", [])
    generation_error = str(state.get("generation_error", "")).strip()
    if generation_error:
        return {
            "hallucination_pass": False,
            "answer_disposition": "pending",
            "candidate_citations": [],
            "attempt_history": _append_attempt(
                state,
                stage="generation",
                passed=False,
                reason="回答生成模型调用失败。",
            ),
            **record_call_stats(state, model_calls=0),
            "failure_stage": "generation",
            "failure_reason": "回答生成模型调用失败。",
            "hallucination_reason": "回答生成模型调用失败。",
            "status_events": ["check_hallucination:generation_error"],
            **_budget_fields(runtime),
        }
    if state.get("failure_stage") in {"tool", "runtime"}:
        reason = safe_diagnostic(
            state.get("failure_reason") or "工具或运行时调用失败。",
            fallback=INTERNAL_ERROR_CODE,
        )
        return {
            "hallucination_pass": False,
            "answer_disposition": "pending",
            "candidate_citations": [],
            "attempt_history": _append_attempt(
                state,
                stage=str(state.get("failure_stage")),
                passed=False,
                reason=reason,
            ),
            **record_call_stats(state, model_calls=0),
            "failure_stage": state.get("failure_stage"),
            "failure_reason": reason,
            "hallucination_reason": reason,
            "generation_instruction": "外部调用失败，请不要基于错误文本生成答案。",
            "status_events": ["check_hallucination:external_error"],
            **_budget_fields(runtime),
        }
    if route == "rag" and not docs:
        return {
            "hallucination_pass": False,
            "answer_disposition": "pending",
            "candidate_citations": [],
            "hallucination_retry_count": (
                state.get("hallucination_retry_count", 0) + 1
            ),
            "attempt_history": _append_attempt(
                state,
                stage="evidence",
                passed=False,
                reason="没有可供回答的检索证据。",
            ),
            **record_call_stats(state, model_calls=0),
            "failure_stage": "evidence",
            "failure_reason": "没有可供回答的检索证据。",
            "hallucination_reason": "没有可供回答的检索证据。",
            "generation_instruction": "没有可供回答的检索证据，请安全拒答。",
            "status_events": ["check_hallucination:no_docs"],
            **_budget_fields(runtime),
        }
    if not answer.strip():
        return {
            "hallucination_pass": False,
            "answer_disposition": "pending",
            "candidate_citations": [],
            "attempt_history": _append_attempt(
                state,
                stage="generation",
                passed=False,
                reason="模型没有生成可提交的答案。",
            ),
            **record_call_stats(state, model_calls=0),
            "failure_stage": "generation",
            "failure_reason": "模型没有生成可提交的答案。",
            "hallucination_reason": "模型没有生成可提交的答案。",
            "status_events": ["check_hallucination:empty_answer"],
            **_budget_fields(runtime),
        }
    if route in {"tool_call", "direct"}:
        return {
            "hallucination_pass": True,
            "answer_disposition": "accepted",
            "candidate_citations": [],
            "attempt_history": _append_attempt(
                state,
                stage="semantic_skip",
                passed=True,
            ),
            **record_call_stats(state, model_calls=0),
            "hallucination_reason": "",
            "failure_stage": None,
            "failure_reason": None,
            "status_events": ["check_hallucination:skip"],
            **_budget_fields(runtime),
        }
    resolved_citations = build_citations(docs, answer, query=state.get("query", ""))
    citation_evidence = [
        {
            "citation_id": citation["citation_id"],
            "quote": citation["quote"],
        }
        for citation in resolved_citations
    ]
    prompt = (
        "请判断回答是否忠实于给定的企业内部知识，仅返回JSON。\n"
        '返回格式：{"hallucination_pass": true|false, "reason": "简短原因"}\n'
        f"问题：{state.get('query', '')}\n"
        f"回答：{answer}\n"
        f"知识：\n{_format_documents(docs)}\n"
        "引用与对应原文（只能用对应引用支持对应断言）：\n"
        f"{json.dumps(citation_evidence, ensure_ascii=False)}"
    )

    pass_check = False
    judge_reason = ""
    call_started_at = time.perf_counter()
    model_call_reserved = False
    try:
        model_call_reserved = _reserve_model_call(runtime, "check_hallucination")
        model = _build_model(
            temperature=0,
            model_name=settings.judge_model_name,
        )
        async with budget_deadline(_budget_for(runtime), "check_hallucination"):
            response = await model.ainvoke([SystemMessage(content=prompt)])
        _finish_model_call(runtime, response)
        parsed = _extract_json_object(_as_text(response))
        candidate = parsed.get("hallucination_pass")
        if isinstance(candidate, bool):
            pass_check = candidate
        judge_reason = str(parsed.get("reason", "")).strip()
    except BudgetExceededError:
        return {
            "hallucination_pass": False,
            "answer_disposition": "pending",
            "candidate_citations": [],
            "attempt_history": _append_attempt(
                state,
                stage="runtime",
                passed=False,
                reason="请求预算已耗尽。",
            ),
            "hallucination_reason": BUDGET_EXHAUSTED_CODE,
            **_budget_failure(
                runtime,
                node="check_hallucination",
                state=state,
                model_call_reserved=model_call_reserved,
            ),
        }
    except Exception:
        pass_check = False
        judge_reason = "证据一致性判断服务不可用，已安全拒绝未经验证的回答。"

    resolved_ids = {citation["citation_id"] for citation in resolved_citations}
    markers = citation_markers(answer)
    basic_citations_valid = (
        bool(markers)
        and markers == resolved_ids
        and bool(resolved_citations)
    )
    citation_alignment_valid, citation_alignment_reason = (
        validate_citation_claim_alignment(answer, resolved_citations)
        if basic_citations_valid
        else validate_citation_claim_alignment(answer, resolved_citations)
    )
    citationless_abstention = (
        not markers
        and not resolved_citations
        and citation_alignment_valid
    )
    citations_valid = (
        (basic_citations_valid or citationless_abstention)
        and citation_alignment_valid
    )
    pass_check = pass_check and citations_valid
    if pass_check:
        reason = ""
    elif not basic_citations_valid:
        reason = "回答中的引用标记缺失、越界或无法对应当前检索证据。"
    elif not citation_alignment_valid:
        reason = citation_alignment_reason
    else:
        reason = judge_reason or "回答包含检索证据未支持的事实。"

    result: dict[str, Any] = {
        "hallucination_pass": pass_check,
        "candidate_citations": resolved_citations,
        "answer_disposition": "accepted" if pass_check else "pending",
        "attempt_history": _append_attempt(
            state,
            stage="citation" if not citations_valid else "hallucination",
            passed=pass_check,
            reason=reason,
        ),
        "hallucination_reason": reason,
        "failure_stage": None if pass_check else ("citation" if not citations_valid else "hallucination"),
        "failure_reason": None if pass_check else reason,
        "generation_instruction": "" if pass_check else reason,
        "status_events": [f"check_hallucination:{pass_check}"],
        **record_call_stats(state, model_calls=1, started_at=call_started_at),
        **_budget_fields(runtime),
    }
    if not pass_check:
        result["hallucination_retry_count"] = (
            state.get("hallucination_retry_count", 0) + 1
        )
    return result
