"""Run the unified RAG evaluation suite.

The runner presents one command and one report shape while keeping each
dataset on its own corpus engine.  Retrieval is the default mode because it
is cheap enough for regular regression runs; answer generation and the LLM
judge are opt-in via ``--mode all`` and ``--enable-judge``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
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
from langchain_core.documents import Document

from backend.agent.memory import (
    _LocalVectorStore,
    _build_documents as build_enterprise_documents,
    ensure_vectorstore,
    get_retriever,
)
from backend.agent.nodes import generate
from backend.config import settings
from backend.evaluation.answers import AnswerEvalCase, evaluate_answer
from backend.evaluation.judge import AutomaticJudgeResult, evaluate_with_judge
from backend.evaluation.runtime import extract_generation_output
from backend.evaluation.unified import (
    DEFAULT_MANIFEST_PATH,
    UnifiedDatasetSpec,
    UnifiedEvalCase,
    UnifiedRetrievalCaseResult,
    aggregate_unified_retrieval,
    evaluate_unified_retrieval_case,
    load_unified_suite,
)
from backend.knowledge.embeddings import EmbeddingProfile, HashingEmbeddings, create_embeddings
from backend.retrieval.engine import RetrievalConfig, RetrievalEngine
from backend.retrieval.reranker import DashScopeReranker, DeterministicReranker
from scripts.run_enterprise_rag_benchmark import _build_offline_engine
from scripts.run_retrieval_benchmark import (
    PROVENANCE_PATH,
    _build_documents as build_official_documents,
    _build_engine as build_official_engine,
    _load_json,
    _run_identity,
)


REPORT_PATH = BASE_DIR / "data" / "eval_reports" / "unified_rag_benchmark.json"


async def run_unified_benchmark(
    *,
    top_k: int = 4,
    mode: str = "retrieval",
    enable_judge: bool = False,
    offline: bool = False,
    limit: int | None = None,
    dataset_ids: set[str] | None = None,
    manifest_path: str | Path | None = None,
    report_path: str | Path = REPORT_PATH,
) -> dict[str, Any]:
    """Run the selected unified datasets and write one structured report."""

    if top_k <= 0:
        raise ValueError("top_k must be positive.")
    if mode not in {"retrieval", "answer", "all"}:
        raise ValueError("mode must be retrieval, answer, or all.")
    if enable_judge and mode == "retrieval":
        raise ValueError("LLM judge requires answer or all mode.")
    if limit is not None and limit <= 0:
        raise ValueError("limit must be positive when provided.")

    suite = load_unified_suite(
        manifest_path or DEFAULT_MANIFEST_PATH,
        dataset_ids=dataset_ids,
    )
    dataset_reports: dict[str, dict[str, Any]] = {}
    all_retrieval_rows: list[UnifiedRetrievalCaseResult] = []
    selected_total = 0
    for spec in suite.specs:
        cases = suite.cases_for(spec.id)
        if limit is not None:
            cases = cases[:limit]
        selected_total += len(cases)
        engine, engine_metadata = await _build_engine_for_dataset(
            spec,
            cases,
            offline=offline,
        )
        retrieval_rows: list[UnifiedRetrievalCaseResult] = []
        answer_rows: list[dict[str, Any]] = []
        for case in cases:
            retrieval = await asyncio.to_thread(
                engine.retrieve,
                case.question,
                top_k,
            )
            retrieval_row = evaluate_unified_retrieval_case(case, retrieval)
            retrieval_rows.append(retrieval_row)
            if mode in {"answer", "all"}:
                answer_rows.append(
                    await _evaluate_answer_case(
                        case,
                        retrieval_row,
                        retrieval.documents,
                        enable_judge=enable_judge,
                    )
                )
        retrieval_summary = _group_retrieval_rows(retrieval_rows)
        all_retrieval_rows.extend(retrieval_rows)
        if spec.id == "enterprise_rag":
            threshold = _calibrate_abstention_threshold(retrieval_rows)
            retrieval_summary["abstention"] = _build_abstention_report(
                retrieval_rows,
                threshold=threshold,
            )
        answer_summary = (
            _aggregate_answer_rows(answer_rows) if mode in {"answer", "all"} else None
        )
        dataset_reports[spec.id] = {
            "dataset_id": spec.id,
            "role": spec.role,
            "corpus_id": spec.corpus_id,
            "case_count": len(cases),
            "engine": engine_metadata,
            "retrieval": retrieval_summary,
            "answer": answer_summary,
            "gate": _evaluate_dataset_gate(spec, retrieval_summary),
        }

    gates = [item["gate"] for item in dataset_reports.values()]
    blocking_gates = [gate for gate in gates if gate.get("blocking")]
    overall_gate = {
        "passed": all(gate.get("passed") is True for gate in blocking_gates)
        if blocking_gates
        else None,
        "blocking_dataset_ids": [
            dataset_id
            for dataset_id, item in dataset_reports.items()
            if item["gate"].get("blocking")
        ],
        "failed_blocking_dataset_ids": [
            dataset_id
            for dataset_id, item in dataset_reports.items()
            if item["gate"].get("blocking")
            and item["gate"].get("passed") is False
        ],
        "legacy_is_non_blocking": True,
        "status": "quality_gate" if not offline and limit is None else "smoke_only",
    }
    if offline or limit is not None:
        overall_gate["passed"] = None
        overall_gate["reason"] = (
            "Offline or limited runs are smoke tests and do not publish a quality gate."
        )
    report = {
        **_run_identity(),
        "suite_id": "unified_rag",
        "manifest_path": str(suite.manifest_path.relative_to(BASE_DIR)),
        "mode": mode,
        "top_k": top_k,
        "offline": offline,
        "generation_model": settings.model_name if mode in {"answer", "all"} else None,
        "judge_model": settings.judge_model_name if enable_judge else None,
        "case_count": selected_total,
        "suite_summary": suite.summary(),
        "dataset_reports": dataset_reports,
        "overall_retrieval": _overall_retrieval_summary(
            all_retrieval_rows,
            dataset_reports,
        ),
        "independent_gates": {
            dataset_id: item["gate"] for dataset_id, item in dataset_reports.items()
        },
        "overall_gate": overall_gate,
        "corpus_isolation": {
            dataset_id: {
                "corpus_id": item["corpus_id"],
                "engine_document_count": item["engine"].get("document_count"),
                "isolated": True,
            }
            for dataset_id, item in dataset_reports.items()
        },
    }
    target = Path(report_path)
    if not target.is_absolute():
        target = BASE_DIR / target
    target.parent.mkdir(parents=True, exist_ok=True)
    report["report_path"] = str(target)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


async def _build_engine_for_dataset(
    spec: UnifiedDatasetSpec,
    cases: list[UnifiedEvalCase],
    *,
    offline: bool,
) -> tuple[RetrievalEngine, dict[str, Any]]:
    if spec.id == "official_policy":
        provenance = _load_json(PROVENANCE_PATH)
        documents = build_official_documents(provenance)
        engine, metadata = build_official_engine(
            documents,
            provenance,
            enable_rerank=offline or bool(
                settings.reranker_enabled and settings.dashscope_api_key
            ),
            offline=offline,
        )
        if offline:
            metadata = {
                **metadata,
                "reranker_model": "deterministic",
                "reranker_endpoint": None,
                "reranker_api_style": None,
                "reranker_timeout_seconds": None,
            }
        _validate_cases_against_documents(spec, cases, documents)
        return engine, {
            **metadata,
            "corpus_id": spec.corpus_id,
            "document_count": len(documents),
        }
    if spec.id == "enterprise_rag":
        if offline:
            documents = build_enterprise_documents()
            engine, metadata = _build_offline_engine(documents)
        else:
            await ensure_vectorstore()
            engine = get_retriever()
            documents = list(engine._documents)
            metadata = _engine_metadata(engine, documents)
        _validate_cases_against_documents(spec, cases, documents)
        return engine, {
            **metadata,
            "corpus_id": spec.corpus_id,
            "document_count": len(documents),
        }
    if spec.id == "dongshan_legacy":
        return _build_legacy_engine(spec, cases, offline=offline)
    raise ValueError(f"No corpus builder registered for unified dataset {spec.id}.")


def _build_legacy_engine(
    spec: UnifiedDatasetSpec,
    cases: list[UnifiedEvalCase],
    *,
    offline: bool,
) -> tuple[RetrievalEngine, dict[str, Any]]:
    all_documents = build_enterprise_documents()
    titles = {case.source_title for case in cases if case.source_title}
    documents = [
        document
        for document in all_documents
        if str(document.metadata.get("title", "")).strip() in titles
    ]
    if not documents:
        raise ValueError(
            "Legacy dataset source title was not found in the isolated enterprise corpus."
        )
    _validate_cases_against_documents(spec, cases, documents)
    if offline:
        embeddings = HashingEmbeddings(dimensions=256)
        reranker = DeterministicReranker()
        profile = {
            "provider": "hashing",
            "model": "offline-hashing",
            "dimensions": 256,
        }
    else:
        embedding_profile = EmbeddingProfile(
            provider=settings.embedding_provider,  # type: ignore[arg-type]
            model=settings.embedding_model,
            dimensions=settings.embedding_dimensions,
            index_version=(
                f"{settings.embedding_index_version}-"
                f"chunk{settings.knowledge_chunk_size}-"
                f"overlap{settings.knowledge_chunk_overlap}-legacy"
            ),
            api_key=settings.dashscope_api_key,
            base_url=settings.dashscope_base_url,
        )
        embeddings = create_embeddings(embedding_profile)
        reranker = _build_online_reranker()
        profile = {
            "provider": embedding_profile.provider,
            "model": embedding_profile.model,
            "dimensions": embedding_profile.dimensions,
        }
    store = _LocalVectorStore(embeddings, documents)
    engine = RetrievalEngine(
        store,
        documents,
        config=_retrieval_config(),
        reranker=reranker,
    )
    return engine, {
        "corpus_id": spec.corpus_id,
        "document_count": len(documents),
        "embedding_provider": profile["provider"],
        "embedding_model": profile["model"],
        "embedding_dimensions": profile["dimensions"],
        "isolated_source_titles": sorted(titles),
        "reranker_model": settings.reranker_model if not offline else "deterministic",
    }


def _retrieval_config() -> RetrievalConfig:
    return RetrievalConfig(
        dense_candidate_k=settings.retrieval_dense_candidate_k,
        lexical_candidate_k=settings.retrieval_lexical_candidate_k,
        rerank_candidate_k=settings.retrieval_rerank_candidate_k,
        rrf_k=settings.retrieval_rrf_k,
        dense_weight=settings.retrieval_dense_weight,
        lexical_weight=settings.retrieval_lexical_weight,
        production_strategy=settings.retrieval_strategy,  # type: ignore[arg-type]
    )


def _build_online_reranker() -> DashScopeReranker | None:
    if not settings.reranker_enabled or not settings.dashscope_api_key:
        return None
    return DashScopeReranker(
        api_key=settings.dashscope_api_key,
        model=settings.reranker_model,
        endpoint=settings.reranker_endpoint,
        api_style=settings.reranker_api_style,  # type: ignore[arg-type]
        timeout_seconds=settings.reranker_timeout_seconds,
        max_document_chars=settings.reranker_max_document_chars,
    )


def _validate_cases_against_documents(
    spec: UnifiedDatasetSpec,
    cases: list[UnifiedEvalCase],
    documents: list[Document],
) -> None:
    """Validate gold evidence before a unified run can publish metrics."""

    if spec.id != "dongshan_legacy":
        from backend.evaluation.schema import validate_cases_against_corpus

        validate_cases_against_corpus(
            [case.to_eval_case() for case in cases],
            documents,
        )
        return
    titles = {case.source_title for case in cases if case.source_title}
    available_titles = {
        str(document.metadata.get("title", "")).strip() for document in documents
    }
    missing_titles = sorted(titles - available_titles)
    if missing_titles:
        raise ValueError(
            "Legacy evaluation source titles are missing from the isolated corpus: "
            + ", ".join(missing_titles)
        )
    content = "\n".join(document.page_content for document in documents)
    missing_keywords = sorted(
        {
            keyword
            for case in cases
            for keyword in (case.expected_keywords or [])
            if not _contains_normalized_keyword(keyword, content)
        }
    )
    if missing_keywords:
        raise ValueError(
            "Legacy evaluation keywords are missing from the isolated corpus: "
            + ", ".join(missing_keywords)
        )


def _contains_normalized_keyword(keyword: str, content: str) -> bool:
    """Accept legacy numeric keyword variants with or without separators."""

    if keyword in content:
        return True
    normalize = lambda value: re.sub(r"[\s,，]", "", value)  # noqa: E731
    normalized_keyword = normalize(keyword)
    return bool(normalized_keyword) and normalized_keyword in normalize(content)


def _engine_metadata(engine: RetrievalEngine, documents: list[Document]) -> dict[str, Any]:
    config = engine._config
    reranker = engine._reranker
    return {
        "document_count": len(documents),
        "chunk_size": settings.knowledge_chunk_size,
        "chunk_overlap": settings.knowledge_chunk_overlap,
        "embedding_provider": settings.embedding_provider,
        "embedding_model": settings.embedding_model,
        "embedding_dimensions": settings.embedding_dimensions,
        "retrieval_config": {
            "dense_candidate_k": config.dense_candidate_k,
            "lexical_candidate_k": config.lexical_candidate_k,
            "rerank_candidate_k": config.rerank_candidate_k,
            "rrf_k": config.rrf_k,
            "dense_weight": config.dense_weight,
            "lexical_weight": config.lexical_weight,
        },
        "reranker_model": settings.reranker_model if reranker is not None else None,
    }


def _group_retrieval_rows(
    rows: list[UnifiedRetrievalCaseResult],
) -> dict[str, Any]:
    report = aggregate_unified_retrieval(rows)
    report["by_split"] = {
        split: aggregate_unified_retrieval(
            row for row in rows if row.split == split
        )
        for split in sorted({row.split for row in rows})
    }
    report["by_category"] = {
        category: aggregate_unified_retrieval(
            row for row in rows if row.category == category
        )
        for category in sorted({row.category for row in rows})
    }
    report["by_domain"] = {
        domain: aggregate_unified_retrieval(
            row for row in rows if row.domain == domain
        )
        for domain in sorted({row.domain for row in rows})
    }
    return report


def _overall_retrieval_summary(
    rows: list[UnifiedRetrievalCaseResult],
    dataset_reports: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Expose weighted and macro views without hiding per-dataset failures."""

    weighted = aggregate_unified_retrieval(rows)
    metric_names = (
        "source_hit_at_k",
        "mrr_at_k",
        "ndcg_at_k",
        "evidence_recall_at_k",
        "full_evidence_hit_rate",
    )
    snapshots = [
        item["retrieval"]
        for item in dataset_reports.values()
        if item.get("retrieval", {}).get("answerable_case_count", 0) > 0
    ]
    macro = {
        name: (
            sum(float(snapshot[name]) for snapshot in snapshots) / len(snapshots)
            if snapshots
            else 0.0
        )
        for name in metric_names
    }
    return {
        "case_weighted": {
            key: value
            for key, value in weighted.items()
            if key != "results"
        },
        "dataset_macro_average": macro,
        "metric_scope": {
            "case_weighted": "all selected cases; recall metrics use answerable cases only",
            "dataset_macro_average": "equal weight per selected dataset; legacy remains visible",
        },
    }


