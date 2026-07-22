"""Behaviour tests for the knowledge ingestion module interfaces."""

from __future__ import annotations

import unittest

from langchain_core.documents import Document

from backend.knowledge.chunking import split_text
from backend.knowledge.deduplication import content_fingerprint, find_duplicate
from backend.knowledge.embeddings import EmbeddingProfile, HashingEmbeddings
from backend.retrieval.engine import RetrievalConfig, RetrievalEngine, tokenize
from backend.retrieval.reranker import RerankScore


class DeduplicationTests(unittest.TestCase):
    def test_exact_match_ignores_unicode_and_whitespace_variations(self) -> None:
        original = "报销金额为 １０００ 元。\n须经财务审批。"
        candidate = "  报销金额为 1000 元。 须经财务审批。  "

        match = find_duplicate(candidate, [{"title": "制度", "content": original}])

        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.kind, "exact")
        self.assertEqual(match.score, 1.0)
        self.assertEqual(content_fingerprint(original), content_fingerprint(candidate))

    def test_exact_match_ignores_a_legacy_stored_fingerprint(self) -> None:
        match = find_duplicate(
            "相同内容",
            [
                {
                    "title": "旧记录",
                    "content": "相同内容",
                    "content_fingerprint": "legacy-fingerprint",
                }
            ],
        )

        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.kind, "exact")

    def test_near_match_detects_a_reformatted_copy(self) -> None:
        paragraphs = [
            f"第{index}条 员工提交报销申请后，应上传合法票据并由直属负责人审批。"
            for index in range(1, 18)
        ]
        original = "\n".join(paragraphs)
        candidate = "\n\n".join(paragraphs) + "\n本文件由财务部发布。"

        match = find_duplicate(
            candidate,
            [{"title": "报销制度", "content": original}],
        )

        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.kind, "near")

    def test_same_topic_with_different_rules_is_not_a_duplicate(self) -> None:
        original = "".join(
            f"第{index}条 国内差旅住宿上限为每日500元，超额部分由员工自行承担。"
            for index in range(1, 20)
        )
        candidate = "".join(
            f"第{index}条 海外差旅住宿按目的地分级审批，凭实际发票据实报销。"
            for index in range(1, 20)
        )

        match = find_duplicate(candidate, [{"title": "差旅制度", "content": original}])

        self.assertIsNone(match)

    def test_short_text_only_uses_exact_matching(self) -> None:
        match = find_duplicate(
            "财务报销需要审批。",
            [{"title": "旧制度", "content": "财务报销无需审批。"}],
        )

        self.assertIsNone(match)


class EmbeddingTests(unittest.TestCase):
    def test_collection_identity_changes_with_embedding_profile(self) -> None:
        base = EmbeddingProfile("hashing", "offline", 256, "v1")
        changed_model = EmbeddingProfile("hashing", "offline-v2", 256, "v1")
        changed_dimensions = EmbeddingProfile("hashing", "offline", 1024, "v1")

        identities = {
            base.collection_name("enterprise"),
            changed_model.collection_name("enterprise"),
            changed_dimensions.collection_name("enterprise"),
        }

        self.assertEqual(len(identities), 3)

    def test_invalid_embedding_profile_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            EmbeddingProfile("hashing", "", 1024, "v1").validate()

    def test_hashing_adapter_is_deterministic_and_normalized(self) -> None:
        embeddings = HashingEmbeddings(dimensions=64)

        first = embeddings.embed_query("员工报销流程")
        second = embeddings.embed_query("员工报销流程")

        self.assertEqual(first, second)
        self.assertAlmostEqual(sum(value * value for value in first), 1.0)


class ChunkingTests(unittest.TestCase):
    def test_short_document_stays_intact(self) -> None:
        self.assertEqual(split_text("第一条。\n\n第二条。"), ["第一条。\n\n第二条。"])

    def test_long_document_respects_size_and_retains_sentence_text(self) -> None:
        source = "".join(f"第{index}条，这是制度内容。" for index in range(50))

        chunks = split_text(source, chunk_size=120, overlap=20)

        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= 120 for chunk in chunks))
        self.assertIn("第0条，这是制度内容。", chunks[0])
        self.assertIn("第49条，这是制度内容。", chunks[-1])

    def test_invalid_overlap_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            split_text("内容", chunk_size=100, overlap=100)


class _DenseFake:
    def __init__(self, documents: list[Document]) -> None:
        self.documents = documents

    def similarity_search(self, query: str, k: int = 4) -> list[Document]:
        return self.documents[:k]


