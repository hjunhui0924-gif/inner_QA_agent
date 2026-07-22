"""Regression tests for bounded LangGraph retries."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from backend.agent.edges import route_after_hallucination_check
from backend.agent.nodes import check_hallucination


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


if __name__ == "__main__":
    unittest.main()