async def _evaluate_answer_case(
    case: UnifiedEvalCase,
    retrieval_row: UnifiedRetrievalCaseResult,
    documents: list[Document],
    *,
    enable_judge: bool,
) -> dict[str, Any]:
    generation_started = time.perf_counter()
    generation: dict[str, Any] = {}
    generation_attempts = 0
    generation_model_calls = 0
    generation_tool_calls = 0
    for attempt in range(3):
        generation_attempts = attempt + 1
        generation = await generate(
            {
                "query": case.question,
                "messages": [HumanMessage(content=case.question)],
                "retrieved_docs": documents,
                "tool_output": "",
                "route": "rag",
            }
        )
        generation_model_calls += int(generation.get("model_call_count", 0))
        generation_tool_calls += int(generation.get("tool_call_count", 0))
        if not generation.get("generation_error"):
            break
        if attempt < 2:
            await asyncio.sleep(2**attempt)
    generation_latency_ms = (time.perf_counter() - generation_started) * 1000
    answer, citations = extract_generation_output(generation)
    deterministic = evaluate_answer(
        AnswerEvalCase(
            answer=answer,
            expected_facts=list(case.expected_facts),
            answerable=case.answerable,
            retrieval_hit=(
                True
                if not case.answerable
                else bool(retrieval_row.candidate_source_hit)
            ),
            evidence_hit=(
                True
                if not case.answerable
                else retrieval_row.evidence_recall == 1.0
            ),
            ranking_hit=(
                True
                if not case.answerable
                else bool(retrieval_row.source_hit)
            ),
            generation_succeeded=not bool(generation.get("generation_error")),
            must_cite=case.must_cite,
            expected_refusal_reason=case.expected_refusal_reason,
        ),
        citations,
        documents,
    )
    judge: AutomaticJudgeResult | None = None
    judge_attempts = 0
    if enable_judge:
        for attempt in range(3):
            judge_attempts = attempt + 1
            judge = await evaluate_with_judge(
                question=case.question,
                answer=answer,
                answerable=case.answerable,
                expected_facts=list(case.expected_facts),
                citations=citations,
            )
            if judge.available:
                break
            if attempt < 2:
                await asyncio.sleep(2**attempt)
    legacy_keyword_match = None
    answer_contains_gold = None
    if case.dataset_id == "dongshan_legacy":
        keywords = case.expected_keywords or []
        legacy_keyword_match = (
            sum(_contains_normalized_keyword(keyword, answer) for keyword in keywords)
            / len(keywords)
            if keywords
            else 0.0
        )
        answer_contains_gold = _contains_gold_answer(answer, case.gold_answer)
    return {
        "id": case.id,
        "source_case_id": case.source_case_id,
        "dataset_id": case.dataset_id,
        "question": case.question,
        "answerable": case.answerable,
        "answer": answer,
        "citations": citations,
        "generation_error": str(generation.get("generation_error", "")),
        "generation_attempts": generation_attempts,
        "model_call_count": generation_model_calls,
        "tool_call_count": generation_tool_calls,
        "generation_latency_ms": generation_latency_ms,
        "metrics": asdict(deterministic),
        "legacy_keyword_match_rate": legacy_keyword_match,
        "answer_contains_gold": answer_contains_gold,
        "automatic_judge": asdict(judge) if judge is not None else None,
        "judge_attempts": judge_attempts,
    }


