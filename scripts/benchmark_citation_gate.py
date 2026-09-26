"""Compare old/new online citation gates on synthetic, labeled candidates."""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from langchain_core.documents import Document
from langchain_core.messages import SystemMessage
from backend.agent import nodes
from backend.agent.nodes import (
    check_hallucination,
    _build_model,
    _as_text,
    _extract_json_object,
)
from backend.config import settings


def valid_verdict(response):
    try:
        return isinstance(
            _extract_json_object(_as_text(response)).get("hallucination_pass"), bool
        )
    except (ValueError, TypeError):
        return False


class ObservedJudge:
    """Keep infrastructure/schema failure separate from semantic rejection."""

    def __init__(self, model, validity):
        self.model, self.validity = model, validity

    async def ainvoke(self, messages):
        try:
            response = await self.model.ainvoke(messages)
        except Exception:
            self.validity.append(False)
            raise
        self.validity.append(valid_verdict(response))
        return response


async def run(args):
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    if not cases or {case.get("expected") for case in cases} != {True, False}:
        raise SystemExit(
            "Cases must include both supported and unsupported labeled candidates."
        )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    try:
        probe = await _build_model(
            temperature=0, model_name=settings.judge_model_name
        ).ainvoke(
            [
                SystemMessage(
                    content='Return JSON: {"hallucination_pass":true,"reason":"availability probe"}'
                )
            ]
        )
        if not valid_verdict(probe):
            raise ValueError("Invalid Judge response schema")
    except Exception as error:
        body = getattr(error, "body", {})
        detail = body.get("error", body) if isinstance(body, dict) else {}
        code = detail.get("code") if isinstance(detail, dict) else None
        blocked = {
            "status": "blocked",
            "http_status": getattr(error, "status_code", None),
            "error_class": type(error).__name__,
            "code": code,
        }
        (args.output_dir / "status.json").write_text(
            json.dumps(blocked, indent=2), encoding="utf-8"
        )
        raise SystemExit(
            "Judge unavailable; see status.json. No quality comparison performed."
        )
    source = subprocess.run(
        ["git", "show", f"{args.baseline_ref}:backend/agent/nodes.py"],
        cwd=ROOT,
        capture_output=True,
        check=True,
    ).stdout
    baseline_path = args.output_dir / "baseline_nodes.py"
    baseline_path.write_bytes(source)
    spec = importlib.util.spec_from_file_location("baseline_gate_nodes", baseline_path)
    baseline = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(baseline)
    rows = []
    for repeat in range(args.repeats):
        for case in cases:
            variants = [
                ("before", baseline.check_hallucination),
                ("after", check_hallucination),
            ]
            if repeat % 2:
                variants.reverse()
            for variant, check in variants:
                settings.citation_validation_mode = (
                    "judge" if variant == "after" else "legacy"
                )
                started = time.perf_counter()
                validity = []

                def observed_builder(**kwargs):
                    return ObservedJudge(_build_model(**kwargs), validity)

                with patch.object(
                    nodes, "_build_model", observed_builder
                ), patch.object(baseline, "_build_model", observed_builder):
                    result = await check(
                        {
                            "route": "rag",
                            "query": case["question"],
                            "candidate_answer": case["answer"],
                            "retrieved_docs": [
                                Document(
                                    page_content=text,
                                    metadata={
                                        "source_id": f"doc-{i}",
                                        "chunk_id": f"doc-{i}:0",
                                    },
                                )
                                for i, text in enumerate(case["documents"])
                            ],
                        }
                    )
                rows.append(
                    {
                        "case_id": case["case_id"],
                        "repeat": repeat,
                        "variant": variant,
                        "expected": case["expected"],
                        "passed": result["hallucination_pass"],
                        "failure_stage": result.get("failure_stage"),
                        "reason": result.get("hallucination_reason"),
                        "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
                        "model_calls": result.get("model_call_count"),
                    }
                )
                (args.output_dir / "results.json").write_text(
                    json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                if False in validity or (
                    result.get("model_call_count", 0) > 0 and not validity
                ):
                    (args.output_dir / "status.json").write_text(
                        '{"status":"blocked","reason":"judge_unavailable_during_comparison"}',
                        encoding="utf-8",
                    )
                    raise SystemExit(
                        "Judge became unavailable; incomplete comparison is not acceptance evidence."
                    )
        print(f"Completed repeat {repeat+1}/{args.repeats}", flush=True)
    for variant in ["before", "after"]:
        group = [r for r in rows if r["variant"] == variant]
        print(
            variant,
            "n=",
            len(group),
            "false_accept=",
            sum(r["passed"] and not r["expected"] for r in group),
            "false_reject=",
            sum(not r["passed"] and r["expected"] for r in group),
        )
    after = [r for r in rows if r["variant"] == "after"]
    before = [r for r in rows if r["variant"] == "before"]

    def errors(group, expected):
        return sum(r["expected"] == expected and r["passed"] != expected for r in group)

    acceptable = errors(after, False) == 0 and errors(after, True) == 0
    (args.output_dir / "status.json").write_text(
        json.dumps(
            {
                "status": "completed",
                "acceptance_pass": acceptable,
                "note": "Synthetic development candidates, not independent production quality proof.",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    if not acceptable:
        raise SystemExit("Quality gate did not pass; keep the legacy default.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        help="Authorize paid Judge calls on synthetic fixtures",
    )
    parser.add_argument("--baseline-ref", default="6e4f00a")
    parser.add_argument(
        "--cases", type=Path, default=ROOT / "tests/fixtures/citation_gate_cases.json"
    )
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument(
        "--output-dir", type=Path, default=ROOT / "work/citation-gate-comparison"
    )
    args = parser.parse_args()
    if not args.live:
        parser.error("--live is required")
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    asyncio.run(run(args))
