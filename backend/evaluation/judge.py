"""Optional LLM-based semantic judge for fully automatic answer evaluation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import SystemMessage

from backend.agent.nodes import _build_model
from backend.config import settings


@dataclass(frozen=True)
class AutomaticJudgeResult:
    available: bool
    answer_correct: bool
    grounded: bool
    citation_support: bool
    abstention_correct: bool
    overall_pass: bool
    reason: str
    error: str


async def evaluate_with_judge(
    *,
    question: str,
    answer: str,
    answerable: bool,
    expected_facts: list[str],
    citations: list[dict[str, Any]],
) -> AutomaticJudgeResult:
    """Judge semantic correctness against reference facts and cited quotations."""

    evidence = [
        {
            "citation_id": str(item.get("citation_id", "")),
            "quote": str(item.get("quote", "")),
        }
        for item in citations
    ]
    prompt = (
        "你是严格的 RAG 自动评测器。参考事实是评测标准，但允许答案使用同义改写。\n"
        "只返回一个 JSON 对象，不要输出 Markdown 或解释性前缀。\n"
        "字段必须全部为布尔值或简短字符串：\n"
        '{"answer_correct":true|false,"grounded":true|false,'
        '"citation_support":true|false,"abstention_correct":true|false,'
        '"reason":"..."}\n'
        "判定规则：\n"
        "1. answer_correct：可回答题覆盖全部参考事实且无矛盾；无答案题明确拒答。\n"
        "2. grounded：答案没有超出给定引用原文的事实性扩展。\n"
        "3. citation_support：每个事实性断言均有引用，且对应原文支持该断言。\n"
        "4. abstention_correct：可回答题没有错误拒答；无答案题正确拒答。\n"
        "5. 引文只证明出处；你必须自行判断语义支持、否定、主体客体和例外条件。\n"
        f"问题：{question}\n"
        f"是否可回答：{json.dumps(answerable)}\n"
        f"参考事实：{json.dumps(expected_facts, ensure_ascii=False)}\n"
        f"模型答案：{answer}\n"
        f"引用原文：{json.dumps(evidence, ensure_ascii=False)}\n"
    )
    try:
        model = _build_model(
            temperature=0,
            model_name=settings.judge_model_name,
        )
        response = await model.ainvoke([SystemMessage(content=prompt)])
        parsed = _parse_judge_json(_response_text(response))
        values = {
            key: parsed.get(key)
            for key in (
                "answer_correct",
                "grounded",
                "citation_support",
                "abstention_correct",
            )
        }
        if not all(isinstance(value, bool) for value in values.values()):
            raise ValueError("Judge JSON must contain all required boolean fields.")
        overall_pass = all(bool(value) for value in values.values())
        return AutomaticJudgeResult(
            available=True,
            answer_correct=bool(values["answer_correct"]),
            grounded=bool(values["grounded"]),
            citation_support=bool(values["citation_support"]),
            abstention_correct=bool(values["abstention_correct"]),
            overall_pass=overall_pass,
            reason=str(parsed.get("reason", ""))[:500],
            error="",
        )
    except Exception as exc:
        return AutomaticJudgeResult(
            available=False,
            answer_correct=False,
            grounded=False,
            citation_support=False,
            abstention_correct=False,
            overall_pass=False,
            reason="",
            error=f"{type(exc).__name__}: {exc}"[:500],
        )


def _parse_judge_json(text: str) -> dict[str, Any]:
    value = json.loads(text.strip())
    if not isinstance(value, dict):
        raise ValueError("Judge JSON must be an object.")
    return value


def _response_text(response: Any) -> str:
    content = getattr(response, "content", response)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(item.get("text", "")) if isinstance(item, dict) else str(item)
            for item in content
        )
    return str(content)
