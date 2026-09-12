"""Stable public diagnostics for failures crossing agent boundaries."""

from __future__ import annotations

import re

GENERATION_ERROR_CODE = "generation_unavailable"
RUNTIME_ERROR_CODE = "runtime_unavailable"
RETRIEVAL_ERROR_CODE = "retrieval_unavailable"
RERANK_ERROR_CODE = "rerank_unavailable"
INTERNAL_ERROR_CODE = "internal_error"

SAFE_ERROR_CODES = frozenset(
    {
        GENERATION_ERROR_CODE,
        RUNTIME_ERROR_CODE,
        RETRIEVAL_ERROR_CODE,
        RERANK_ERROR_CODE,
        INTERNAL_ERROR_CODE,
        "knowledge_base_unavailable",
        "web_search_unavailable",
        "time_unavailable",
        "tool_unavailable",
        "generation_error",
        "reranker_not_configured",
        "request_budget_exhausted",
    }
)

SAFE_FALLBACK_REASONS = frozenset(
    {
        "generation_error",
        "tool_error",
        "retrieval_exhausted",
        "hallucination_exhausted",
        "unknown",
        "budget_exhausted",
    }
)

SAFE_REASON_VALUES = frozenset(
    {
        "回答生成模型调用失败。",
        "模型没有生成可提交的答案。",
        "没有可供回答的检索证据。",
        "检索没有返回候选文档。",
        "检索候选与当前问题不够相关。",
        "检索调用失败。",
        "检索重试耗尽，仍未找到足够相关的企业知识证据。",
        "回答未通过证据一致性校验。",
        "回答中的引用标记缺失、越界或无法对应当前检索证据。",
        "引用原文不支持对应的事实断言。",
        "回答包含检索证据未支持的事实。",
        "工具调用失败。",
        "流程未生成可提交的正式答案。",
        "知识库检索调用失败。",
        "外部调用失败，请不要基于错误文本生成答案。",
    }
)

SAFE_FAILURE_STAGES = frozenset(
    {
        "retrieval",
        "relevance",
        "evidence",
        "citation",
        "generation",
        "hallucination",
        "tool",
        "runtime",
    }
)

SAFE_ATTEMPT_STAGES = SAFE_FAILURE_STAGES | {"semantic_skip"}

def stable_error_code(value: object, *, fallback: str = INTERNAL_ERROR_CODE) -> str:
    """Return a known low-cardinality error code, never an exception message."""

    candidate = value.strip() if isinstance(value, str) else ""
    return candidate if candidate in SAFE_ERROR_CODES else fallback


def safe_diagnostic(
    value: object,
    *,
    fallback: str = INTERNAL_ERROR_CODE,
) -> str:
    """Keep only explicitly approved reasons at a public diagnostic boundary."""

    candidate = value.strip() if isinstance(value, str) else ""
    if not candidate:
        return ""
    if candidate in SAFE_ERROR_CODES or candidate in SAFE_REASON_VALUES:
        return candidate
    return fallback


def safe_fallback_reason(value: object, *, fallback: str = "unknown") -> str:
    """Allow only the finite fallback reason vocabulary in trace output."""

    candidate = value.strip() if isinstance(value, str) else ""
    return candidate if candidate in SAFE_FALLBACK_REASONS else fallback


def safe_failure_stage(value: object, *, fallback: str = "runtime") -> str:
    """Allow only the documented internal failure stages in traces and APIs."""

    candidate = value.strip() if isinstance(value, str) else ""
    return candidate if candidate in SAFE_FAILURE_STAGES else fallback


def safe_attempt_stage(value: object, *, fallback: str = "unknown") -> str:
    """Allow only known validation-attempt stage labels in traces."""

    candidate = value.strip() if isinstance(value, str) else ""
    return candidate if candidate in SAFE_ATTEMPT_STAGES else fallback


def safe_status_event(value: object, *, fallback: str = "event_omitted") -> str:
    """Keep known low-cardinality status events without exposing diagnostics."""

    candidate = value.strip() if isinstance(value, str) else ""
    if candidate.startswith("error:"):
        return "error:runtime_unavailable"
    if re.fullmatch(
        r"(?:manage_conversation_context|inject_memory|route_query|retrieve|"
        r"grade_documents|rewrite_query|tool_executor|generate|"
        r"check_hallucination|fallback_answer|commit_answer|update_memory)"
        r"(?::(?:unchanged|summarized|error|true|false|no_docs|skip|"
        r"empty_answer|generation_error|external_error|pending|[0-9]+|"
        r"budget_exhausted|rag|tool_call|direct))?",
        candidate,
        flags=re.IGNORECASE,
    ):
        return candidate[:300]
    return fallback
