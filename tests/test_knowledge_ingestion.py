"""Failure-injection tests for knowledge persistence."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from backend.agent import memory
from backend.knowledge.embeddings import HashingEmbeddings


class KnowledgeIngestionTests(unittest.TestCase):
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
            self.assertEqual(saved.name, "outside.txt")

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

    def test_local_vectorstore_delete_removes_stale_ids(self) -> None:
        document = memory.Document(
            page_content="制度内容",
            metadata={"chunk_id": "stale"},
        )
        store = memory._LocalVectorStore(HashingEmbeddings(64), [document])

        store.delete(ids=["stale"])

        self.assertEqual(store.get()["ids"], [])
        self.assertEqual(store.similarity_search("制度", k=1), [])


if __name__ == "__main__":
    unittest.main()
