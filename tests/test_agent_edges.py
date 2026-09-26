"""Regression tests for bounded LangGraph retries."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from backend.agent.edges import route_after_hallucination_check
from backend.agent.nodes import check_hallucination
from langchain_core.documents import Document
from langchain_core.messages import AIMessage


class HallucinationRetryTests(unittest.TestCase):
    def test_one_configured_retry_allows_one_more_generation(self) -> None:
        state = {"hallucination_pass": False, "hallucination_retry_count": 1}

        with patch("backend.agent.edges.settings.max_hallucination_retries", 1):
            self.assertEqual(route_after_hallucination_check(state), "generate")

    def test_retry_limit_uses_safe_fallback_after_allowed_retry_fails(self) -> None:
        state = {"hallucination_pass": False, "hallucination_retry_count": 2}

        with patch("backend.agent.edges.settings.max_hallucination_retries", 1):
            self.assertEqual(route_after_hallucination_check(state), "fallback_answer")


class HallucinationNodeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        mode = patch("backend.agent.nodes.settings.citation_validation_mode", "judge")
        mode.start()
        self.addCleanup(mode.stop)

    async def test_legacy_mode_keeps_semantic_rule_veto(self):
        judge = AsyncMock()
        judge.ainvoke.return_value = AIMessage(content='{"hallucination_pass":true}')
        with patch("backend.agent.nodes.settings.citation_validation_mode", "legacy"), patch("backend.agent.nodes._build_model", return_value=judge):
            result = await check_hallucination({
                "route": "rag", "query": "住宿标准？",
                "candidate_answer": "住宿限额600元 [C1]。",
                "retrieved_docs": [Document(page_content="住宿限额500元。")],
            })
        self.assertFalse(result["hallucination_pass"])
        self.assertEqual(result["failure_stage"], "citation")

    async def test_judge_mode_malformed_verdict_fails_closed(self):
        for output in ['{}', '{"hallucination_pass":"true"}', 'not JSON']:
            judge = AsyncMock()
            judge.ainvoke.return_value = AIMessage(content=output)
            with patch("backend.agent.nodes._build_model", return_value=judge):
                result = await check_hallucination({
                    "route": "rag", "query": "住宿标准？",
                    "candidate_answer": "住宿限额500元 [C1]。",
                    "retrieved_docs": [Document(page_content="住宿限额500元。")],
                })
            self.assertFalse(result["hallucination_pass"])
            self.assertEqual(result["failure_stage"], "hallucination")

    async def test_judge_accepted_translation_has_no_second_semantic_veto(self) -> None:
        class Judge:
            async def ainvoke(self, messages):
                return AIMessage(content='{"hallucination_pass":true,"reason":"faithful translation"}')

        with patch("backend.agent.nodes._build_model", return_value=Judge()):
            result = await check_hallucination({
                "route": "rag", "query": "Explain the leave process in English.",
                "candidate_answer": "Your manager must approve leave before HR records it [C1].",
                "retrieved_docs": [Document(page_content="请假先提交申请，经直属主管批准后交人事备案。")],
            })
        self.assertTrue(result["hallucination_pass"])
        self.assertEqual(result["answer_disposition"], "accepted")

    async def test_invalid_citation_stops_before_judge(self) -> None:
        with patch("backend.agent.nodes._build_model") as builder:
            result = await check_hallucination({
                "route": "rag", "query": "审批要求？",
                "candidate_answer": "需要主管审批 [C9]。",
                "retrieved_docs": [Document(page_content="需要主管审批。")],
            })
        builder.assert_not_called()
        self.assertFalse(result["hallucination_pass"])
        self.assertEqual(result["failure_stage"], "citation")
        self.assertEqual(result["hallucination_retry_count"], 1)
        self.assertEqual(result["model_call_count"], 0)

    async def test_missing_documents_increments_failure_count(self) -> None:
        result = await check_hallucination(
            {
                "route": "rag",
                "retrieved_docs": [],
                "hallucination_retry_count": 0,
            }
        )

        self.assertFalse(result["hallucination_pass"])
        self.assertEqual(result["hallucination_retry_count"], 1)

    async def test_judge_unavailable_fails_closed_even_with_lexical_overlap(self) -> None:
        with patch("backend.agent.nodes._build_model", side_effect=RuntimeError("offline")):
            result = await check_hallucination(
                {
                    "route": "rag",
                    "query": "records",
                    "answer": "records are retained [C1]",
                    "retrieved_docs": [
                        Document(page_content="records are retained", metadata={})
                    ],
                }
            )

        self.assertFalse(result["hallucination_pass"])

    async def test_wrong_citation_target_fails_closed(self) -> None:
        class RejectWrongTargetJudge:
            async def ainvoke(self, prompt_messages: list[object]) -> AIMessage:
                return AIMessage(
                    content='{"hallucination_pass":false,"reason":"C1 is about licensing, not final decisions"}'
                )

        documents = [
            Document(
                page_content="数据处理者应当依法取得行政许可。",
                metadata={"source_id": "wrong", "document_id": "wrong", "chunk_id": "wrong:0"},
            ),
            Document(
                page_content="依法作出的安全审查决定为最终决定。",
                metadata={"source_id": "right", "document_id": "right", "chunk_id": "right:0"},
            ),
        ]
        with patch("backend.agent.nodes._build_model", return_value=RejectWrongTargetJudge()):
            result = await check_hallucination(
                {
                    "route": "rag",
                    "query": "安全审查决定能否继续申诉？",
                    "candidate_answer": "安全审查决定为最终决定 [C1]。",
                    "retrieved_docs": documents,
                }
            )

        self.assertFalse(result["hallucination_pass"])
        self.assertEqual(result["answer_disposition"], "pending")
        self.assertEqual(result["failure_stage"], "hallucination")

    async def test_paraphrased_supported_citation_remains_accepted(self) -> None:
        class AlwaysPassJudge:
            async def ainvoke(self, prompt_messages: list[object]) -> AIMessage:
                return AIMessage(
                    content='{"hallucination_pass":true,"reason":"supported"}'
                )

        document = Document(
            page_content="个人信息处理者利用个人信息进行自动化决策，应当保证决策的透明度和结果公平、公正。",
            metadata={
                "source_id": "policy",
                "document_id": "policy",
                "chunk_id": "policy:0",
            },
        )
        with patch("backend.agent.nodes._build_model", return_value=AlwaysPassJudge()):
            result = await check_hallucination(
                {
                    "route": "rag",
                    "query": "自动化决策需要保证什么？",
                    "candidate_answer": "自动化决策时应保证决策透明、公平公正 [C1]。",
                    "retrieved_docs": [document],
                }
            )

        self.assertTrue(result["hallucination_pass"])
        self.assertEqual(result["answer_disposition"], "accepted")

    async def test_short_conclusion_can_be_explained_by_following_cited_sentence(self) -> None:
        class AlwaysPassJudge:
            async def ainvoke(self, prompt_messages: list[object]) -> AIMessage:
                return AIMessage(
                    content='{"hallucination_pass":true,"reason":"supported"}'
                )

        document = Document(
            page_content="未向境内公众提供生成式人工智能服务的，不适用本办法的规定。",
            metadata={
                "source_id": "policy",
                "document_id": "policy",
                "chunk_id": "policy:0",
            },
        )
        with patch("backend.agent.nodes._build_model", return_value=AlwaysPassJudge()):
            result = await check_hallucination(
                {
                    "route": "rag",
                    "query": "是否适用？",
                    "candidate_answer": "不适用。未向境内公众提供生成式人工智能服务的，不适用本办法的规定 [C1]。",
                    "retrieved_docs": [document],
                }
            )

        self.assertTrue(result["hallucination_pass"])


if __name__ == "__main__":
    unittest.main()
