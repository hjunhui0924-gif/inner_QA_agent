"""LangGraph state definition."""

from __future__ import annotations

import operator
from typing import Annotated, Literal, TypedDict

from langchain_core.documents import Document
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict, total=False):
    """State shared across the customer service graph."""

    messages: Annotated[list[BaseMessage], add_messages]
    user_id: str
    session_id: str
    query: str
    rewritten_query: str
    retrieved_docs: list[Document]
    is_relevant: bool
    retrieval_retry_count: int
    answer: str
    hallucination_pass: bool
    hallucination_retry_count: int
    route: Literal["rag", "tool_call", "direct"]
    tool_output: str
    status_events: Annotated[list[str], operator.add]
