"""Run the offline enterprise RAG benchmark and regression gate.

This command intentionally uses hashing embeddings and a deterministic overlap
reranker. It verifies corpus/evaluation integrity and retrieval behavior in CI
without requiring Chroma, an API key, or a hosted model. Hosted answer
benchmarks remain a separate, explicitly optional layer.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from backend.agent.memory import _LocalVectorStore, _build_documents
from backend.config import settings
from backend.evaluation.retrieval import run_retrieval_ablation
from backend.evaluation.schema import (
    EvalCase,
    parse_eval_cases,
    validate_cases_against_corpus,
    validate_dataset_shape,
)
from backend.knowledge.schema import validate_knowledge_records
from backend.knowledge.embeddings import EmbeddingProfile, HashingEmbeddings
from backend.retrieval.engine import RetrievalConfig, RetrievalEngine
from backend.retrieval.reranker import DeterministicReranker


KB_PATH = BASE_DIR / "data" / "knowledge_base.json"
EVAL_PATH = BASE_DIR / "data" / "evals" / "enterprise_rag_eval.json"
THRESHOLD_PATH = BASE_DIR / "data" / "evals" / "enterprise_regression_thresholds.json"
REPORT_PATH = BASE_DIR / "data" / "eval_reports" / "enterprise_rag_benchmark.json"


def _report_path(split: str | None) -> Path:
    if split is None:
        return REPORT_PATH
    return REPORT_PATH.with_name(f"enterprise_rag_benchmark_{split}.json")


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _identity() -> dict[str, Any]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=BASE_DIR,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=BASE_DIR,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        commit = "unknown"
        status = "unknown"
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_commit": commit,
        "git_dirty": bool(status),
        "python_version": sys.version.split()[0],
        "package_versions": _package_versions(
            ["langchain", "langchain-openai", "pydantic", "filelock"]
        ),
        "evaluation_mode": "offline_deterministic_retrieval",
        "hosted_answer_evaluation": "not_run",
    }


def _package_versions(packages: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for package in packages:
        try:
            result[package] = version(package)
        except PackageNotFoundError:
            result[package] = "not-installed"
    return result


def _corpus_identity(documents: list[Any]) -> str:
    payload = [
        {
            "source_id": document.metadata.get("source_id"),
            "content_checksum": document.metadata.get("content_checksum"),
            "chunk_id": document.metadata.get("chunk_id"),
        }
        for document in documents
    ]
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _build_offline_engine(documents: list[Any]) -> tuple[RetrievalEngine, dict[str, Any]]:
    profile = EmbeddingProfile(
        provider="hashing",
        model="offline-hashing",
        dimensions=256,
        index_version=(
            f"offline-v1-chunk{settings.knowledge_chunk_size}-"
            f"overlap{settings.knowledge_chunk_overlap}"
        ),
    )
    store = _LocalVectorStore(HashingEmbeddings(profile.dimensions), documents)
    config = RetrievalConfig(
        dense_candidate_k=30,
        lexical_candidate_k=30,
        rerank_candidate_k=20,
        rrf_k=60,
        dense_weight=0.5,
        lexical_weight=0.5,
        production_strategy="rerank",
    )
    engine = RetrievalEngine(
        store,
        documents,
        config=config,
        reranker=DeterministicReranker(),
    )
    return engine, {
        "embedding_provider": profile.provider,
        "embedding_model": profile.model,
        "embedding_dimensions": profile.dimensions,
        "embedding_index_version": profile.index_version,
        "chunk_size": settings.knowledge_chunk_size,
        "chunk_overlap": settings.knowledge_chunk_overlap,
        "retrieval_config": {
            "dense_candidate_k": config.dense_candidate_k,
            "lexical_candidate_k": config.lexical_candidate_k,
            "rerank_candidate_k": config.rerank_candidate_k,
            "rrf_k": config.rrf_k,
            "dense_weight": config.dense_weight,
            "lexical_weight": config.lexical_weight,
        },
        "reranker": "deterministic_token_overlap",
    }


def _score_for_abstention(engine: RetrievalEngine, case: EvalCase, top_k: int) -> float:
    result = engine.retrieve(case.question, top_k=top_k, strategy="rerank")
    if not result.documents:
        return 0.0
    score = result.documents[0].metadata.get("rerank_score")
    if isinstance(score, (int, float)) and not isinstance(score, bool):
        return float(score)
    return 0.0


def _evidence_support_score(engine: RetrievalEngine, case: EvalCase, top_k: int) -> float:
    """Measure exact gold-phrase coverage for offline refusal calibration.

    This is intentionally a gold-aware evaluation metric, not a production
    runtime signal.  It lets the regression gate test the refusal labels while
    keeping hosted answer generation responsible for the real user decision.
    """

    result = engine.retrieve(case.question, top_k=top_k, strategy="rerank")
    combined = "\n".join(document.page_content for document in result.documents)
    if not case.evidence_phrases:
        return 0.0
    return sum(phrase in combined for phrase in case.evidence_phrases) / len(
        case.evidence_phrases
    )


def _calibrate_abstention_threshold(
    engine: RetrievalEngine,
    cases: list[EvalCase],
    *,
    top_k: int,
) -> float:
    development = [case for case in cases if case.split == "development"]
    scored = [
        (case.answerable, _evidence_support_score(engine, case, top_k))
        for case in development
    ]
    # Exact gold evidence is available only inside evaluation.  A score of
    # zero is the deterministic "no supporting evidence" condition; any
    # positive coverage is answerable.  The threshold is still learned from
    # development so the report records the calibration decision.
    candidates = sorted({0.0, 0.5, 1.0, *(score for _, score in scored)})
    best = (0.5, 0.0)
    for threshold in candidates:
        accuracy = sum(
            (score <= threshold) == (not answerable)
            for answerable, score in scored
        ) / len(scored) if scored else 0.0
        if accuracy > best[1] or (accuracy == best[1] and threshold < best[0]):
            best = (threshold, accuracy)
    return best[0]


def _abstention_report(
    engine: RetrievalEngine,
    cases: list[EvalCase],
    *,
    threshold: float,
    top_k: int,
) -> dict[str, Any]:
    by_split: dict[str, dict[str, Any]] = {}
    for split in ("development", "regression", "held_out"):
        selected = [case for case in cases if case.split == split]
        rows: list[dict[str, Any]] = []
        for case in selected:
            score = _evidence_support_score(engine, case, top_k)
            abstained = score <= threshold
            correct = abstained == (not case.answerable)
            rows.append(
                {
                    "id": case.id,
                    "answerable": case.answerable,
                    "score": score,
                    "abstained": abstained,
                    "correct": correct,
                    "failure_type": "none" if correct else "refusal_failure",
                }
            )
        by_split[split] = {
            "case_count": len(rows),
            "accuracy": (
                sum(row["correct"] for row in rows) / len(rows) if rows else 0.0
            ),
            "no_answer_evidence_proxy_accuracy": (
                sum(row["correct"] for row in rows if not row["answerable"])
                / sum(not row["answerable"] for row in rows)
                if any(not row["answerable"] for row in rows)
                else None
            ),
            "failure_count": sum(not row["correct"] for row in rows),
            "results": rows,
        }
    return {
        "threshold": threshold,
        "metric_type": "gold_evidence_support_proxy",
        "not_generation_refusal_evaluation": True,
        "by_split": by_split,
    }


def _check_gate(
    report: dict[str, Any],
    thresholds: dict[str, Any],
) -> dict[str, Any]:
    regression = report["by_split"]["regression"]["strategies"]["rerank"]
    abstention = report["abstention"]["by_split"]["regression"]
    checks = {
        "source_hit_at_k": regression["source_hit_at_k"]
        >= float(thresholds["source_hit_at_k_min"]),
        "evidence_recall_at_k": regression["evidence_recall_at_k"]
        >= float(thresholds["evidence_recall_at_k_min"]),
        "no_answer_evidence_proxy_accuracy": (
            abstention["no_answer_evidence_proxy_accuracy"] is not None
            and abstention["no_answer_evidence_proxy_accuracy"]
            >= float(thresholds["no_answer_evidence_proxy_accuracy_min"])
        ),
        "retrieval_failure_count": regression["retrieval_failure_count"]
        <= int(thresholds["retrieval_failure_count_max"]),
        "data_validation": report["validation"]["passed"],
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "thresholds": thresholds,
        "observed": {
            "source_hit_at_k": regression["source_hit_at_k"],
            "evidence_recall_at_k": regression["evidence_recall_at_k"],
            "no_answer_evidence_proxy_accuracy": abstention[
                "no_answer_evidence_proxy_accuracy"
            ],
            "retrieval_failure_count": regression["retrieval_failure_count"],
        },
    }


def run_benchmark(
    *,
    top_k: int = 5,
    split: str | None = None,
) -> dict[str, Any]:
    raw_records = _load_json(KB_PATH)
    raw_cases = _load_json(EVAL_PATH)
    records_errors = validate_knowledge_records(raw_records)
    cases = parse_eval_cases(raw_cases, require_structured=True)
    if split is not None and split not in {"development", "regression", "held_out"}:
        raise ValueError(f"Unknown split: {split}")
    selected_cases = [case for case in cases if split is None or case.split == split]
    shape_errors = validate_dataset_shape(cases, minimum_cases=150)
    documents = _build_documents()
    gold_errors: list[str] = []
    try:
        validate_cases_against_corpus(cases, documents)
    except ValueError as exc:
        gold_errors = str(exc).splitlines()[1:]
    validation = {
        "passed": not records_errors and not shape_errors and not gold_errors,
        "knowledge_record_errors": records_errors,
        "dataset_shape_errors": shape_errors,
        "gold_validation_errors": gold_errors,
    }
    if not validation["passed"]:
        raise ValueError(json.dumps(validation, ensure_ascii=False, indent=2))

    engine, engine_metadata = _build_offline_engine(documents)
    strategies = ["dense", "lexical", "fusion", "rerank"]
    by_split: dict[str, Any] = {}
    selected_splits = (
        (split,) if split is not None else ("development", "regression", "held_out")
    )
    for split_name in selected_splits:
        split_cases = [case for case in selected_cases if case.split == split_name]
        by_split[split_name] = {
            "case_count": len(split_cases),
            "strategies": run_retrieval_ablation(
                engine,
                split_cases,
                strategies=strategies,  # type: ignore[arg-type]
                top_k=top_k,
            ),
        }
    threshold = _calibrate_abstention_threshold(engine, selected_cases, top_k=top_k)
    report: dict[str, Any] = {
        **_identity(),
        "eval_file": str(EVAL_PATH.relative_to(BASE_DIR)),
        "knowledge_file": str(KB_PATH.relative_to(BASE_DIR)),
        "top_k": top_k,
        "selected_split": split,
        "corpus_identity": _corpus_identity(documents),
        "document_count": len(raw_records),
        "chunk_count": len(documents),
        "answerable_count": sum(case.answerable for case in selected_cases),
        "no_answer_count": sum(not case.answerable for case in selected_cases),
        "split_counts": dict(Counter(case.split for case in selected_cases)),
        "domain_counts": dict(Counter(case.domain for case in selected_cases)),
        "validation": validation,
        **engine_metadata,
        "by_split": by_split,
        "abstention": _abstention_report(
            engine,
            selected_cases,
            threshold=threshold,
            top_k=top_k,
        ),
    }
    thresholds = _load_json(THRESHOLD_PATH)
    report["regression_gate"] = (
        _check_gate(report, thresholds)
        if "regression" in by_split
        else {"passed": None, "status": "not_run_for_non_regression_split"}
    )
    report_path = _report_path(split)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument(
        "--split",
        choices=("development", "regression", "held_out"),
        help="Run one split independently; omit to run all splits.",
    )
    args = parser.parse_args()
    report = run_benchmark(top_k=args.top_k, split=args.split)
    print(
        json.dumps(
            {
                "document_count": report["document_count"],
                "chunk_count": report["chunk_count"],
                "case_count": sum(report["split_counts"].values()),
                "regression_gate": report["regression_gate"],
                "abstention_threshold": report["abstention"]["threshold"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    print(f"Report written to: {_report_path(args.split)}")
    if report["regression_gate"]["passed"] is False:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