def _aggregate_answer_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    answerable = [row for row in rows if row["answerable"]]
    no_answer = [row for row in rows if not row["answerable"]]
    metric_names = (
        "gold_phrase_match_rate",
        "numeric_match_rate",
        "citation_provenance_accuracy",
        "citation_completeness",
        "extractive_overlap_rate",
        "abstention_accuracy",
    )
    summary: dict[str, Any] = {}
    for name in metric_names:
        population = rows if name == "abstention_accuracy" else answerable
        values = [float(row["metrics"][name]) for row in population]
        summary[name] = sum(values) / len(values) if values else 0.0
    legacy_values = [
        float(row["legacy_keyword_match_rate"])
        for row in rows
        if row["legacy_keyword_match_rate"] is not None
    ]
    summary["legacy_keyword_match_rate"] = (
        sum(legacy_values) / len(legacy_values) if legacy_values else None
    )
    judged = [
        row["automatic_judge"]
        for row in rows
        if isinstance(row.get("automatic_judge"), dict)
        and row["automatic_judge"].get("available") is True
    ]
    summary["automatic_judge_summary"] = {
        "available_count": len(judged),
        **{
            key: (
                sum(bool(item.get(key)) for item in judged) / len(judged)
                if judged
                else None
            )
            for key in (
                "answer_correct",
                "grounded",
                "citation_support",
                "abstention_correct",
                "overall_pass",
            )
        },
    }
    summary["failure_counts"] = dict(
        sorted(
            Counter(
                row["metrics"]["failure_class"] for row in rows
            ).items()
        )
    )
    summary["case_count"] = len(rows)
    summary["answerable_case_count"] = len(answerable)
    summary["no_answer_case_count"] = len(no_answer)
    return {"summary": summary, "results": rows}


