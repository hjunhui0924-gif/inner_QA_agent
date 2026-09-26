"""Model selection regressions for generation and automatic judging."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from langchain_core.documents import Document
from langchain_core.messages import AIMessage
from pydantic import ValidationError

from backend.agent.nodes import _build_model, check_hallucination
from backend.config import Settings, settings as configured_settings
from scripts import run_answer_benchmark


class ModelConfigurationTests(unittest.TestCase):
    def test_citation_mode_defaults_to_validated_legacy_until_live_gate_passes(self):
        self.assertEqual(Settings(_env_file=None).citation_validation_mode, "legacy")
        self.assertEqual(Settings(_env_file=None, citation_validation_mode="judge").citation_validation_mode, "judge")
        with self.assertRaises(ValidationError):
            Settings(_env_file=None, citation_validation_mode="disabled")

    def test_text_generation_model_is_the_default_generation_and_judge_model(self) -> None:
        configured = Settings(_env_file=None)

        self.assertEqual(configured.model_name, "qwen3.8-flash")
        self.assertEqual(configured.judge_model_name, "qwen3.8-flash")
        self.assertEqual(configured.embedding_model, "qwen3.7-text-embedding")
        self.assertFalse(configured.qwen_enable_thinking)

    def test_model_builder_accepts_an_explicit_judge_model(self) -> None:
        with patch("backend.agent.nodes.settings", Settings(_env_file=None, DASHSCOPE_API_KEY="test-key")):
            model = _build_model(temperature=0, model_name="judge-model")

        self.assertEqual(model.model_name, "judge-model")
        self.assertEqual(model.extra_body, {"enable_thinking": False})

    def test_deepseek_builder_uses_its_own_credentials_and_protocol(self):
        configured = Settings(_env_file=None, model_provider="deepseek",
                              DEEPSEEK_API_KEY="deepseek-test", model_name="deepseek-flash")
        with patch("backend.agent.nodes.settings", configured):
            model = _build_model(max_tokens=24)
        self.assertEqual(model.openai_api_key.get_secret_value(), "deepseek-test")
        self.assertEqual(model.openai_api_base, "https://api.deepseek.com")
        self.assertEqual(model.extra_body, {"thinking": {"type": "disabled"}})
        self.assertEqual(model.max_tokens, 24)
        self.assertEqual(configured.text_extra_body(title=True), model.extra_body)

    def test_deepseek_missing_key_does_not_fall_back_to_dashscope_key(self):
        configured = Settings(_env_file=None, model_provider="deepseek", DEEPSEEK_API_KEY="", DASHSCOPE_API_KEY="test")
        with patch("backend.agent.nodes.settings", configured):
            with self.assertRaisesRegex(RuntimeError, "DEEPSEEK_API_KEY"):
                _build_model()

    def test_answer_benchmark_uses_the_shared_model_settings(self) -> None:
        self.assertIs(run_answer_benchmark.settings, configured_settings)

    def test_empty_model_names_fail_at_startup(self) -> None:
        with self.assertRaises(ValidationError):
            Settings(_env_file=None, judge_model_name=" ")

    def test_summary_trigger_cannot_exceed_conversation_budget(self) -> None:
        with self.assertRaises(ValidationError):
            Settings(
                _env_file=None,
                conversation_token_budget=100,
                conversation_summary_trigger_tokens=101,
            )


class JudgeRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_hallucination_check_uses_the_independent_judge_model(self) -> None:
        fake_model = AsyncMock()
        fake_model.ainvoke.return_value = AIMessage(
            content='{"hallucination_pass": true}'
        )
        with patch("backend.agent.nodes._build_model", return_value=fake_model) as builder:
            await check_hallucination(
                {
                    "route": "rag",
                    "query": "When?",
                    "answer": "Effective today [C1].",
                    "retrieved_docs": [
                        Document(page_content="Effective today.", metadata={})
                    ],
                }
            )

        builder.assert_called_once_with(
            temperature=0,
            model_name=configured_settings.judge_model_name,
        )


if __name__ == "__main__":
    unittest.main()
