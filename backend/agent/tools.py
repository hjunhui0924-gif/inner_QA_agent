"""Tool functions used by the agent."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo
import json
import math
from collections.abc import Mapping, Sequence
from typing import TypedDict

from langchain_core.tools import tool

from backend.config import settings
from backend.agent.memory import search_knowledge_base_data, search_knowledge_base_text
from backend.retrieval.engine import RetrievalFilter


class ToolResult(TypedDict):
    """Serializable result shared by tool execution and graph state."""

    ok: bool
    text: str
    sources: list[dict[str, object]]
    error: str | None


TOOL_ERROR_MESSAGES = {
    "knowledge_base_unavailable": "知识库暂时不可用，请稍后重试。",
    "web_search_unavailable": "联网搜索暂时不可用，请稍后重试。",
    "web_search_no_results": "没有找到可用的网页来源，请调整关键词后重试。",
    "web_answer_invalid": "已获得网页来源，但回答未通过引用校验，请重试。",
    "time_unavailable": "时间服务暂时不可用，请稍后重试。",
    "tool_unavailable": "工具暂时不可用，请稍后重试。",
}

MAX_TOOL_TEXT_CHARS = 12_000
MAX_TOOL_SOURCES = 10
MAX_SOURCE_FIELDS = 24
MAX_SOURCE_FIELD_CHARS = 500
MAX_SOURCE_SNIPPET_CHARS = 1_200


def _bounded_text(value: object, *, limit: int = 12_000) -> str:
    """Convert tool output to bounded plain text."""

    return str(value or "").strip()[:limit]


def _json_safe(
    value: object,
    *,
    depth: int = 0,
    seen: set[int] | None = None,
) -> object:
    """Keep source metadata finite, bounded, cycle-safe, and JSON serializable."""

    if value is None or isinstance(value, (str, bool, int)):
        return _bounded_text(value, limit=MAX_SOURCE_FIELD_CHARS) if isinstance(value, str) else value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite metadata")
        return value
    if depth >= 3:
        return "[nested metadata omitted]"
    active_ids = seen if seen is not None else set()
    value_id = id(value)
    if value_id in active_ids:
        return "[cyclic metadata omitted]"
    active_ids.add(value_id)
    try:
        if type(value) is dict:
            normalized: dict[str, object] = {}
            for index, (key, item) in enumerate(value.items()):
                if index >= MAX_SOURCE_FIELDS:
                    break
                if not isinstance(key, str):
                    raise ValueError("non-string metadata key")
                key_name = key[:80]
                normalized[key_name] = _json_safe(
                    item,
                    depth=depth + 1,
                    seen=active_ids,
                )
            return normalized
        if type(value) is list:
            return [
                _json_safe(item, depth=depth + 1, seen=active_ids)
                for item in value[:MAX_SOURCE_FIELDS]
            ]
        raise ValueError("unsupported metadata value")
    finally:
        active_ids.remove(value_id)


def tool_success(
    text: object,
    *,
    sources: Sequence[Mapping[str, object]] | None = None,
) -> ToolResult:
    """Build a successful, serializable tool result."""

    if not isinstance(text, str):
        return tool_failure("tool_unavailable")
    if sources is None:
        sources = []
    if not isinstance(sources, list):
        return tool_failure("tool_unavailable")
    normalized_sources: list[dict[str, object]] = []
    try:
        for source in sources:
            if type(source) is not dict:
                return tool_failure("tool_unavailable")
            normalized: dict[str, object] = {}
            for index, (key, value) in enumerate(source.items()):
                if index >= MAX_SOURCE_FIELDS:
                    break
                if not isinstance(key, str):
                    return tool_failure("tool_unavailable")
                field_name = key[:80]
                normalized[field_name] = _json_safe(value)
                if isinstance(normalized[field_name], str):
                    field_limit = (
                        MAX_SOURCE_SNIPPET_CHARS
                        if field_name.casefold() in {"snippet", "quote", "text"}
                        else MAX_SOURCE_FIELD_CHARS
                    )
                    normalized[field_name] = normalized[field_name][:field_limit]
            normalized_sources.append(normalized)
            if len(normalized_sources) >= MAX_TOOL_SOURCES:
                break
    except (TypeError, ValueError, RecursionError):
        return tool_failure("tool_unavailable")
    return {
        "ok": True,
        "text": _bounded_text(text, limit=MAX_TOOL_TEXT_CHARS),
        "sources": normalized_sources,
        "error": None,
    }


def tool_failure(error: object, *, text: object = "") -> ToolResult:
    """Build a failed result without persisting error-context text."""

    code = error.strip() if isinstance(error, str) else ""
    if code not in TOOL_ERROR_MESSAGES:
        code = "tool_unavailable"
    return {
        "ok": False,
        "text": "",
        "sources": [],
        "error": code,
    }


def render_tool_output(result: Mapping[str, object]) -> str:
    """Render structured output for the legacy generation prompt field."""

    normalized = normalize_tool_result(result)
    if normalized["ok"]:
        text = _bounded_text(normalized["text"], limit=MAX_TOOL_TEXT_CHARS)
        return text or "工具未返回可用结果。"
    error_code = normalized["error"] or "tool_unavailable"
    message = TOOL_ERROR_MESSAGES.get(error_code, TOOL_ERROR_MESSAGES["tool_unavailable"])
    return f"工具调用失败：{message}"


def normalize_tool_result(value: object) -> ToolResult:
    """Validate a tool boundary at runtime and fail closed on malformed values."""

    try:
        if not isinstance(value, Mapping):
            return tool_failure("tool_unavailable")
        required = {"ok", "text", "sources", "error"}
        if not required.issubset(value):
            return tool_failure("tool_unavailable")
        ok = value.get("ok")
        text = value.get("text")
        sources = value.get("sources")
        error = value.get("error")
        if (
            not isinstance(ok, bool)
            or not isinstance(text, str)
            or not isinstance(sources, list)
            or any(type(source) is not dict for source in sources)
        ):
            return tool_failure("tool_unavailable")
        if error is not None and not isinstance(error, str):
            return tool_failure("tool_unavailable")
        if ok:
            if error is not None:
                return tool_failure("tool_unavailable")
            return tool_success(text, sources=sources)
        error_code = error if error in TOOL_ERROR_MESSAGES else "tool_unavailable"
        return tool_failure(error_code, text=text)
    except Exception:
        return tool_failure("tool_unavailable")


@tool
def get_current_time() -> str:
    """Return the current date and time in Beijing time.

    Returns:
        Current time formatted as ``YYYY-MM-DD HH:MM:SS``.
    """

    return datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")


def get_current_time_result() -> ToolResult:
    """Return the current time using the structured tool contract."""

    try:
        return tool_success(get_current_time.invoke({}))
    except Exception:
        return tool_failure("time_unavailable")


@tool
def search_knowledge_base(query: str) -> str:
    """Search the enterprise internal knowledge base for the most relevant content.

    Args:
        query: The internal question or search phrase.

    Returns:
        A text summary of the top matching internal knowledge entries.
    """

    return search_knowledge_base_text(query, top_k=settings.retrieval_top_k)


def search_knowledge_base_result(
    query: str,
    *,
    filters: RetrievalFilter | None = None,
) -> ToolResult:
    """Return knowledge search text and source metadata in a structured envelope."""

    try:
        text, sources, error = search_knowledge_base_data(
            query,
            top_k=settings.retrieval_top_k,
            filters=filters,
        )
        if error:
            return tool_failure(error, text=text)
        return tool_success(text, sources=sources)
    except Exception:
        return tool_failure("knowledge_base_unavailable")


def _search_web_payload(query: str) -> tuple[str, list[dict[str, object]]]:
    """Fetch a native grounded answer with its provider source indices."""
    from backend.agent.web_search import WebSearchError, search_sync
    result = search_sync(query)
    if not result["ok"]:
        raise WebSearchError(result["error"])
    return result["text"], result["sources"]


@tool
def search_web(query: str) -> str:
    """Search the web and return a source-backed answer for a general question."""
    return render_tool_output(search_web_result(query))


def search_web_result(query: str) -> ToolResult:
    """Synchronous entry; the graph uses native async search for cancellation."""
    from backend.agent.web_search import WebSearchError, web_citations
    try:
        text, sources = _search_web_payload(query)
        if not sources:
            return tool_failure("web_search_no_results")
        if not web_citations(text, sources):
            return tool_failure("web_answer_invalid")
        return tool_success(text, sources=sources)
    except WebSearchError as error:
        return tool_failure(error.code)
    except Exception:
        return tool_failure("web_search_unavailable")
