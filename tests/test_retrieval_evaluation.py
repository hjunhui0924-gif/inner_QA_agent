"""Tests for deterministic retrieval evaluation metrics."""

from __future__ import annotations

import unittest

from langchain_core.documents import Document

from backend.evaluation.retrieval import (
    RetrievalEvalCase,
    run_retrieval_ablation,
    validate_cases_against_corpus,
)
from backend.retrieval.engine import RetrievalResult


class _EvaluationEngineFake:
    def retrieve(self, query: str, top_k: int, *, strategy: str) -> RetrievalResult:
        documents = (
            [
                Document(
                    page_content="标准证据文本",
                    metadata={"source_id": "official", "chunk_id": "official:0"},
                )
            ]
            if query == "answerable"
            else [
                Document(
                    page_content="干扰文本",
                    metadata={"source_id": "distractor", "chunk_id": "other:0"},
                )
            ]
        )
        return RetrievalResult(
            documents=documents[:top_k],
            strategy=strategy,  # type: ignore[arg-type]
            dense_candidates=1,
            lexical_candidates=1,
            fused_candidates=1,
            rerank_used=strategy == "rerank",
            degraded_reason=None,
            latency_ms=5.0,
        )


class RetrievalEvaluationTests(unittest.TestCase):
    def test_metrics_exclude_no_answer_cases_from_recall(self) -> None:
        cases = [
            RetrievalEvalCase(
                id="a",
                category="fact",
                question="answerable",
                source_ids=["official"],
                evidence_phrases=["标准证据"],
            ),
            RetrievalEvalCase(
                id="n",
                category="no_answer",
                question="no-answer",
                source_ids=[],
                evidence_phrases=[],
            ),
        ]

        report = run_retrieval_ablation(
            _EvaluationEngineFake(),  # type: ignore[arg-type]
            cases,
            strategies=["fusion"],
            top_k=1,
        )["fusion"]

        self.assertEqual(report["answerable_case_count"], 1)
        self.assertEqual(report["no_answer_case_count"], 1)
        self.assertEqual(report["source_hit_at_k"], 1.0)
        self.assertEqual(report["evidence_recall_at_k"], 1.0)

    def test_gold_validation_rejects_missing_evidence(self) -> None:
        cases = [
            RetrievalEvalCase(
                id="bad",
                category="fact",
                question="question",
                source_ids=["official"],
                evidence_phrases=["不存在"],
            )
        ]
        documents = [
            Document(
                page_content="标准证据文本",
                metadata={"source_id": "official"},
            )
        ]

        with self.assertRaisesRegex(ValueError, "evidence not found"):
            validate_cases_against_corpus(cases, documents)


if __name__ == "__main__":
    unittest.main()

