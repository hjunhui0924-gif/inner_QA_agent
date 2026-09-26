"""An unavailable Judge must not look like a successful quality comparison."""

import argparse
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from langchain_core.messages import AIMessage

from scripts import benchmark_citation_gate


class CitationBenchmarkTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_or_nonboolean_verdict_is_not_a_semantic_rejection(self):
        for content in ["{}", '{"hallucination_pass":"true"}', "not JSON"]:
            model = AsyncMock()
            model.ainvoke.return_value = AIMessage(content=content)
            validity = []
            observed = benchmark_citation_gate.ObservedJudge(model, validity)
            await observed.ainvoke([])
            self.assertEqual(validity, [False])
        self.assertTrue(
            benchmark_citation_gate.valid_verdict(
                AIMessage(content='{"hallucination_pass":false}')
            )
        )

    async def test_quota_denial_blocks_comparison_before_baseline_execution(self):
        class QuotaError(Exception):
            status_code = 403
            body = {"code": "AllocationQuota.FreeTierOnly"}

        model = AsyncMock()
        model.ainvoke.side_effect = QuotaError()
        with tempfile.TemporaryDirectory() as directory:
            args = argparse.Namespace(
                cases=benchmark_citation_gate.ROOT
                / "tests/fixtures/citation_gate_cases.json",
                output_dir=Path(directory),
                baseline_ref="HEAD",
                repeats=3,
            )
            with patch.object(
                benchmark_citation_gate, "_build_model", return_value=model
            ), patch.object(benchmark_citation_gate.subprocess, "run") as git:
                with self.assertRaises(SystemExit):
                    await benchmark_citation_gate.run(args)
            git.assert_not_called()
            status = json.loads((Path(directory) / "status.json").read_text())
            self.assertEqual(status["status"], "blocked")
            self.assertEqual(status["code"], "AllocationQuota.FreeTierOnly")
            self.assertFalse((Path(directory) / "results.json").exists())
