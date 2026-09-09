"""LangGraph state definition."""

from __future__ import annotations

from typing import Annotated, Literal, TypedDict

from langchain_core.documents import Document
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

from backend.agent.citations import Citation


class AgentState(TypedDict, total=False):
    """State shared across the customer service graph."""

    messages: Annotated[list[BaseMessage], add_messages]
    user_id: str
    session_id: str
    trace_id: str
    mode: Literal["knowledge", "general"]
    web_search: bool
    query: str
    conversation_summary: str
    rewritten_query: str
    should_rewrite_query: bool
    retrieval_filter: dict[str, object] | None
    retrieved_docs: list[Document]
    retrieval_metadata: dict[str, object]
    is_relevant: bool | None
    retrieval_retry_count: int
    answer: str
    candidate_answer: str
    candidate_citations: list[Citation]
    answer_disposition: Literal["pending", "accepted", "fallback"]
    turn_id: str
    generation_instruction: str
    generation_error: str
    fallback_reason: str
    citations: list[Citation]
    hallucination_pass: bool | None
    hallucination_reason: str
    hallucination_retry_count: int
    attempt_history: list[dict[str, object]]
    failure_stage: Literal[
        "retrieval",
        "relevance",
        "evidence",
        "citation",
        "generation",
        "hallucination",
        "tool",
        "runtime",
    ] | None
    failure_reason: str | None
    request_call_count: int
    model_call_count: int
    tool_call_count: int
    total_latency_ms: float
    budget_snapshot: dict[str, object] | None
    route: Literal["rag", "tool_call", "direct"]
    tool_result: dict[str, object] | None
    tool_output: str
    status_events: list[str]
