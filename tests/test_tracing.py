"""Tests for request tracing and failure classification."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from langchain_core.documents import Document

from backend.observability.tracing import build_trace, record_trace


class TraceTests(unittest.TestCase):
    def test_rag_answer_without_citations_is_classified(self) -> None:
        trace = build_trace(
            trace_id="trace-1",
            query="What is the rule?",
            state={
                "route": "rag",
                "answer": "The limit is 10.",
                "retrieved_docs": [
                    Document(page_content="The limit is 10.", metadata={"chunk_id": "a:0"})
                ],
                "citations": [],
                "status_events": ["retrieve", "generate"],
            },
        )

        self.assertEqual(trace["failure_type"], "citation_error")
        self.assertEqual(trace["retrieved_chunks"][0]["chunk_id"], "a:0")

    def test_generation_error_takes_priority_over_fallback_shape(self) -> None:
        trace = build_trace(
            trace_id="trace-generation",
            query="What is the rule?",
            state={
                "route": "rag",
                "answer": "Insufficient information.",
                "generation_error": "PermissionDeniedError: quota exhausted",
                "retrieved_docs": [Document(page_content="rule", metadata={})],
                "citations": [],
            },
        )

        self.assertEqual(trace["failure_type"], "generation_error")
        self.assertIn("PermissionDeniedError", trace["generation_error"])

    def test_retrieval_fallback_reason_is_not_misclassified_as_citation(self) -> None:
        trace = build_trace(
            trace_id="trace-retrieval",
            query="unknown",
            state={
                "route": "rag",
                "answer": "我没有在企业内部知识库中找到足够相关的信息。",
                "fallback_reason": "retrieval_exhausted",
                "retrieved_docs": [Document(page_content="irrelevant", metadata={})],
                "citations": [],
            },
        )

        self.assertEqual(trace["failure_type"], "retrieval_miss")

    def test_trace_is_appended_as_valid_jsonl(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "traces.jsonl"
            trace = build_trace(
                trace_id="trace-2",
                query="Unknown policy?",
                state={"route": "rag", "answer": "Insufficient information.", "retrieved_docs": []},
            )

            record_trace(trace, path)

            saved = json.loads(path.read_text(encoding="utf-8").strip())
            self.assertEqual(saved["trace_id"], "trace-2")
            self.assertEqual(saved["failure_type"], "retrieval_miss")

    def test_trace_file_rotates_before_unbounded_growth(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "traces.jsonl"
            trace = build_trace(trace_id="trace-3", query="q", state={"answer": "a"})
            record_trace(trace, path, max_bytes=100, backup_count=2)
            record_trace(trace, path, max_bytes=100, backup_count=2)

            self.assertTrue(path.exists())
            self.assertTrue(path.with_name("traces.jsonl.1").exists())

    def test_single_oversized_trace_is_hard_capped(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "traces.jsonl"

            record_trace({"trace_id": "x", "answer": "a" * 10_000}, path, max_bytes=100)

            self.assertLessEqual(path.stat().st_size, 100)


if __name__ == "__main__":
    unittest.main()