class _ReorderReranker:
    def rerank(
        self,
        query: str,
        documents: list[Document],
        top_n: int,
    ) -> list[RerankScore]:
        return [RerankScore(index=1, score=0.99)][:top_n]


class _FailingReranker:
    def rerank(
        self,
        query: str,
        documents: list[Document],
        top_n: int,
    ) -> list[RerankScore]:
        raise TimeoutError("simulated timeout")


class _PartialReranker:
    def rerank(
        self,
        query: str,
        documents: list[Document],
        top_n: int,
    ) -> list[RerankScore]:
        return [RerankScore(index=1, score=0.9)]


class RetrievalTests(unittest.TestCase):
    def test_lexical_rank_can_rescue_an_exact_identifier(self) -> None:
        irrelevant = Document(
            page_content="会议室预约需要提前十分钟确认。",
            metadata={"chunk_id": "meeting"},
        )
        relevant = Document(
            page_content="法律意见书文号为(2026)承义法字第00091号。",
            metadata={"chunk_id": "legal"},
        )
        engine = RetrievalEngine(
            _DenseFake([irrelevant, relevant]),
            [irrelevant, relevant],
            config=RetrievalConfig(
                dense_candidate_k=2,
                lexical_candidate_k=2,
                rerank_candidate_k=2,
                production_strategy="fusion",
            ),
        )

        result = engine.retrieve("承义法字00091", top_k=1)

        self.assertEqual(result.documents[0].metadata["chunk_id"], "legal")
        self.assertEqual(result.documents[0].metadata["lexical_rank"], 1)

    def test_reranker_can_reorder_fused_candidates(self) -> None:
        first = Document(page_content="甲", metadata={"chunk_id": "first"})
        second = Document(page_content="乙", metadata={"chunk_id": "second"})
        engine = RetrievalEngine(
            _DenseFake([first, second]),
            [first, second],
            config=RetrievalConfig(
                dense_candidate_k=2,
                lexical_candidate_k=2,
                rerank_candidate_k=2,
            ),
            reranker=_ReorderReranker(),
        )

        result = engine.retrieve("没有词法命中的查询", top_k=1)

        self.assertTrue(result.rerank_used)
        self.assertEqual(result.documents[0].metadata["chunk_id"], "second")
        self.assertEqual(result.documents[0].metadata["rerank_score"], 0.99)

    def test_reranker_failure_falls_back_to_fusion(self) -> None:
        first = Document(page_content="甲", metadata={"chunk_id": "first"})
        engine = RetrievalEngine(
            _DenseFake([first]),
            [first],
            config=RetrievalConfig(
                dense_candidate_k=1,
                lexical_candidate_k=1,
                rerank_candidate_k=1,
            ),
            reranker=_FailingReranker(),
        )

        result = engine.retrieve("甲", top_k=1)

        self.assertFalse(result.rerank_used)
        self.assertIn("TimeoutError", result.degraded_reason or "")
        self.assertEqual(
            result.documents[0].metadata["retrieval_stage"],
            "fusion_fallback",
        )

    def test_partial_rerank_response_is_filled_from_fusion(self) -> None:
        first = Document(page_content="甲", metadata={"chunk_id": "first"})
        second = Document(page_content="乙", metadata={"chunk_id": "second"})
        engine = RetrievalEngine(
            _DenseFake([first, second]),
            [first, second],
            config=RetrievalConfig(
                dense_candidate_k=2,
                lexical_candidate_k=2,
                rerank_candidate_k=2,
            ),
            reranker=_PartialReranker(),
        )

        result = engine.retrieve("查询", top_k=2)

        self.assertEqual(len(result.documents), 2)
        self.assertEqual(result.documents[0].metadata["chunk_id"], "second")
        self.assertEqual(
            result.documents[1].metadata["retrieval_stage"],
            "rerank_unscored_fallback",
        )

    def test_add_documents_deduplicates_within_the_same_batch(self) -> None:
        first = Document(page_content="制度内容", metadata={"chunk_id": "same"})
        engine = RetrievalEngine(
            _DenseFake([]),
            [],
            config=RetrievalConfig(production_strategy="lexical"),
        )

        engine.add_documents([first, first])
        result = engine.retrieve("制度内容", top_k=5)

        self.assertEqual(len(result.documents), 1)

    def test_tokenizer_keeps_ascii_identifiers_and_chinese_ngrams(self) -> None:
        tokens = tokenize("文号ABC_001")

        self.assertIn("abc_001", tokens)
        self.assertIn("文号", tokens)


if __name__ == "__main__":
    unittest.main()
