"""Regression coverage for the structured RAG optimization work."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from langchain_core.documents import Document

from backend.agent import memory
from backend.agent.citations import Citation, build_citations
from backend.config import Settings
from backend.evaluation.schema import parse_eval_cases, validate_dataset_shape
from backend.knowledge.schema import validate_knowledge_records
from backend.observability.tracing import build_trace, record_trace
from backend.retrieval.engine import RetrievalConfig, RetrievalEngine, RetrievalFilter
from backend.knowledge.embeddings import HashingEmbeddings


BASE_DIR = Path(__file__).resolve().parents[1]


class StructuredDataTests(unittest.TestCase):
    def test_knowledge_records_have_valid_metadata_and_checksums(self) -> None:
        records = json.loads(
            (BASE_DIR / "data" / "knowledge_base.json").read_text(encoding="utf-8")
        )

        self.assertEqual(validate_knowledge_records(records), [])
        self.assertGreaterEqual(len(records), 20)
        self.assertEqual(
            {str(record["department"]) for record in records},
            {"HR", "Finance", "Procurement", "IT", "Legal", "Administration"},
        )
        self.assertTrue(all(record["source_type"] == "synthetic_seed" for record in records[:-1]))

    def test_structured_eval_corpus_has_three_splits_and_required_coverage(self) -> None:
        raw = json.loads(
            (BASE_DIR / "data" / "evals" / "enterprise_rag_eval.json").read_text(
                encoding="utf-8"
            )
        )
        cases = parse_eval_cases(raw, require_structured=True)

        self.assertGreaterEqual(len(cases), 150)
        self.assertEqual(validate_dataset_shape(cases, minimum_cases=150), [])
        self.assertEqual({case.split for case in cases}, {"development", "regression", "held_out"})
        self.assertEqual(sum(not case.answerable for case in cases), 60)


class RetrievalFilterTests(unittest.TestCase):
    def test_filter_by_version_excludes_other_versions(self) -> None:
        documents = [
            Document(
                page_content="旧版规则。",
                metadata={
                    "chunk_id": "leave-v1:0",
                    "source_id": "leave-v1",
                    "document_id": "leave-v1",
                    "version": "v1",
                    "status": "deprecated",
                },
            ),
            Document(
                page_content="当前规则。",
                metadata={
                    "chunk_id": "leave-v2:0",
                    "source_id": "leave-v2",
                    "document_id": "leave-v2",
                    "version": "v2",
                    "status": "active",
                },
            ),
        ]
        store = memory._LocalVectorStore(HashingEmbeddings(64), documents)
        engine = RetrievalEngine(
            store,
            documents,
            config=RetrievalConfig(production_strategy="fusion"),
        )

        result = engine.retrieve(
            "请假规则",
            top_k=4,
            filters=RetrievalFilter(versions=frozenset({"v1"})),
        )

        self.assertEqual(
            [doc.metadata["version"] for doc in result.documents],
            ["v1"],
        )
        self.assertEqual(result.applied_filter["versions"], ["v1"])

    def test_filter_and_current_version_preference(self) -> None:
        old = Document(
            page_content="请假超过五天需抄送部门负责人。",
            metadata={
                "chunk_id": "leave-v1:0",
                "source_id": "leave-v1",
                "document_id": "leave-v1",
                "department": "HR",
                "status": "deprecated",
                "effective_from": "2025-01-01",
                "effective_to": "2025-12-31",
                "version": "v1",
                "document_family": "leave",
            },
        )
        current = Document(
            page_content="当前请假超过三天需抄送部门负责人和人力资源。",
            metadata={
                "chunk_id": "leave-v2:0",
                "source_id": "leave-v2",
                "document_id": "leave-v2",
                "department": "HR",
                "status": "active",
                "effective_from": "2026-01-01",
                "effective_to": None,
                "version": "v2",
                "document_family": "leave",
            },
        )
        newer = Document(
            page_content="最新请假超过两天需抄送部门负责人和人力资源。",
            metadata={
                "chunk_id": "leave-v3:0",
                "source_id": "leave-v3",
                "document_id": "leave-v3",
                "department": "HR",
                "status": "active",
                "effective_from": "2027-01-01",
                "effective_to": None,
                "version": "v3",
                "document_family": "leave",
            },
        )
        store = memory._LocalVectorStore(HashingEmbeddings(64), [old, current, newer])
        engine = RetrievalEngine(
            store,
            [old, current, newer],
            config=RetrievalConfig(production_strategy="fusion"),
        )

        result = engine.retrieve(
            "当前请假规则",
            top_k=3,
            filters=RetrievalFilter(
                departments=frozenset({"HR"}),
                prefer_current=True,
            ),
        )

        self.assertEqual(
            [doc.metadata["source_id"] for doc in result.documents],
            ["leave-v3", "leave-v2", "leave-v1"],
        )
        self.assertEqual(result.applied_filter["departments"], ["HR"])

    def test_upload_rejects_a_reused_source_id_before_indexing(self) -> None:
        with TemporaryDirectory() as directory:
            kb_path = Path(directory) / "knowledge.json"
            kb_path.write_text(
                json.dumps(
                    [
                        {
                            "id": "fixed-id",
                            "title": "Existing",
                            "content": "existing content",
                        }
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            with (
                patch.object(memory.settings, "knowledge_base_path", str(kb_path)),
                patch.object(memory, "_VECTORSTORE", Mock()),
            ):
                with self.assertRaisesRegex(ValueError, "source_id"):
                    memory.add_knowledge_record(
                        "New",
                        "new content",
                        "test",
                        "new.txt",
                        record_id="fixed-id",
                    )


class TracePrivacyTests(unittest.TestCase):
    def test_trace_is_metadata_only_by_default(self) -> None:
        document = Document(
            page_content="secret policy body",
            metadata={"source_id": "policy", "chunk_id": "policy:0", "title": "Policy"},
        )
        trace = build_trace(
            trace_id="trace-private",
            query="secret user question",
            state={
                "route": "rag",
                "answer": "secret answer [C1]",
                "retrieved_docs": [document],
                "citations": [
                    {
                        "citation_id": "C1",
                        "quote": "secret policy body",
                        "chunk_id": "policy:0",
                    }
                ],
            },
        )

        self.assertEqual(trace["content_recording"], "metadata_only")
        self.assertNotIn("secret user question", trace["query"])
        self.assertNotIn("secret policy body", json.dumps(trace, ensure_ascii=False))

    def test_trace_retention_days_is_enforced_as_a_positive_setting(self) -> None:
        with self.assertRaisesRegex(ValueError, "retention_days"):
            Settings(_env_file=None, trace_retention_days=0)


if __name__ == "__main__":
    unittest.main()
