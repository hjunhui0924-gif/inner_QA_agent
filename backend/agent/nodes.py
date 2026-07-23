"""Async LangGraph node implementations."""

from __future__ import annotations

import asyncio
import json
import re
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

from backend.config import settings
from backend.agent.citations import (
    sanitize_answer_citations,
    build_citations,
    citation_markers,
    format_documents_for_prompt,
)
from backend.agent.conversation import (
    merge_fallback_summary,
    plan_conversation_window,
    render_messages,
    truncate_to_token_budget,
)
from backend.agent.memory import (
    keyword_overlap_score,
    latest_user_text,
    search_documents,
)
from backend.agent.state import AgentState
from backend.agent.tools import get_current_time, search_knowledge_base


class _RouteDecision(dict):
    """Tiny helper type for JSON parsing."""


async def manage_conversation_context(state: AgentState) -> dict[str, Any]:
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
        return {"status_events": ["manage_conversation_context:unchanged"]}

    old_conversation = render_messages(plan.messages_to_summarize)
    prompt = (
        "你负责压缩企业知识助手的旧对话。请只返回简洁的结构化摘要，不要回答当前问题。\n"
        "保留用户目标、明确的实体/制度名称、金额、时间、限制条件、已做决定、"
        "尚未解决的问题和代词指向。不要把助手的推测写成已确认事实。\n"
        f"已有摘要：\n{existing_summary or '无'}\n"
        f"待压缩旧对话：\n{old_conversation}\n"
    )
    try:
        model = _build_model(
            temperature=0,
            max_tokens=settings.conversation_summary_target_tokens,
        )
        response = await model.ainvoke([SystemMessage(content=prompt)])
        summary = _as_text(response).strip()
        if not summary:
            raise ValueError("Conversation summarizer returned empty content.")
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


async def route_query(state: AgentState) -> dict[str, Any]:
    """Use the LLM to select the execution route."""

    query = state.get("query") or latest_user_text(state.get("messages", []))
    conversation_summary = state.get("conversation_summary", "").strip()
    recent_conversation = render_messages(list(state.get("messages", [])))
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
    )

    heuristic_input = query
    if _looks_like_contextual_follow_up(query):
        heuristic_input = f"{conversation_summary}\n{recent_conversation}\n{query}"
    route = _heuristic_route(heuristic_input)
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

    query = state.get("rewritten_query") or state.get("query", "")
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
    """Use conversation context to produce a standalone retrieval query."""

    query = state.get("query", "")
    retry_count = state.get("retrieval_retry_count", 0) + 1
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
    try:
        model = _build_model(temperature=0)
        response = await model.ainvoke([SystemMessage(content=prompt)])
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
        "retrieval_retry_count": retry_count,
        "status_events": [f"rewrite_query:{retry_count}"],
    }


async def fallback_answer(state: AgentState) -> dict[str, Any]:
    """Return a safe fallback answer when retrieval fails."""

    answer = (
        "我没有在企业内部知识库中找到足够相关的信息。"
        "请补充更具体的制度名称、流程名称或部门信息，我再继续检索。"
    )
    if state.get("generation_error"):
        fallback_reason = "generation_error"
    elif state.get("is_relevant") is False or not state.get("retrieved_docs"):
        fallback_reason = "retrieval_exhausted"
    elif state.get("hallucination_pass") is False:
        fallback_reason = "hallucination_exhausted"
    else:
        fallback_reason = "unknown"
    return {
        "answer": answer,
        "citations": [],
        "messages": [AIMessage(content=answer)],
        "status_events": ["fallback_answer"],
        "hallucination_pass": True,
        "fallback_reason": fallback_reason,
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
    conversation_summary = state.get("conversation_summary", "").strip()

    system_prompt = (
        "你是企业内部知识助手。你的职责是基于企业内部制度、流程、规范和文档回答问题。\n"
        "请用中文回答，语气自然、专业、简洁。\n"
        "你面向企业内部员工，主要支持制度查询、流程说明、审批规则、合同与法务、财务与人事等内部事务。\n"
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

    prompt_messages: list[BaseMessage] = [SystemMessage(content=system_prompt)]
    prompt_messages.extend(state.get("messages", []))
    if not prompt_messages or not isinstance(prompt_messages[-1], HumanMessage):
        prompt_messages.append(HumanMessage(content=query))

    answer = ""
    generation_error = ""
    try:
        model = _build_model(temperature=0 if route == "rag" else 0.2)
        async for chunk in model.astream(prompt_messages):
            answer += _as_text(chunk)
    except Exception as exc:
        generation_error = f"{type(exc).__name__}: {exc}"[:500]
        answer = "回答生成服务暂时不可用，当前无法可靠作答，请稍后重试。"

    if route == "rag":
        answer = sanitize_answer_citations(answer, docs, query=query)
        citations = build_citations(docs, answer, query=query)
    else:
        citations = []
    return {
        "answer": answer,
        "citations": citations,
        "generation_error": generation_error,
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
            "hallucination_retry_count": (
                state.get("hallucination_retry_count", 0) + 1
            ),
            "status_events": ["check_hallucination:no_docs"],
        }

    prompt = (
        "请判断回答是否忠实于给定的企业内部知识，仅返回JSON。\n"
        '返回格式：{"hallucination_pass": true|false, "reason": "简短原因"}\n'
        f"问题：{state.get('query', '')}\n"
        f"回答：{answer}\n"
        f"知识：\n{_format_documents(docs)}"
    )

    pass_check = False
    try:
        model = _build_model(
            temperature=0,
            model_name=settings.judge_model_name,
        )
        response = await model.ainvoke([SystemMessage(content=prompt)])
        parsed = _extract_json_object(_as_text(response))
        candidate = parsed.get("hallucination_pass")
        if isinstance(candidate, bool):
            pass_check = candidate
    except Exception:
        pass_check = False

    resolved_citations = build_citations(docs, answer, query=state.get("query", ""))
    resolved_ids = {citation["citation_id"] for citation in resolved_citations}
    markers = citation_markers(answer)
    pass_check = pass_check and bool(markers) and markers == resolved_ids

    result: dict[str, Any] = {
        "hallucination_pass": pass_check,
        "citations": resolved_citations,
        "status_events": [f"check_hallucination:{pass_check}"],
    }
    if not pass_check:
        result["hallucination_retry_count"] = (
            state.get("hallucination_retry_count", 0) + 1
        )
    return result
