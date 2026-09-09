"""Regression tests for bounded LangGraph retries."""

from __future__ import annotations

import unittest
from unittest.mock import patch

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
        class AlwaysPassJudge:
            async def ainvoke(self, prompt_messages: list[object]) -> AIMessage:
                return AIMessage(
                    content='{"hallucination_pass":true,"reason":"supported"}'
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
        with patch("backend.agent.nodes._build_model", return_value=AlwaysPassJudge()):
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
        self.assertEqual(result["failure_stage"], "citation")

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
