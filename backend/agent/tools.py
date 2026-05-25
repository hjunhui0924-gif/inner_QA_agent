"""Tool functions used by the agent."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from langchain_core.tools import tool

from backend.config import settings
from backend.agent.memory import search_knowledge_base_text


@tool
def get_current_time() -> str:
    """Return the current date and time in Beijing time.

    Returns:
        Current time formatted as ``YYYY-MM-DD HH:MM:SS``.
    """

    return datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")


@tool
def search_knowledge_base(query: str) -> str:
    """Search the enterprise internal knowledge base for the most relevant content.

    Args:
        query: The internal question or search phrase.

    Returns:
        A text summary of the top matching internal knowledge entries.
    """

    return search_knowledge_base_text(query, top_k=settings.retrieval_top_k)
