"""Async LangGraph node implementations."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Sequence
from typing import Any, Literal

from langchain_core.documents import Document
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from backend.config import settings
from backend.agent.memory import (
    keyword_overlap_score,
    latest_user_text,
    search_documents,
)
from backend.agent.state import AgentState
from backend.agent.tools import get_current_time, search_knowledge_base


class _RouteDecision(dict):
    """Tiny helper type for JSON parsing."""


def _build_model(temperature: float = 0.2) -> ChatOpenAI:
    """Create the Qwen model configured through DashScope."""

    if not settings.dashscope_api_key:
        raise RuntimeError(
            "DASHSCOPE_API_KEY is not set. Configure it in .env before running the model."
        )
    return ChatOpenAI(
        model=settings.model_name,
        api_key=settings.dashscope_api_key,
        base_url=settings.dashscope_base_url,
        temperature=temperature,
        streaming=True,
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

    if not documents:
        return "无"
    lines: list[str] = []
    for index, doc in enumerate(documents, start=1):
        title = str(doc.metadata.get("title", "未命名条目"))
        snippet = doc.page_content.replace("\n", " ").strip()
        lines.append(f"{index}. {title}: {snippet}")
    return "\n".join(lines)


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


async def route_query(state: AgentState) -> dict[str, Any]:
    """Use the LLM to select the execution route."""

    query = state.get("query") or latest_user_text(state.get("messages", []))
    prompt = (
        "你是一个企业内部知识助手的路由器。请仅返回JSON对象，不要输出多余文字。\n"
        "可选路由：\n"
        '1. "rag"：问题应通过企业内部知识库检索回答。\n'
        '2. "tool_call"：问题需要调用工具，例如当前时间。\n'
        '3. "direct"：可以直接回答，不需要检索或工具。\n'
        "返回格式：{\"route\": \"rag|tool_call|direct\", \"reason\": \"简短原因\"}\n"
        f"用户问题：{query}\n"
    )

    route = _heuristic_route(query)
    try:
        model = _build_model(temperature=0)
        response = await model.ainvoke([SystemMessage(content=prompt)])
        parsed = _extract_json_object(_as_text(response))
        candidate = str(parsed.get("route", "")).strip()
        if candidate in {"rag", "tool_call", "direct"}:
            route = candidate  # type: ignore[assignment]
    except Exception:
        pass

    return {"route": route, "status_events": [f"route_query:{route}"]}


async def retrieve(state: AgentState) -> dict[str, Any]:
    """Fetch the most relevant documents from Chroma."""

    query = state.get("rewritten_query") or state.get("query") or latest_user_text(
        state.get("messages", [])
    )
    try:
        documents = await search_documents(query, top_k=settings.retrieval_top_k)
    except Exception:
        documents = []
    return {
        "retrieved_docs": documents,
        "status_events": ["retrieve"],
    }


async def grade_documents(state: AgentState) -> dict[str, Any]:
    """Judge whether the retrieved documents are relevant to the query."""

    query = state.get("query", "")
    documents = state.get("retrieved_docs", [])
    if not documents:
        return {"is_relevant": False, "status_events": ["grade_documents:no_docs"]}

    prompt = (
        "你是一个企业内部知识库相关性判断器。请仅返回JSON对象。\n"
        "返回格式：{\"is_relevant\": true|false, \"reason\": \"简短原因\"}\n"
        f"用户问题：{query}\n"
        f"候选知识：\n{_format_documents(documents)}"
    )
    score = keyword_overlap_score(query, [doc.page_content for doc in documents])
    is_relevant = score >= 0.08
    try:
        model = _build_model(temperature=0)
        response = await model.ainvoke([SystemMessage(content=prompt)])
        parsed = _extract_json_object(_as_text(response))
        candidate = parsed.get("is_relevant")
        if isinstance(candidate, bool):
            is_relevant = candidate
    except Exception:
        pass

    return {
        "is_relevant": is_relevant,
        "status_events": [f"grade_documents:{is_relevant}"],
    }


async def rewrite_query(state: AgentState) -> dict[str, Any]:
    """Rewrite the query to improve retrieval quality."""

    query = state.get("query", "")
    retry_count = state.get("retrieval_retry_count", 0) + 1
    rewritten_query = query

    prompt = (
        "请将用户问题改写成更适合企业内部知识库检索的短查询，仅返回JSON。\n"
        '返回格式：{"rewritten_query": "..." }\n'
        f"用户问题：{query}\n"
    )
    try:
        model = _build_model(temperature=0)
        response = await model.ainvoke([SystemMessage(content=prompt)])
        parsed = _extract_json_object(_as_text(response))
        candidate = str(parsed.get("rewritten_query", "")).strip()
        if candidate:
            rewritten_query = candidate
    except Exception:
        if "报销" in query or "费用" in query:
            rewritten_query = f"{query} 报销制度 财务流程"
        elif "请假" in query or "休假" in query:
            rewritten_query = f"{query} 请假制度 人事流程"
        elif "合同" in query or "审批" in query:
            rewritten_query = f"{query} 合同审批规范"

    return {
        "rewritten_query": rewritten_query,
        "retrieval_retry_count": retry_count,
        "status_events": [f"rewrite_query:{retry_count}"],
    }


async def fallback_answer(state: AgentState) -> dict[str, Any]:
    """Return a safe fallback answer when retrieval fails."""

    answer = (
        "我没有在企业内部知识库中找到足够相关的信息。"
        "请补充更具体的制度名称、流程名称或部门信息，我再继续检索。"
    )
    return {
        "answer": answer,
        "messages": [AIMessage(content=answer)],
        "status_events": ["fallback_answer"],
        "hallucination_pass": True,
    }


def _choose_tool_output(query: str) -> str:
    """Pick the most suitable tool based on the query."""

    lowered = query.lower()
    if any(
        keyword in lowered
        for keyword in ["时间", "几点", "日期", "today", "now", "current time"]
    ):
        return get_current_time.invoke({})
    return search_knowledge_base.invoke({"query": query})


async def tool_executor(state: AgentState) -> dict[str, Any]:
    """Execute the selected tool and store the output."""

    query = state.get("query") or latest_user_text(state.get("messages", []))
    try:
        tool_output = await asyncio.to_thread(_choose_tool_output, query)
    except Exception as exc:
        tool_output = f"工具调用失败：{exc}"
    return {
        "tool_output": tool_output,
        "status_events": ["tool_executor"],
    }


async def generate(state: AgentState) -> dict[str, Any]:
    """Generate the final answer using the current context."""

    query = state.get("query") or latest_user_text(state.get("messages", []))
    docs = state.get("retrieved_docs", [])
    tool_output = state.get("tool_output", "")
    route = state.get("route", "direct")

    system_prompt = (
        "你是企业内部知识助手。你的职责是基于企业内部制度、流程、规范和文档回答问题。\n"
        "请用中文回答，语气自然、专业、简洁。\n"
        "你面向企业内部员工，主要支持制度查询、流程说明、审批规则、合同与法务、财务与人事等内部事务。\n"
        "回答时聚焦企业内部知识，不要主动扩展到无关主题。\n"
        "如果知识依据不足，不要臆测，直接说明信息不足并指出建议补充的信息。\n"
        "优先遵守以下信息：\n"
        f"路由类型：{route}\n"
        f"工具结果：{tool_output or '无'}\n"
        f"检索到的知识：\n{_format_documents(docs)}\n"
        "如果用户只是打招呼或询问你能做什么，请简要介绍你支持的企业内部知识能力。"
    )

    prompt_messages: list[BaseMessage] = [SystemMessage(content=system_prompt)]
    prompt_messages.extend(state.get("messages", []))
    if not prompt_messages or not isinstance(prompt_messages[-1], HumanMessage):
        prompt_messages.append(HumanMessage(content=query))

    answer = ""
    try:
        model = _build_model(temperature=0.2)
        async for chunk in model.astream(prompt_messages):
            answer += _as_text(chunk)
    except Exception:
        if tool_output:
            answer = f"{tool_output}\n\n基于以上信息，建议先查看相关说明并按步骤处理。"
        elif docs:
            answer = (
                "根据企业内部知识库内容，相关说明如下：\n"
                f"{_format_documents(docs)}"
            )
        else:
            answer = (
                "当前没有足够的企业内部信息支持直接回答。"
                "请补充更具体的制度名称、流程名称、部门名称或文件标题。"
            )

    return {
        "answer": answer,
        "messages": [AIMessage(content=answer)],
        "status_events": ["generate"],
    }


async def check_hallucination(state: AgentState) -> dict[str, Any]:
    """Validate that the answer is grounded in the retrieved evidence."""

    route = state.get("route", "direct")
    answer = state.get("answer", "")
    docs = state.get("retrieved_docs", [])
    if route in {"tool_call", "direct"}:
        return {
            "hallucination_pass": True,
            "status_events": ["check_hallucination:skip"],
        }
    if not docs:
        return {
            "hallucination_pass": False,
            "status_events": ["check_hallucination:no_docs"],
        }

    prompt = (
        "请判断回答是否忠实于给定的企业内部知识，仅返回JSON。\n"
        '返回格式：{"hallucination_pass": true|false, "reason": "简短原因"}\n'
        f"问题：{state.get('query', '')}\n"
        f"回答：{answer}\n"
        f"知识：\n{_format_documents(docs)}"
    )

    pass_check = keyword_overlap_score(
        answer,
        [doc.page_content for doc in docs],
    ) >= 0.05
    try:
        model = _build_model(temperature=0)
        response = await model.ainvoke([SystemMessage(content=prompt)])
        parsed = _extract_json_object(_as_text(response))
        candidate = parsed.get("hallucination_pass")
        if isinstance(candidate, bool):
            pass_check = candidate
    except Exception:
        pass

    return {
        "hallucination_pass": pass_check,
        "status_events": [f"check_hallucination:{pass_check}"],
    }
