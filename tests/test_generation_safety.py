"""Safety regressions for model failures during grounded generation."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from langchain_core.documents import Document

from backend.agent.nodes import generate


class GenerationFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_failure_never_dumps_retrieved_documents_as_answer(self) -> None:
        secret = "CONFIDENTIAL_INTERNAL_POLICY_TEXT"
        with patch("backend.agent.nodes._build_model", side_effect=RuntimeError("offline")):
            result = await generate(
                {
                    "query": "What is the policy?",
                    "route": "rag",
                    "retrieved_docs": [Document(page_content=secret, metadata={})],
                    "messages": [],
                }
            )

        self.assertNotIn(secret, result["answer"])
        self.assertIn("RuntimeError", result["generation_error"])
        self.assertEqual(result["citations"], [])


if __name__ == "__main__":
    unittest.main()
