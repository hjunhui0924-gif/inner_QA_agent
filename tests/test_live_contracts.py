"""Credential-gated contracts for hosted embedding and rerank APIs."""

from __future__ import annotations

import os
import unittest

from langchain_core.documents import Document

from backend.config import settings
from backend.knowledge.embeddings import EmbeddingProfile, create_embeddings
from backend.retrieval.reranker import DashScopeReranker


RUN_LIVE = os.getenv("RUN_LIVE_RAG_TESTS") == "1"


@unittest.skipUnless(RUN_LIVE, "set RUN_LIVE_RAG_TESTS=1 to call DashScope")
class HostedModelContractTests(unittest.TestCase):
    def test_embedding_contract_returns_configured_dimensions(self) -> None:
        profile = EmbeddingProfile(
            provider="dashscope",
            model=settings.embedding_model,
            dimensions=settings.embedding_dimensions,
            index_version="live-contract",
            api_key=settings.dashscope_api_key,
            base_url=settings.dashscope_base_url,
        )

        vector = create_embeddings(profile).embed_query("企业数据安全制度")

        self.assertEqual(len(vector), settings.embedding_dimensions)
        self.assertTrue(any(vector))

    def test_reranker_contract_places_direct_evidence_first(self) -> None:
        reranker = DashScopeReranker(
            api_key=settings.dashscope_api_key,
            model=settings.reranker_model,
            endpoint=settings.reranker_endpoint,
            api_style=settings.reranker_api_style,  # type: ignore[arg-type]
            timeout_seconds=settings.reranker_timeout_seconds,
        )
        documents = [
            Document(page_content="会议室使用前需要预约。"),
            Document(page_content="处理敏感个人信息应当取得个人的单独同意。"),
        ]

        results = reranker.rerank(
            "处理敏感个人信息需要取得哪种同意？",
            documents,
            top_n=2,
        )

        self.assertEqual(results[0].index, 1)


if __name__ == "__main__":
    unittest.main()