def _calibrate_abstention_threshold(
    rows: list[UnifiedRetrievalCaseResult],
) -> float:
    """Calibrate the gold-evidence abstention proxy on development rows."""

    development = [row for row in rows if row.split == "development"]
    scored = [
        (row.answerable, row.evidence_support_score)
        for row in development
    ]
    candidates = sorted({0.0, 0.5, 1.0, *(score for _, score in scored)})
    best_threshold = 0.5
    best_accuracy = -1.0
    for threshold in candidates:
        accuracy = (
            sum((score <= threshold) == (not answerable) for answerable, score in scored)
            / len(scored)
            if scored
            else 0.0
        )
        if accuracy > best_accuracy or (
            accuracy == best_accuracy and threshold < best_threshold
        ):
            best_threshold = threshold
            best_accuracy = accuracy
    return best_threshold


def _build_abstention_report(
    rows: list[UnifiedRetrievalCaseResult],
    *,
    threshold: float,
) -> dict[str, Any]:
    by_split: dict[str, dict[str, Any]] = {}
    for split in sorted({row.split for row in rows}):
        selected = [row for row in rows if row.split == split]
        outcomes = [
            {
                "id": row.id,
                "answerable": row.answerable,
                "score": row.evidence_support_score,
                "abstained": row.evidence_support_score <= threshold,
                "correct": (row.evidence_support_score <= threshold)
                == (not row.answerable),
            }
            for row in selected
        ]
        no_answer = [item for item in outcomes if not item["answerable"]]
        by_split[split] = {
            "case_count": len(outcomes),
            "accuracy": (
                sum(item["correct"] for item in outcomes) / len(outcomes)
                if outcomes
                else 0.0
            ),
            "no_answer_evidence_proxy_accuracy": (
                sum(item["correct"] for item in no_answer) / len(no_answer)
                if no_answer
                else None
            ),
            "failure_count": sum(not item["correct"] for item in outcomes),
            "results": outcomes,
        }
    return {
        "threshold": threshold,
        "metric_type": "gold_evidence_support_proxy",
        "not_generation_refusal_evaluation": True,
        "by_split": by_split,
    }


