"""Conditional routing helpers."""

from __future__ import annotations

from backend.config import settings
from backend.agent.state import AgentState


def route_after_grading(state: AgentState) -> str:
    """Choose the next step after document grading."""

    if state.get("is_relevant"):
        return "generate"
    if state.get("retrieval_retry_count", 0) >= settings.max_retrieval_retries:
        return "fallback_answer"
    return "rewrite_query"


def route_after_hallucination_check(state: AgentState) -> str:
    """Choose whether to accept, retry, or persist the current answer."""

    if state.get("hallucination_pass"):
        return "update_memory"
    if state.get("hallucination_retry_count", 0) >= settings.max_hallucination_retries:
        return "update_memory"
    return "generate"


def route_after_routing(state: AgentState) -> str:
    """Route to the correct branch based on the query type."""

    return {
        "rag": "retrieve",
        "tool_call": "tool_executor",
        "direct": "generate",
    }[state["route"]]

