"""Regression tests for validated answer streaming helpers."""

from __future__ import annotations

import unittest

from pydantic import ValidationError

from backend.api.routes import ChatRequest, _answer_chunks, health


class ValidatedStreamingTests(unittest.TestCase):
    def test_chunks_reconstruct_only_the_authoritative_answer(self) -> None:
        final_answer = "good answer with verified citation [C1]"

        chunks = _answer_chunks(final_answer, chunk_size=7)

        self.assertEqual("".join(chunks), final_answer)
        self.assertNotIn("bad answer", "".join(chunks))

    def test_invalid_chunk_size_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "positive"):
            _answer_chunks("answer", chunk_size=0)

    def test_single_message_cannot_exceed_conversation_budget(self) -> None:
        with self.assertRaises(ValidationError):
            ChatRequest(message="测" * 11_000)

    def test_health_contract_exposes_runtime_configuration(self) -> None:
        payload = health()

        self.assertEqual(payload["status"], "ok")
        self.assertTrue(payload["model"])
        self.assertIn(payload["retrieval_strategy"], {"dense", "lexical", "fusion", "rerank"})


if __name__ == "__main__":
    unittest.main()