def _evaluate_dataset_gate(
    spec: UnifiedDatasetSpec,
    retrieval: dict[str, Any],
) -> dict[str, Any]:
    gate = dict(spec.gate)
    blocking = bool(gate.pop("blocking", False))
    if spec.id == "enterprise_rag":
        split_name = str(gate.pop("regression_split", "regression"))
        observed = retrieval.get("by_split", {}).get(split_name)
        if not isinstance(observed, dict):
            return {
                "blocking": blocking,
                "passed": False,
                "status": "missing_regression_split",
            }
        thresholds = _load_thresholds(spec.threshold_file)
        abstention = retrieval.get("abstention", {}).get("by_split", {}).get(
            split_name,
            {},
        )
        checks = {
            "source_hit_at_k": observed["source_hit_at_k"]
                >= float(thresholds["source_hit_at_k_min"]),
            "evidence_recall_at_k": observed["evidence_recall_at_k"]
                >= float(thresholds["evidence_recall_at_k_min"]),
            "no_answer_evidence_proxy_accuracy": (
                abstention.get("no_answer_evidence_proxy_accuracy") is not None
                and abstention["no_answer_evidence_proxy_accuracy"]
                >= float(thresholds["no_answer_evidence_proxy_accuracy_min"])
            ),
            "retrieval_failure_count": observed["retrieval_failure_count"]
                <= int(thresholds["retrieval_failure_count_max"]),
        }
        return {
            "blocking": blocking,
            "passed": all(checks.values()),
            "scope": split_name,
            "checks": checks,
            "thresholds": thresholds,
            "observed": {
                "source_hit_at_k": observed["source_hit_at_k"],
                "evidence_recall_at_k": observed["evidence_recall_at_k"],
                "no_answer_evidence_proxy_accuracy": abstention.get(
                    "no_answer_evidence_proxy_accuracy"
                ),
                "retrieval_failure_count": observed["retrieval_failure_count"],
            },
        }
    checks = {
        "source_hit_at_k": retrieval["source_hit_at_k"]
        >= float(gate.get("source_hit_at_k_min", 0.0)),
        "evidence_recall_at_k": retrieval["evidence_recall_at_k"]
        >= float(gate.get("evidence_recall_at_k_min", 0.0)),
        "retrieval_failure_count": retrieval["retrieval_failure_count"]
        <= int(gate.get("retrieval_failure_count_max", 10**9)),
    }
    return {
        "blocking": blocking,
        "passed": all(checks.values()),
        "checks": checks,
        "thresholds": gate,
        "observed": {
            "source_hit_at_k": retrieval["source_hit_at_k"],
            "evidence_recall_at_k": retrieval["evidence_recall_at_k"],
            "retrieval_failure_count": retrieval["retrieval_failure_count"],
        },
    }


