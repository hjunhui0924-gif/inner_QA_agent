"""Regressions discovered by the frozen M6 real-service audit."""
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.documents import Document
from langchain_core.messages import AIMessage, HumanMessage

from backend.agent.citations import build_citations, sanitize_answer_citations
from backend.agent.edges import route_after_hallucination_check
from backend.agent.nodes import (
    _contextual_retrieval_filter, _explicit_version_filter,
    _retrieval_filter_from_state, check_hallucination, commit_answer,
)
from backend.retrieval.engine import _matches_filter


@pytest.mark.asyncio
async def test_judge_true_cannot_commit_uncited_fact():
    docs = [Document(page_content="差旅报销6000元须直属主管审批后由财务负责人复核。")]
    state = dict(route="rag", query="6000元谁审批？", retrieved_docs=docs,
                 candidate_answer="须直属主管审批后由财务负责人复核。", turn_id="audit")
    model = AsyncMock()
    model.ainvoke.return_value = AIMessage(content='{"hallucination_pass":true,"reason":""}')
    with patch("backend.agent.nodes.settings.citation_validation_mode", "judge"), patch("backend.agent.nodes._build_model", return_value=model):
        checked = await check_hallucination(state)
    assert checked["hallucination_pass"] is False
    assert route_after_hallucination_check({**state, **checked}) == "generate"
    assert "messages" not in await commit_answer({**state, **checked})


@pytest.mark.asyncio
async def test_canonical_refusal_still_requires_semantic_judge():
    model = AsyncMock()
    model.ainvoke.return_value = AIMessage(content='{"hallucination_pass":false,"reason":"证据包含答案"}')
    with patch("backend.agent.nodes.settings.citation_validation_mode", "judge"), patch("backend.agent.nodes._build_model", return_value=model):
        checked = await check_hallucination(dict(route="rag", query="金额？",
            retrieved_docs=[Document(page_content="金额5000元。")],
            candidate_answer="检索到的知识未包含该问题的答案。"))
    assert not checked["hallucination_pass"]
    model.ainvoke.assert_awaited_once()


def test_current_query_excludes_active_but_future_or_expired_sources():
    filters = _retrieval_filter_from_state({"retrieval_filter": _explicit_version_filter("当前差旅额度？")})
    assert filters is not None
    for start, end, expected in [("2026-01-01", None, True), ("2035-01-01", None, False), ("2020-01-01", "2025-12-31", False)]:
        doc = Document(page_content="额度", metadata=dict(status="active", effective_from=start, effective_to=end))
        assert _matches_filter(doc, filters) is expected


def test_effective_date_followup_does_not_switch_old_version_to_current():
    doc = Document(page_content="旧版2025年1月1日生效。", metadata=dict(source_id="old", version="v1", status="deprecated"))
    messages = [HumanMessage(content="旧版差旅规则？"), AIMessage(content="旧版规则。[C1]", additional_kwargs={"citations": build_citations([doc], "旧版规则。[C1]")})]
    raw = _contextual_retrieval_filter(messages, "这个版本何时生效？")
    assert raw["versions"] == ["v1"]
    assert raw["source_ids"] == ["old"]


def test_explicit_version_effective_date_preserves_requested_version():
    assert _explicit_version_filter("v1版本何时生效？")["versions"] == ["v1"]


def test_explicit_future_version_followup_is_not_filtered_as_current():
    doc = Document(page_content="v3于2035年1月1日生效。", metadata=dict(source_id="future", version="v3", status="active", effective_from="2035-01-01"))
    messages = [HumanMessage(content="查询v3差旅制度"), AIMessage(content="未来版。[C1]", additional_kwargs={"citations": build_citations([doc], "未来版。[C1]")})]
    raw = _contextual_retrieval_filter(messages, "这个版本何时生效？")
    filters = _retrieval_filter_from_state({"retrieval_filter": raw})
    assert _matches_filter(doc, filters)


def test_post_sentence_citation_is_preserved_for_semantic_validation():
    docs = [Document(page_content="须保留电子发票、行程单和审批记录。")]
    answer = "须保留电子发票、行程单和审批记录。[C1]"
    sanitized = sanitize_answer_citations(answer, docs)
    assert "[C1]" in sanitized
    assert build_citations(docs, sanitized)


@pytest.mark.asyncio
async def test_refusal_classification_never_publishes_original_uncited_facts():
    model = AsyncMock()
    model.ainvoke.return_value = AIMessage(content='{"hallucination_pass":true,"is_abstention":true}')
    with patch("backend.agent.nodes.settings.citation_validation_mode", "judge"), patch("backend.agent.nodes._build_model", return_value=model):
        checked = await check_hallucination(dict(route="rag", query="额度？",
            retrieved_docs=[Document(page_content="额度5000元。")], candidate_answer="额度9000元。"))
    assert checked["candidate_answer"] == "检索到的知识未包含该问题的答案。"
