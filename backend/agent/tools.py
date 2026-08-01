"""Tool functions used by the agent."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo
import json
from urllib.parse import quote
from urllib.request import Request, urlopen

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


@tool
def search_web(query: str) -> str:
    """Search public web snippets for a general-mode question."""

    request = Request(
        "https://api.duckduckgo.com/?q=" + quote(query) + "&format=json&no_html=1&skip_disambig=1",
        headers={"User-Agent": "EnterpriseKnowledgeAssistant/1.0"},
    )
    with urlopen(request, timeout=8) as response:  # noqa: S310 - fixed public endpoint
        payload = json.loads(response.read().decode("utf-8"))
    snippets: list[str] = []
    if payload.get("AbstractText"):
        snippets.append(str(payload["AbstractText"]))
    for item in payload.get("RelatedTopics", [])[:5]:
        if isinstance(item, dict) and item.get("Text"):
            snippets.append(str(item["Text"]))
    return "\n".join(snippets) or "公开网页没有返回可用摘要。"
