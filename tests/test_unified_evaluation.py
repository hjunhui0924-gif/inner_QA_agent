"""Tests for the canonical multi-dataset evaluation catalog."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from langchain_core.documents import Document

from backend.evaluation.runtime import extract_generation_output
from backend.evaluation.unified import (
    UnifiedEvalCase,
    aggregate_unified_retrieval,
    evaluate_unified_retrieval_case,
    load_unified_suite,
)
from backend.retrieval.engine import RetrievalResult


class UnifiedEvaluationTests(unittest.TestCase):
    def test_catalog_contains_all_three_source_datasets(self) -> None:
        suite = load_unified_suite()
        expected_counts = {
            "official_policy": len(
                json.loads(
                    Path("data/evals/official_policy_retrieval_eval.json").read_text(
                        encoding="utf-8"
                    )
                )
            ),
            "enterprise_rag": len(
                json.loads(
                    Path("data/evals/enterprise_rag_eval.json").read_text(
                        encoding="utf-8"
                    )
                )
            ),
            "dongshan_legacy": len(
                json.loads(
                    Path("data/evals/dongshan_legal_opinion_eval.json").read_text(
                        encoding="utf-8"
                    )
                )
            ),
        }

        self.assertEqual(len(suite.cases), sum(expected_counts.values()))
        self.assertEqual(
            {spec.id for spec in suite.specs},
            {"official_policy", "enterprise_rag", "dongshan_legacy"},
        )
        self.assertEqual(
            {
                dataset_id: len(suite.cases_for(dataset_id))
                for dataset_id in {spec.id for spec in suite.specs}
            },
            expected_counts,
        )

    def test_canonical_ids_are_unique_and_preserve_source_ids(self) -> None:
        suite = load_unified_suite()

        self.assertEqual(len({case.id for case in suite.cases}), 306)
        self.assertTrue(all(":" in case.id for case in suite.cases))
        self.assertTrue(all(case.source_case_id for case in suite.cases))

    def test_catalog_keeps_independent_roles_and_splits(self) -> None:
        suite = load_unified_suite()

        roles = {
            spec.id: {case.role for case in suite.cases_for(spec.id)}
            for spec in suite.specs
        }
        self.assertEqual(roles["official_policy"], {"safety"})
        self.assertEqual(roles["enterprise_rag"], {"main_regression"})
        self.assertEqual(roles["dongshan_legacy"], {"legacy_compatibility"})
        self.assertEqual(
            {case.split for case in suite.cases_for("enterprise_rag")},
            {"development", "regression", "held_out"},
        )
        self.assertEqual(
            {case.split for case in suite.cases_for("dongshan_legacy")},
            {"legacy"},
        )

    def test_runtime_output_prefers_committed_fields(self) -> None:
        answer, citations = extract_generation_output(
            {
                "answer": "正式答案",
                "citations": [{"citation_id": "C1"}],
                "candidate_answer": "候选答案",
                "candidate_citations": [{"citation_id": "C2"}],
            }
        )

        self.assertEqual(answer, "正式答案")
        self.assertEqual(citations, [{"citation_id": "C1"}])

    def test_runtime_output_accepts_direct_generation_fields(self) -> None:
        answer, citations = extract_generation_output(
            {
                "candidate_answer": "候选答案",
                "candidate_citations": [{"citation_id": "C1"}],
            }
        )

        self.assertEqual(answer, "候选答案")
        self.assertEqual(citations, [{"citation_id": "C1"}])

    def test_runtime_output_uses_candidate_citations_for_uncommitted_state(self) -> None:
        answer, citations = extract_generation_output(
            {
                "answer": "",
                "citations": [],
                "candidate_answer": "待校验答案",
                "candidate_citations": [{"citation_id": "C2"}],
            }
        )

        self.assertEqual(answer, "待校验答案")
        self.assertEqual(citations, [{"citation_id": "C2"}])

    def test_legacy_retrieval_uses_source_title_as_relevance_key(self) -> None:
        case = UnifiedEvalCase(
            id="dongshan_legacy:q1",
            source_case_id="q1",
            dataset_id="dongshan_legacy",
            corpus_id="enterprise_knowledge_base",
            role="legacy_compatibility",
            split="legacy",
            domain="Legal",
            difficulty="legacy",
            category="legacy_single_document",
            tags=["legacy"],
            question="召集人是谁？",
            answerable=True,
            source_ids=[],
            evidence_phrases=["第六届董事会"],
            expected_facts=["东山精密第六届董事会"],
            must_cite=True,
            source_title="东山精密：2025年度股东会法律意见书",
        )
        document = Document(
            page_content="东山精密第六届董事会负责召集会议。",
            metadata={
                "title": "东山精密：2025年度股东会法律意见书",
                "source_id": "dongshan-2025-shareholders-opinion",
                "chunk_id": "dongshan:0",
            },
        )
        result = evaluate_unified_retrieval_case(
            case,
            RetrievalResult(
                documents=[document],
                strategy="rerank",
                dense_candidates=1,
                lexical_candidates=1,
                fused_candidates=1,
                rerank_used=True,
                degraded_reason=None,
                latency_ms=1.0,
            ),
        )

        self.assertTrue(result.source_hit)
        self.assertEqual(result.evidence_recall, 1.0)
        self.assertEqual(result.failure_type, "none")

    def test_unified_summary_does_not_use_no_answer_rows_for_recall(self) -> None:
        answerable = UnifiedEvalCase(
            id="enterprise_rag:a",
            source_case_id="a",
            dataset_id="enterprise_rag",
            corpus_id="enterprise_knowledge_base",
            role="main_regression",
            split="regression",
            domain="HR",
            difficulty="easy",
            category="direct_fact",
            tags=[],
            question="请假？",
            answerable=True,
            source_ids=["hr"],
            evidence_phrases=["提前一天"],
            expected_facts=["提前一天"],
            must_cite=True,
        )
        no_answer = UnifiedEvalCase(
            id="enterprise_rag:n",
            source_case_id="n",
            dataset_id="enterprise_rag",
            corpus_id="enterprise_knowledge_base",
            role="main_regression",
            split="regression",
            domain="HR",
            difficulty="easy",
            category="no_answer",
            tags=[],
            question="不存在的问题？",
            answerable=False,
            source_ids=[],
            evidence_phrases=[],
            expected_facts=[],
            must_cite=False,
        )
        document = Document(
            page_content="员工请假需提前一天提交。",
            metadata={"source_id": "hr", "chunk_id": "hr:0"},
        )
        rows = [
            evaluate_unified_retrieval_case(
                answerable,
                RetrievalResult(
                    documents=[document],
                    strategy="rerank",
                    dense_candidates=1,
                    lexical_candidates=1,
                    fused_candidates=1,
                    rerank_used=False,
                    degraded_reason=None,
                    latency_ms=1.0,
                ),
            ),
            evaluate_unified_retrieval_case(
                no_answer,
                RetrievalResult(
                    documents=[document],
                    strategy="rerank",
                    dense_candidates=1,
                    lexical_candidates=1,
                    fused_candidates=1,
                    rerank_used=False,
                    degraded_reason=None,
                    latency_ms=1.0,
                ),
            ),
        ]
        summary = aggregate_unified_retrieval(rows)

        self.assertEqual(summary["source_hit_at_k"], 1.0)
        self.assertEqual(summary["evidence_recall_at_k"], 1.0)
        self.assertEqual(summary["no_answer_case_count"], 1)
        self.assertEqual(summary["no_answer_retrieved_nonempty_rate"], 1.0)

    def test_legacy_numeric_variants_are_evaluated_consistently(self) -> None:
        from scripts.run_unified_rag_benchmark import _contains_normalized_keyword

        self.assertTrue(_contains_normalized_keyword("2256", "共有 2,256 名"))
        self.assertTrue(
            _contains_normalized_keyword("1,032,766,222", "1 032 766 222 股")
        )


if __name__ == "__main__":
    unittest.main()
