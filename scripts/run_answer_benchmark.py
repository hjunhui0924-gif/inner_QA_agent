"""Evaluate retrieval plus one generation pass on the official development set."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from langchain_core.messages import HumanMessage

from backend.agent.nodes import generate
from backend.evaluation.answers import AnswerEvalCase, evaluate_answer
from scripts.run_retrieval_benchmark import (
    EVAL_PATH,
    PROVENANCE_PATH,
    _build_documents,
    _build_engine,
    _load_json,
    _run_identity,
)


REPORT_PATH = BASE_DIR / "data" / "eval_reports" / "official_policy_answer_benchmark.json"


async def run_answer_benchmark(*, top_k: int = 5, limit: int | None = None) -> dict[str, Any]:
    """Generate and score answers for the authoritative policy dataset."""

    provenance = _load_json(PROVENANCE_PATH)
    documents = _build_documents(provenance)
    engine, retrieval_metadata = _build_engine(
        documents,
        provenance,
        enable_rerank=True,
    )
    raw_cases = _load_json(EVAL_PATH)
    if limit is not None:
        raw_cases = raw_cases[:limit]
    results: list[dict[str, Any]] = []

    for raw_case in raw_cases:
        question = str(raw_case["question"])
        source_ids = {str(item) for item in raw_case.get("source_ids", [])}
        expected_facts = [str(item) for item in raw_case.get("evidence_phrases", [])]
        answerable = bool(source_ids)
        started = time.perf_counter()
        retrieval = await asyncio.to_thread(engine.retrieve, question, top_k)
        retrieved = retrieval.documents
        retrieved_source_ids = {
            str(document.metadata.get("source_id", "")) for document in retrieved
        }
        retrieval_hit = not answerable or bool(source_ids & retrieved_source_ids)
        combined_evidence = "\n".join(document.page_content for document in retrieved)
        ranking_hit = not answerable or all(
            phrase in combined_evidence for phrase in expected_facts
        )
        generation: dict[str, Any] = {}
        generation_attempts = 0
        for attempt in range(3):
            generation_attempts = attempt + 1
            generation = await generate(
                {
                    "query": question,
                    "messages": [HumanMessage(content=question)],
                    "retrieved_docs": retrieved,
                    "tool_output": "",
                    "route": "rag",
                }
            )
            if not generation.get("generation_error"):
                break
            await asyncio.sleep(2**attempt)
        answer = str(generation.get("answer", "")).strip()
        generation_error = str(generation.get("generation_error", "")).strip()
        citations = generation.get("citations", [])
        if not isinstance(citations, list):
            citations = []
        metrics = evaluate_answer(
            AnswerEvalCase(
                answer=answer,
                expected_facts=expected_facts,
                answerable=answerable,
                retrieval_hit=retrieval_hit,
                ranking_hit=ranking_hit,
                generation_succeeded=not generation_error,
            ),
            citations,
            retrieved,
        )
        results.append(
            {
                "id": str(raw_case.get("id", "")),
                "category": str(raw_case.get("category", "")),
                "question": question,
                "answerable": answerable,
                "expected_facts": expected_facts,
                "retrieved_chunk_ids": [
                    str(document.metadata.get("chunk_id", "")) for document in retrieved
                ],
                "retrieval_hit": retrieval_hit,
                "ranking_hit": ranking_hit,
                "answer": answer,
                "generation_error": generation_error,
                "generation_attempts": generation_attempts,
                "citations": citations,
                "metrics": asdict(metrics),
                "latency_ms": (time.perf_counter() - started) * 1000,
            }
        )

    metric_names = (
        "gold_phrase_match_rate",
        "numeric_match_rate",
        "citation_provenance_accuracy",
        "citation_completeness",
        "extractive_overlap_rate",
        "abstention_accuracy",
    )
    answerable_results = [result for result in results if result["answerable"]]
    no_answer_results = [result for result in results if not result["answerable"]]
    summary: dict[str, float] = {}
    for name in metric_names:
        population = results if name == "abstention_accuracy" else answerable_results
        summary[name] = (
            sum(float(result["metrics"][name]) for result in population) / len(population)
            if population
            else 0.0
        )
    failure_counts = Counter(result["metrics"]["failure_type"] for result in results)
    report = {
        **_run_identity(),
        "eval_file": str(EVAL_PATH.relative_to(BASE_DIR)),
        "provenance_file": str(PROVENANCE_PATH.relative_to(BASE_DIR)),
        "top_k": top_k,
        "dataset_role": "development_set",
        "pipeline_scope": "retrieval_plus_single_generation",
        "case_count": len(results),
        "answerable_count": len(answerable_results),
        "no_answer_count": len(no_answer_results),
        **retrieval_metadata,
        "summary": summary,
        "metric_scope": {
            "abstention_accuracy": "all_cases",
            "other_summary_metrics": "answerable_cases_only",
        },
        "metric_limitations": (
            "Quote provenance proves source location only. Extractive overlap is "
            "not a human or NLI entailment judgment."
        ),
        "no_answer_accuracy": (
            sum(item["metrics"]["abstention_accuracy"] for item in no_answer_results)
            / len(no_answer_results)
            if no_answer_results
            else None
        ),
        "failure_counts": dict(sorted(failure_counts.items())),
        "results": results,
    }
    report["publishable"] = (
        limit is None
        and len(results) == 34
        and all(not result["generation_error"] for result in results)
    )
    if report["publishable"]:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    report = asyncio.run(run_answer_benchmark(top_k=args.top_k, limit=args.limit))
    print(json.dumps({
        "case_count": report["case_count"],
        "summary": report["summary"],
        "no_answer_accuracy": report["no_answer_accuracy"],
        "failure_counts": report["failure_counts"],
    }, ensure_ascii=False, indent=2))
    if report["publishable"]:
        print(f"Report written to: {REPORT_PATH}")
    else:
        print("Report not written: run was partial or contained generation errors.")


if __name__ == "__main__":
    main()
