"""Tests for structured, verifiable RAG citations."""

from __future__ import annotations

import unittest

from langchain_core.documents import Document

from backend.agent.citations import (
    sanitize_answer_citations,
    build_citations,
    format_documents_for_prompt,
)


class CitationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.documents = [
            Document(
                page_content="Article 10 requires a security review before export.",
                metadata={
                    "document_id": "law-a",
                    "title": "Security Law",
                    "source": "official",
                    "original_filename": "law.pdf",
                    "page": 3,
                    "section": "Article 10",
                    "chunk_id": "law-a:2",
                },
            ),
            Document(
                page_content="Article 20 requires incident reporting within one hour.",
                metadata={
                    "document_id": "law-b",
                    "title": "Incident Rules",
                    "source": "official",
                    "original_filename": "rules.docx",
                    "section": "Article 20",
                    "chunk_id": "law-b:4",
                },
            ),
        ]

    def test_prompt_uses_stable_citation_markers_and_locations(self) -> None:
        rendered = format_documents_for_prompt(self.documents)

        self.assertIn("[C1]", rendered)
        self.assertIn("page 3", rendered)
        self.assertIn("[C2]", rendered)
        self.assertIn("Article 20", rendered)

    def test_only_referenced_sources_are_emitted_with_verbatim_quote(self) -> None:
        citations = build_citations(
            self.documents,
            "Reports are required within one hour [C2].",
            query="When must an incident be reported?",
        )

        self.assertEqual(len(citations), 1)
        self.assertEqual(citations[0]["citation_id"], "C2")
        self.assertEqual(citations[0]["chunk_id"], "law-b:4")
        self.assertEqual(citations[0]["section"], "Article 20")
        self.assertIn(citations[0]["quote"], self.documents[1].page_content)
        self.assertEqual(citations[0]["verification_status"], "provenance_only")

    def test_uncited_answer_does_not_fabricate_used_citations(self) -> None:
        citations = build_citations(
            self.documents,
            "Reports are required within one hour.",
            query="When must an incident be reported?",
        )

        self.assertEqual(citations, [])

    def test_quote_prefers_matching_number_and_claim_over_generic_sentence(self) -> None:
        document = Document(
            page_content=(
                "The department manages policy implementation and related work. "
                "This policy takes effect on 2025-01-01."
            ),
            metadata={"chunk_id": "policy:0", "title": "Policy"},
        )

        citation = build_citations(
            [document],
            "The policy takes effect on 2025-01-01 [ C1 ].",
            query="When does the policy take effect?",
        )[0]

        self.assertEqual(citation["quote"], "This policy takes effect on 2025-01-01.")

    def test_sanitizer_preserves_in_range_marker_as_provenance_only(self) -> None:
        documents = [
            Document(
                page_content="This policy takes effect on 2025-01-01.",
                metadata={"chunk_id": "right"},
            ),
            Document(
                page_content="The department receives complaints.",
                metadata={"chunk_id": "wrong"},
            ),
        ]

        answer = sanitize_answer_citations(
            "This policy takes effect on 2025-01-01 [C2].",
            documents,
            query="When does it take effect?",
        )

        self.assertNotIn("[C1]", answer)
        self.assertIn("[C2]", answer)

    def test_alignment_does_not_attach_evidence_to_list_number(self) -> None:
        answer = sanitize_answer_citations(
            "1. The policy takes effect on 2025-01-01.",
            [Document(page_content="The policy takes effect on 2025-01-01.", metadata={})],
        )

        self.assertFalse(answer.startswith("1 [C1]."))
        self.assertNotIn("[C1]", answer)

    def test_sanitizer_does_not_claim_to_resolve_semantic_conflicts(self) -> None:
        answer = sanitize_answer_citations(
            "The policy prohibits data export [C1].",
            [Document(page_content="The policy permits data export.", metadata={})],
        )

        self.assertIn("[C1]", answer)

    def test_sanitizer_keeps_supported_model_marker(self) -> None:
        answer = sanitize_answer_citations(
            "The policy takes effect on 2025-01-01 [C1].",
            [Document(page_content="The policy takes effect on 2025-01-01.", metadata={})],
        )

        self.assertIn("[C1]", answer)

    def test_sanitizer_removes_marker_outside_candidate_range(self) -> None:
        answer = sanitize_answer_citations(
            "The policy takes effect on 2025-01-01 [C9].",
            [Document(page_content="The policy takes effect on 2025-01-01.", metadata={})],
        )

        self.assertNotIn("[C9]", answer)

if __name__ == "__main__":
    unittest.main()
