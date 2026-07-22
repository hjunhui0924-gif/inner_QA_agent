"""Tests for DashScope reranker response validation."""

from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from langchain_core.documents import Document

from backend.retrieval.reranker import DashScopeReranker


class DashScopeRerankerTests(unittest.TestCase):
    @patch("backend.retrieval.reranker.httpx.post")
    def test_native_response_is_parsed_and_invalid_rows_are_skipped(
        self,
        post: Mock,
    ) -> None:
        response = Mock()
        response.json.return_value = {
            "output": {
                "results": [
                    {"index": 1, "relevance_score": 0.9},
                    {"index": 99, "relevance_score": 1.0},
                    {"index": 0, "relevance_score": 0.4},
                ]
            }
        }
        post.return_value = response
        reranker = DashScopeReranker(api_key="test-key")
        documents = [
            Document(page_content="first"),
            Document(page_content="second"),
        ]

        results = reranker.rerank("query", documents, top_n=2)

        self.assertEqual([result.index for result in results], [1, 0])
        response.raise_for_status.assert_called_once()

    @patch("backend.retrieval.reranker.httpx.post")
    def test_empty_valid_response_is_rejected(self, post: Mock) -> None:
        response = Mock()
        response.json.return_value = {"output": {"results": []}}
        post.return_value = response
        reranker = DashScopeReranker(api_key="test-key")

        with self.assertRaisesRegex(ValueError, "no valid candidates"):
            reranker.rerank("query", [Document(page_content="text")], top_n=1)


if __name__ == "__main__":
    unittest.main()

