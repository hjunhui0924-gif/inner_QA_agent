"""Compare retrieval strategies on any unified evaluation dataset."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from run_unified_rag_benchmark import (
    _build_engine_for_dataset,
    load_unified_suite,
)
from backend.evaluation.retrieval import run_retrieval_ablation


BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
DEFAULT_REPORT = BASE_DIR / "data" / "eval_reports" / "retrieval_ablation.json"
STRATEGIES = ("rerank", "fusion", "dense", "lexical")


async def run_ablation(
    *,
    dataset: str,
    top_k: int = 5,
    split: str | None = None,
    offline: bool = False,
    strategies: list[str] | None = None,
    report_path: Path = DEFAULT_REPORT,
) -> dict[str, Any]:
    suite = load_unified_suite(
        BASE_DIR / "data" / "evals" / "unified_rag_eval_manifest.json",
        dataset_ids={dataset},
    )
    spec = suite.specs[0]
    cases = suite.cases_for(dataset)
    if split is not None:
        cases = [case for case in cases if case.split == split]
    if not cases:
        raise ValueError(f"No cases found for dataset={dataset!r}, split={split!r}.")
    engine, metadata = await _build_engine_for_dataset(spec, cases, offline=offline)
    eval_cases = [case.to_eval_case() for case in cases]
    selected = strategies or list(STRATEGIES)
    unknown = set(selected) - set(STRATEGIES)
    if unknown:
        raise ValueError(f"Unknown strategies: {sorted(unknown)}")
    effective_top_k = max(top_k, 6) if dataset == "dongshan_legacy" else top_k
    report = {
        "dataset_id": dataset,
        "split": split,
        "case_count": len(cases),
        "top_k": effective_top_k,
        "requested_top_k": top_k,
        "offline": offline,
        "engine": metadata,
        "strategies": run_retrieval_ablation(
            engine,
            eval_cases,
            strategies=selected,  # type: ignore[arg-type]
            top_k=effective_top_k,
        ),
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", choices=("official_policy", "enterprise_rag", "dongshan_legacy"), default="enterprise_rag")
    parser.add_argument("--split", choices=("development", "regression", "held_out", "safety", "legacy"))
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--strategies", default=",".join(STRATEGIES))
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    report = asyncio.run(run_ablation(
        dataset=args.dataset,
        top_k=args.top_k,
        split=args.split,
        offline=args.offline,
        strategies=[item.strip() for item in args.strategies.split(",") if item.strip()],
        report_path=args.report,
    ))
    print(json.dumps({
        "dataset_id": report["dataset_id"],
        "split": report["split"],
        "case_count": report["case_count"],
        "top_k": report["top_k"],
        "offline": report["offline"],
        "strategies": {
            name: {key: metrics[key] for key in (
                "source_hit_at_k", "mrr_at_k", "evidence_recall_at_k",
                "full_evidence_hit_rate", "latency_p50_ms", "latency_p95_ms",
                "retrieval_failure_count", "evidence_failure_count",
            )}
            for name, metrics in report["strategies"].items()
        },
    }, ensure_ascii=False, indent=2))
    print(f"Report written to: {args.report}")


if __name__ == "__main__":
    main()
