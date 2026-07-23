"""Tests for the optional automatic semantic answer judge."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from langchain_core.messages import AIMessage

from backend.evaluation.judge import evaluate_with_judge


class AutomaticJudgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_parses_strict_boolean_judgment(self) -> None:
        model = AsyncMock()
        model.ainvoke.return_value = AIMessage(
            content=(
                '{"answer_correct":true,"grounded":true,'
                '"citation_support":true,"abstention_correct":true,'
                '"reason":"all facts supported"}'
            )
        )
        with patch("backend.evaluation.judge._build_model", return_value=model) as builder:
            result = await evaluate_with_judge(
                question="When does it apply?",
                answer="It applies on 2025-01-01 [C1].",
                answerable=True,
                expected_facts=["2025-01-01"],
                citations=[
                    {
                        "citation_id": "C1",
                        "quote": "This policy applies on 2025-01-01.",
                    }
                ],
            )

        self.assertTrue(result.available)
        self.assertTrue(result.answer_correct)
        self.assertTrue(result.overall_pass)
        builder.assert_called_once()

    async def test_malformed_judge_output_is_unavailable_not_passing(self) -> None:
        model = AsyncMock()
        model.ainvoke.return_value = AIMessage(content="looks good")
        with patch("backend.evaluation.judge._build_model", return_value=model):
            result = await evaluate_with_judge(
                question="Question",
                answer="Answer",
                answerable=True,
                expected_facts=["fact"],
                citations=[],
            )

        self.assertFalse(result.available)
        self.assertFalse(result.overall_pass)
        self.assertIn("JSON", result.error)

    async def test_json_wrapped_in_explanation_is_rejected(self) -> None:
        model = AsyncMock()
        model.ainvoke.return_value = AIMessage(
            content=(
                'Result: {"answer_correct":true,"grounded":true,'
                '"citation_support":true,"abstention_correct":true,'
                '"reason":"looks fine"}'
            )
        )
        with patch("backend.evaluation.judge._build_model", return_value=model):
            result = await evaluate_with_judge(
                question="Question",
                answer="Answer",
                answerable=True,
                expected_facts=["fact"],
                citations=[],
            )

        self.assertFalse(result.available)
        self.assertFalse(result.overall_pass)
        self.assertIn("JSONDecodeError", result.error)


if __name__ == "__main__":
    unittest.main()
