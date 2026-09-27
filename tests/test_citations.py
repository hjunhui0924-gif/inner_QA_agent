"""Tests for structured, verifiable RAG citations."""

from __future__ import annotations

import unittest

from langchain_core.documents import Document

from backend.agent.citations import (
    _claims_for_marker,
    sanitize_answer_citations,
    build_citations,
    format_documents_for_prompt,
    validate_citation_claim_alignment,
    validate_citation_structure,
)


class CitationStructureTests(unittest.TestCase):
    def test_excerpt_target_preserves_soft_wraps_and_source_boundaries(self):
        for answer in ['Security review is required\nbefore export [C1].',
                       'Security review is required before export\n[C1].']:
            self.assertIn('Security review is required', _claims_for_marker(answer, 1))
        answer = 'Security review is required [C1]. Finance approval is required [C2].'
        self.assertNotIn('Finance', _claims_for_marker(answer, 1))
        self.assertNotIn('Security', _claims_for_marker(answer, 2))
        answer = '- Security review is required [C1].\n- Finance approval is required [C2].'
        self.assertNotIn('Finance', _claims_for_marker(answer, 1))

    def test_paragraph_final_citation_includes_earlier_conditions(self):
        text = ('差旅报销需提交发票、行程单和审批记录。'
                '单笔金额不超过5000元由直属主管审批；超过5000元由直属主管审批后再由财务负责人复核。'
                '部门负责人报销由上一级主管审批，超过5000元仍需财务负责人复核。'
                '出差结束后10个工作日内提交。出差住宿标准为每晚500元。')
        answer = ('差旅报销的审批要求如下：单笔金额不超过5000元由直属主管审批；'
                  '超过5000元由直属主管审批后再由财务负责人复核。'
                  '部门负责人报销由上一级主管审批，超过5000元仍需财务负责人复核 [C1]。')
        citations = build_citations([Document(page_content=text)], answer, query='差旅报销需要经过哪些审批？')
        self.assertIn('单笔金额不超过5000元由直属主管审批', citations[0]['quote'])
        self.assertIn('超过5000元由直属主管审批后再由财务负责人复核', citations[0]['quote'])
        self.assertIn('部门负责人报销由上一级主管审批', citations[0]['quote'])

    def test_provenance_does_not_claim_semantic_entailment(self):
        docs = [Document(page_content="住宿限额500元。", metadata={"source_id": "a", "chunk_id": "a:0"})]
        answer = "住宿限额600元 [C1]。"
        citations = build_citations(docs, answer)
        self.assertTrue(validate_citation_structure(answer, docs, citations)[0])
        # This is deliberately left for the online semantic Judge.
        self.assertFalse(validate_citation_claim_alignment(answer, citations)[0])

    def test_fabricated_quote_or_source_is_rejected(self):
        docs = [Document(page_content="住宿限额500元。", metadata={"source_id": "a", "chunk_id": "a:0"})]
        answer = "住宿限额500元 [C1]。"
        citation = build_citations(docs, answer)[0]
        for field, value in [("quote", "住宿限额600元。"), ("source_id", "other-user"), ("chunk_id", "other:0"), ("evidence_id", "S_forged")]:
            with self.subTest(field=field):
                self.assertFalse(validate_citation_structure(answer, docs, [{**citation, field: value}])[0])

    def test_citationless_candidates_leave_refusal_classification_to_judge(self):
        docs = [Document(page_content="住宿限额500元。")]
        self.assertTrue(validate_citation_structure("检索到的知识未包含该问题的答案。", docs, [])[0])
        self.assertTrue(validate_citation_structure("住宿限额500元。", docs, [])[0])
        self.assertTrue(validate_citation_structure("资料未说明育儿假天数，暂时无法回答。", docs, [])[0])