def _load_thresholds(path: Path | None) -> dict[str, Any]:
    if path is None:
        raise ValueError("Enterprise unified dataset requires a threshold_file.")
    return json.loads(path.read_text(encoding="utf-8"))


def _contains_gold_answer(answer: str, gold_answer: str) -> bool:
    normalized_answer = " ".join(answer.split())
    normalized_gold = " ".join(gold_answer.split())
    if normalized_gold and normalized_gold in normalized_answer:
        return True
    return any(
        fragment in normalized_answer
        for fragment in normalized_gold.split("。")
        if fragment
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--top-k", type=int, default=4)
    parser.add_argument(
        "--mode",
        choices=("retrieval", "answer", "all"),
        default="retrieval",
    )
    parser.add_argument("--enable-judge", action="store_true")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument(
        "--limit",
        type=int,
        help="Limit the number of cases per dataset; useful for smoke tests.",
    )
    parser.add_argument(
        "--dataset",
        action="append",
        dest="datasets",
        choices=("official_policy", "enterprise_rag", "dongshan_legacy"),
        help="Select one or more datasets; omit to run all three.",
    )
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--report", type=Path, default=REPORT_PATH)
    args = parser.parse_args()
    report = asyncio.run(
        run_unified_benchmark(
            top_k=args.top_k,
            mode=args.mode,
            enable_judge=args.enable_judge,
            offline=args.offline,
            limit=args.limit,
            dataset_ids=set(args.datasets) if args.datasets else None,
            manifest_path=args.manifest,
            report_path=args.report,
        )
    )
    print(
        json.dumps(
            {
                "suite_id": report["suite_id"],
                "case_count": report["case_count"],
                "mode": report["mode"],
                "dataset_counts": {
                    key: value["case_count"]
                    for key, value in report["dataset_reports"].items()
                },
                "overall_gate": report["overall_gate"],
                "report_path": report["report_path"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if report["overall_gate"]["passed"] is False:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
