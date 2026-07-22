"""Failure-injection tests for knowledge persistence."""

from __future__ import annotations

import unittest
import json
import zipfile
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from filelock import FileLock, Timeout

from backend.agent import memory
from backend.config import Settings
from backend.knowledge.embeddings import HashingEmbeddings
from backend.retrieval.engine import RetrievalConfig, RetrievalEngine


class KnowledgeIngestionTests(unittest.TestCase):
    def test_segment_metadata_survives_chunking(self) -> None:
        documents = memory._chunk_documents_from_record(
            "Policy",
            "First page text.\nSecond page text.",
            "official",
            original_filename="policy.pdf",
            document_id="policy-id",
            segments=[
                {"text": "First page text.", "page": 1, "section": "Scope"},
                {"text": "Second page text.", "page": 2, "section": "Duties"},
            ],
        )

        self.assertEqual(documents[0].metadata["page"], 1)
        self.assertEqual(documents[0].metadata["section"], "Scope")
        self.assertEqual(documents[-1].metadata["page"], 2)
        self.assertEqual(documents[-1].metadata["section"], "Duties")

    def test_docx_heading_is_indexed_as_text_and_metadata(self) -> None:
        if memory.DocxDocument is None:
            self.skipTest("python-docx is not installed")
        document = memory.DocxDocument()
        document.add_heading("Access Control", level=1)
        document.add_paragraph("Only administrators may approve access.")

        segments = memory._docx_segments(document)

        self.assertEqual(segments[0]["section"], "Access Control")
        self.assertTrue(segments[0]["text"].startswith("Access Control\n"))

    def test_invalid_segment_page_type_is_ignored(self) -> None:
        segments = memory._normalize_segments([{"text": "content", "page": []}])

        self.assertNotIn("page", segments[0])

    def test_exact_legacy_duplicate_backfills_segment_metadata(self) -> None:
        with TemporaryDirectory() as directory:
            kb_path = Path(directory) / "knowledge.json"
            content = "Page one policy. Page two policy."
            kb_path.write_text(
                json.dumps([{"title": "Policy", "content": content, "source": "test"}]),
                encoding="utf-8",
            )
            old_documents = memory._chunk_documents_from_record("Policy", content, "test")
            store = memory._LocalVectorStore(HashingEmbeddings(64), old_documents)
            engine = RetrievalEngine(
                store,
                old_documents,
                config=RetrievalConfig(production_strategy="lexical"),
            )
            with (
                patch.object(memory.settings, "knowledge_base_path", str(kb_path)),
                patch.object(memory.settings, "embedding_provider", "hashing"),
                patch.object(memory.settings, "reranker_enabled", False),
            ):
                memory.set_vectorstore(store)
                memory.set_retriever(engine)
                result = memory.add_knowledge_record(
                    "Policy",
                    content,
                    "test",
                    "policy.pdf",
                    segments=[
                        {"text": "Page one policy.", "page": 1},
                        {"text": "Page two policy.", "page": 2},
                    ],
                )

            saved = json.loads(kb_path.read_text(encoding="utf-8"))[0]
            self.assertTrue(result["metadata_upgraded"])
            self.assertEqual(saved["segments"][1]["page"], 2)
            self.assertEqual(store.get()["metadatas"][-1]["page"], 2)

    def test_invalid_resource_limit_fails_at_configuration_time(self) -> None:
        with self.assertRaisesRegex(ValueError, "limits must be positive"):
            Settings(_env_file=None, max_upload_bytes=0)

    def test_chunk_configuration_changes_collection_identity(self) -> None:
        with (
            patch.object(memory.settings, "knowledge_chunk_size", 800),
            patch.object(memory.settings, "knowledge_chunk_overlap", 120),
        ):
            first = memory._embedding_profile().collection_name("knowledge")
        with (
            patch.object(memory.settings, "knowledge_chunk_size", 600),
            patch.object(memory.settings, "knowledge_chunk_overlap", 80),
        ):
            second = memory._embedding_profile().collection_name("knowledge")

        self.assertNotEqual(first, second)

    def test_uploaded_filename_cannot_escape_upload_directory(self) -> None:
        with TemporaryDirectory() as directory:
            with patch.object(memory.settings, "upload_dir", directory):
                saved = memory.save_uploaded_file("../../outside.txt", b"safe")

            self.assertEqual(saved.parent.resolve(), Path(directory).resolve())
            self.assertTrue(saved.name.startswith("outside-"))
            self.assertEqual(saved.suffix, ".txt")

    def test_same_upload_name_gets_distinct_atomic_paths(self) -> None:
        with TemporaryDirectory() as directory:
            with patch.object(memory.settings, "upload_dir", directory):
                first = memory.save_uploaded_file("policy.txt", b"first")
                second = memory.save_uploaded_file("policy.txt", b"second")

            self.assertNotEqual(first, second)
            self.assertEqual(first.read_bytes(), b"first")
            self.assertEqual(second.read_bytes(), b"second")

    def test_json_failure_rolls_back_new_vector_ids(self) -> None:
        with TemporaryDirectory() as directory:
            kb_path = Path(directory) / "knowledge.json"
            vectorstore = Mock()
            memory.set_vectorstore(vectorstore)
            with (
                patch.object(memory.settings, "knowledge_base_path", str(kb_path)),
                patch.object(memory, "_save_json_file", side_effect=OSError("disk full")),
            ):
                with self.assertRaisesRegex(OSError, "disk full"):
                    memory.add_knowledge_record(
                        "新制度",
                        "这是一份全新的制度内容，用于验证事务回滚。",
                        "test",
                        "policy.txt",
                    )

            vectorstore.add_documents.assert_called_once()
            vectorstore.delete.assert_called_once()
            self.assertFalse(kb_path.exists())

    def test_cross_process_file_lock_blocks_competing_writer(self) -> None:
        with TemporaryDirectory() as directory:
            kb_path = Path(directory) / "knowledge.json"
            external_lock = FileLock(f"{kb_path}.lock")
            with (
                patch.object(memory.settings, "knowledge_base_path", str(kb_path)),
                patch.object(memory.settings, "knowledge_write_lock_timeout_seconds", 0.01),
                external_lock,
            ):
                with self.assertRaises(Timeout):
                    memory.add_knowledge_record("title", "content", "test", "x.txt")

    def test_local_vectorstore_delete_removes_stale_ids(self) -> None:
        document = memory.Document(
            page_content="制度内容",
            metadata={"chunk_id": "stale"},
        )
        store = memory._LocalVectorStore(HashingEmbeddings(64), [document])

        store.delete(ids=["stale"])

        self.assertEqual(store.get()["ids"], [])
        self.assertEqual(store.similarity_search("制度", k=1), [])

    def test_startup_reconciles_same_id_with_stale_metadata(self) -> None:
        with TemporaryDirectory() as directory:
            kb_path = Path(directory) / "knowledge.json"
            kb_path.write_text(
                json.dumps(
                    [{"title": "新标题", "content": "相同内容", "source": "new"}],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            expected = memory._chunk_documents_from_record(
                "新标题",
                "相同内容",
                "new",
            )[0]
            store = Mock()
            store.get.return_value = {
                "ids": [expected.metadata["chunk_id"]],
                "documents": [expected.page_content],
                "metadatas": [{**expected.metadata, "title": "旧标题"}],
            }
            with (
                patch.object(memory, "Chroma", return_value=store),
                patch.object(memory.settings, "knowledge_base_path", str(kb_path)),
                patch.object(memory.settings, "chroma_persist_dir", directory),
                patch.object(memory.settings, "embedding_provider", "hashing"),
            ):
                memory._create_vectorstore_sync()

            store.add_documents.assert_called_once()

    def test_worker_refreshes_lexical_state_when_json_version_changes(self) -> None:
        with TemporaryDirectory() as directory:
            kb_path = Path(directory) / "knowledge.json"
            kb_path.write_text(
                json.dumps(
                    [{"title": "旧制度", "content": "旧内容", "source": "test"}],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            initial_documents = memory._chunk_documents_from_record(
                "旧制度",
                "旧内容",
                "test",
            )
            store = memory._LocalVectorStore(HashingEmbeddings(64), initial_documents)
            engine = RetrievalEngine(
                store,
                initial_documents,
                config=RetrievalConfig(production_strategy="lexical"),
            )
            with (
                patch.object(memory.settings, "knowledge_base_path", str(kb_path)),
                patch.object(memory.settings, "reranker_enabled", False),
            ):
                memory.set_vectorstore(store)
                memory.set_retriever(engine)
                kb_path.write_text(
                    json.dumps(
                        [
                            {"title": "新制度", "content": "独特新规则", "source": "test"}
                        ],
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )

                refreshed = memory.get_retriever().retrieve(
                    "独特新规则",
                    top_k=1,
                    strategy="lexical",
                )

            self.assertEqual(refreshed.documents[0].metadata["title"], "新制度")

    def test_extracted_text_limit_is_enforced(self) -> None:
        with patch.object(memory.settings, "max_extracted_chars", 5):
            with self.assertRaisesRegex(ValueError, "文本过大"):
                memory.extract_text_from_upload("large.txt", b"123456")

    def test_docx_uncompressed_limit_is_checked_before_parsing(self) -> None:
        stream = BytesIO()
        with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("word/document.xml", "x" * 100)

        with patch.object(memory.settings, "max_archive_uncompressed_bytes", 10):
            with self.assertRaisesRegex(ValueError, "解压后的内容过大"):
                memory.extract_text_from_upload("bomb.docx", stream.getvalue())


if __name__ == "__main__":
    unittest.main()