class EffectiveDateCitationTests(unittest.TestCase):
    def test_date_can_support_lower_precision_without_supporting_amounts(self) -> None:
        doc = Document(page_content="制度于2026年1月1日生效。")
        for claim, expected in [
            ("制度于2026年生效", True),
            ("制度于2026年1月生效", True),
            ("制度于2026年2月生效", False),
            ("制度费用为2026元", False),
        ]:
            answer = claim + " [C1]。"
            with self.subTest(claim=claim):
                self.assertEqual(validate_citation_claim_alignment(
                    answer, build_citations([doc], answer)
                )[0], expected)

    def test_date_shaped_version_is_not_normalized(self) -> None:
        for prefix in ["Release version v", "Release version ", "Release version: ", "版本号为"]:
            doc = Document(page_content=f"{prefix}2026-01-01 is active.")
            answer = f"{prefix}2026-1-1 is active [C1]."
            with self.subTest(prefix=prefix):
                self.assertFalse(validate_citation_claim_alignment(answer, build_citations([doc], answer))[0])

    def test_invalid_source_date_does_not_poison_unrelated_claim(self) -> None:
        doc = Document(page_content="Submit expense invoices (example invalid date 2026-02-30).")
        answer = "Submit expense invoices [C1]."
        self.assertTrue(validate_citation_claim_alignment(answer, build_citations([doc], answer))[0])

    def test_equivalent_date_formats_preserve_verbatim_source(self) -> None:
        for source_date, answer_date in [
            ("2026-01-01", "2026年1月1日"),
            ("2026年1月1日", "2026-01-01"),
            ("2024-02-29", "2024年2月29日"),
        ]:
            with self.subTest(source_date=source_date):
                document = Document(
                    page_content=f"## 差旅报销 v2（{source_date} 生效）\n差旅报销需提交发票。",
                    metadata={"version": "v2"},
                )
                answer = f"该差旅报销制度版本（v2）于{answer_date}生效 [C1]。"
                citations = build_citations([document], answer, query="这个版本何时生效？")
                self.assertTrue(validate_citation_claim_alignment(answer, citations)[0])
                self.assertIn(source_date, citations[0]["quote"])

    def test_wrong_dates_cannot_borrow_digits_from_other_dates_or_amounts(self) -> None:
        for quote, claim in [
            ("差旅报销于2026-01-02生效。", "差旅报销于2026-02-01生效"),
            ("差旅报销于2026-01-02生效，2027-02-01废止。", "差旅报销于2026-02-01生效"),
            ("差旅报销于2026-01-01生效，2天内提交。", "差旅报销于2026年1月2日生效"),
            ("差旅报销于2026-01-01生效。", "差旅报销于2027年1月1日生效"),
            ("差旅报销2026年发布，1月讨论，1日提交。", "差旅报销于2026年1月1日生效"),
        ]:
            with self.subTest(claim=claim, quote=quote):
                answer = claim + " [C1]。"
                citations = build_citations([Document(page_content=quote)], answer)
                self.assertFalse(validate_citation_claim_alignment(answer, citations)[0])

    def test_date_equivalence_does_not_relax_other_numbers(self) -> None:
        document = Document(page_content="差旅报销v2于2026-01-01生效，限额5000元。")
        for claim in [
            "差旅报销v3于2026年1月1日生效",
            "差旅报销v2于2026年1月1日生效，限额6000元",
            "差旅报销v2于2026年2月30日生效",
        ]:
            answer = claim + " [C1]。"
            self.assertFalse(validate_citation_claim_alignment(
                answer, build_citations([document], answer)
            )[0])


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
        self.assertEqual(citations[0]["source_id"], "law-b")
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

    def test_sanitizer_removes_irrelevant_marker_from_abstention(self) -> None:
        answer = sanitize_answer_citations(
            "检索到的知识未包含该问题的答案 [C1]。",
            [Document(page_content="unrelated", metadata={})],
        )

        self.assertNotIn("[C1]", answer)

if __name__ == "__main__":
    unittest.main()
