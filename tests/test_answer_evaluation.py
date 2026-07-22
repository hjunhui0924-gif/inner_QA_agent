"""Deterministic answer-level RAG evaluation tests."""

from __future__ import annotations

import unittest

from langchain_core.documents import Document

from backend.evaluation.answers import AnswerEvalCase, evaluate_answer


class AnswerEvaluationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.document = Document(
            page_content="The policy takes effect on 2025-01-01 and records are kept for 3 years.",
            metadata={"chunk_id": "policy:0", "document_id": "policy"},
        )
        self.citation = {
            "citation_id": "C1",
            "document_id": "policy",
            "title": "Policy",
            "source": "official",
            "filename": "policy.pdf",
            "page": 1,
            "section": "Effective date",
            "chunk_id": "policy:0",
            "quote": self.document.page_content,
        }

    def test_scores_facts_numbers_and_citations_without_substring_fragility(self) -> None:
        result = evaluate_answer(
            AnswerEvalCase(
                answer="The policy takes effect on 2025-01-01 and records are kept for 3 years [C1].",
                expected_facts=["2025-01-01", "3 years"],
                answerable=True,
            ),
            [self.citation],
            [self.document],
        )

        self.assertEqual(result.gold_phrase_match_rate, 1.0)
        self.assertEqual(result.numeric_match_rate, 1.0)
        self.assertEqual(result.citation_provenance_accuracy, 1.0)
        self.assertEqual(result.extractive_overlap_rate, 1.0)
        self.assertEqual(result.citation_completeness, 1.0)
        self.assertEqual(result.failure_type, "none")

    def test_detects_wrong_number_even_when_other_words_overlap(self) -> None:
        result = evaluate_answer(
            AnswerEvalCase(
                answer="Records are kept for 5 years [C1].",
                expected_facts=["3 years"],
                answerable=True,
            ),
            [self.citation],
            [self.document],
        )

        self.assertEqual(result.numeric_match_rate, 0.0)
        self.assertEqual(result.failure_type, "gold_phrase_mismatch")

    def test_scores_supported_abstention_for_no_answer_case(self) -> None:
        result = evaluate_answer(
            AnswerEvalCase(
                answer="The provided documents do not contain enough information to answer.",
                expected_facts=[],
                answerable=False,
            ),
            [],
            [],
        )

        self.assertEqual(result.abstention_accuracy, 1.0)
        self.assertEqual(result.failure_type, "none")

    def test_no_numeric_requirement_statement_counts_as_abstention(self) -> None:
        result = evaluate_answer(
            AnswerEvalCase(
                answer="这些规定未对模型参数量设定具体数值要求。",
                expected_facts=[],
                answerable=False,
            ),
            [],
            [],
        )

        self.assertEqual(result.abstention_accuracy, 1.0)

    def test_wrong_document_premise_counts_as_abstention(self) -> None:
        result = evaluate_answer(
            AnswerEvalCase(
                answer="该法律不是由律师事务所出具的文件。",
                expected_facts=[],
                answerable=False,
            ),
            [],
            [],
        )

        self.assertEqual(result.abstention_accuracy, 1.0)

    def test_provenance_does_not_claim_unrelated_quote_is_semantic_support(self) -> None:
        unrelated = dict(self.citation)
        unrelated["quote"] = "The policy takes effect on 2025-01-01"
        result = evaluate_answer(
            AnswerEvalCase(
                answer="Records are kept for 3 years [C1].",
                expected_facts=["3 years"],
                answerable=True,
            ),
            [unrelated],
            [self.document],
        )

        self.assertEqual(result.extractive_overlap_rate, 0.0)
        self.assertEqual(result.citation_provenance_accuracy, 1.0)
        self.assertEqual(result.failure_type, "none")

    def test_negated_fact_cannot_score_against_positive_evidence(self) -> None:
        result = evaluate_answer(
            AnswerEvalCase(
                answer="Records are not kept for 3 years [C1].",
                expected_facts=["Records are kept for 3 years"],
                answerable=True,
            ),
            [self.citation],
            [self.document],
        )

        self.assertEqual(result.gold_phrase_match_rate, 0.0)
        self.assertEqual(result.extractive_overlap_rate, 0.0)

    def test_conflicting_relations_cannot_score_as_supported(self) -> None:
        cases = [
            ("Users must export data [C1].", "Users may export data."),
            ("Apply after June 1 [C1].", "Apply before June 1."),
            ("At most 10 records [C1].", "At least 10 records."),
            ("The rate decreases 5% [C1].", "The rate increases 5%."),
            ("Must not be more than 10 [C1].", "Must not be less than 10."),
            ("Not accepted after June 1 [C1].", "Not accepted before June 1."),
        ]
        for answer, evidence in cases:
            with self.subTest(answer=answer):
                document = Document(page_content=evidence, metadata={"chunk_id": "x"})
                citation = dict(self.citation)
                citation["chunk_id"] = "x"
                citation["quote"] = evidence
                result = evaluate_answer(
                    AnswerEvalCase(answer=answer, expected_facts=[evidence], answerable=True),
                    [citation],
                    [document],
                )
                self.assertEqual(result.gold_phrase_match_rate, 0.0)
                self.assertEqual(result.extractive_overlap_rate, 0.0)

    def test_reversed_roles_cannot_score_as_supported(self) -> None:
        evidence = "The manager approves the employee request."
        answer = "The employee approves the manager request [C1]."
        document = Document(page_content=evidence, metadata={"chunk_id": "roles"})
        citation = dict(self.citation)
        citation["chunk_id"] = "roles"
        citation["quote"] = evidence

        result = evaluate_answer(
            AnswerEvalCase(answer=answer, expected_facts=[evidence], answerable=True),
            [citation],
            [document],
        )

        self.assertEqual(result.gold_phrase_match_rate, 0.0)
        self.assertEqual(result.extractive_overlap_rate, 0.0)

    def test_single_predicate_replacement_cannot_score_as_supported(self) -> None:
        evidence = "The manager carefully approves the employee annual leave request today."
        answer = "The manager carefully rejects the employee annual leave request today [C1]."
        document = Document(page_content=evidence, metadata={"chunk_id": "predicate"})
        citation = dict(self.citation)
        citation["chunk_id"] = "predicate"
        citation["quote"] = evidence

        result = evaluate_answer(
            AnswerEvalCase(answer=answer, expected_facts=[evidence], answerable=True),
            [citation],
            [document],
        )

        self.assertEqual(result.gold_phrase_match_rate, 0.0)
        self.assertEqual(result.extractive_overlap_rate, 0.0)


if __name__ == "__main__":
    unittest.main()
