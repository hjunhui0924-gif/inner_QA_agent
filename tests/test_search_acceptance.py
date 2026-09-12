"""Search results must be useful, attributable, and explicitly classified."""
import unittest
from unittest.mock import patch

from backend.agent.nodes import check_hallucination, fallback_answer, generate
from backend.observability.tracing import classify_runtime_failure


class SearchAcceptanceTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_search_has_its_own_failure_and_message(self):
        state = {"mode": "general", "route": "tool_call", "failure_stage": "tool", "failure_reason": "web_search_no_results"}
        result = await fallback_answer(state)
        self.assertIn("没有找到可用", result["candidate_answer"])
        self.assertEqual(classify_runtime_failure({**state, **result}), "search_no_results")

    async def test_citation_failure_does_not_claim_retrieval_missed(self):
        result = await fallback_answer({"failure_stage": "citation", "hallucination_pass": False, "retrieved_docs": [object()]})
        self.assertIn("找到相关资料", result["candidate_answer"])
        self.assertNotIn("没有在企业内部知识库中找到", result["candidate_answer"])

    async def test_unattributed_web_answer_cannot_bypass_validation(self):
        result = await check_hallucination({
            "mode": "general", "web_search": True, "route": "tool_call", "query": "解释 Python",
            "candidate_answer": '```json\n{"answer":["Python"]}\n```',
            "tool_result": {"ok": True, "text": "无摘要", "sources": [], "error": None},
        })
        self.assertFalse(result["hallucination_pass"])

    async def test_failed_search_does_not_invoke_a_generation_model(self):
        with patch('backend.agent.nodes._build_model') as model:
            await generate({"mode": "general", "web_search": True, "route": "tool_call", "failure_stage": "tool", "failure_reason": "web_search_no_results"})
        model.assert_not_called()
