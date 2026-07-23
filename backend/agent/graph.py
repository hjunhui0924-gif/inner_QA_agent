"""LangGraph assembly for the customer service agent."""

from __future__ import annotations

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph

from backend.agent.edges import (
    route_after_grading,
    route_after_hallucination_check,
    route_after_routing,
)
from backend.agent.nodes import (
    check_hallucination,
    fallback_answer,
    generate,
    grade_documents,
    manage_conversation_context,
    retrieve,
    route_query,
    rewrite_query,
    tool_executor,
)
from backend.agent.state import AgentState


def build_graph(checkpointer: AsyncSqliteSaver):
    """Compile the LangGraph workflow."""

    builder = StateGraph(AgentState)
    builder.add_node("manage_conversation_context", manage_conversation_context)
    builder.add_node("route_query", route_query)
    builder.add_node("retrieve", retrieve)
    builder.add_node("grade_documents", grade_documents)
    builder.add_node("rewrite_query", rewrite_query)
    builder.add_node("fallback_answer", fallback_answer)
    builder.add_node("tool_executor", tool_executor)
    builder.add_node("generate", generate)
    builder.add_node("check_hallucination", check_hallucination)

    builder.add_edge(START, "manage_conversation_context")
    builder.add_edge("manage_conversation_context", "route_query")
    builder.add_conditional_edges("route_query", route_after_routing)
    builder.add_edge("retrieve", "grade_documents")
    builder.add_conditional_edges("grade_documents", route_after_grading)
    builder.add_edge("rewrite_query", "retrieve")
    builder.add_edge("tool_executor", "generate")
    builder.add_edge("generate", "check_hallucination")
    builder.add_conditional_edges(
        "check_hallucination",
        route_after_hallucination_check,
    )
    builder.add_edge("fallback_answer", END)

    return builder.compile(checkpointer=checkpointer)
