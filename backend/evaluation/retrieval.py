"""Deterministic retrieval metrics, grouping, and failure attribution."""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from dataclasses import asdict, dataclass
from typing import Any, Callable, Iterable

from langchain_core.documents import Document

from backend.evaluation.schema import (
    EvalCase,
    parse_eval_cases,
    validate_cases_against_corpus,
)
from backend.retrieval.engine import RetrievalEngine, RetrievalStrategy


# Compatibility names retained for the original official 34-case benchmark and
# its tests. The structured schema is the canonical implementation now.
RetrievalEvalCase = EvalCase
parse_cases = parse_eval_cases


@dataclass(frozen=True)
class RetrievalCaseResult:
    id: str
    split: str
    domain: str
    difficulty: str
    category: str
    tags: list[str]
    question: str
    gold_source_ids: list[str]
    gold_evidence_phrases: list[str]
    answerable: bool
    source_hit: bool | None
    candidate_source_hit: bool | None
    reciprocal_rank: float | None
    ndcg: float | None
    evidence_recall: float | None
    retrieved_sources: list[str]
    retrieved_chunk_ids: list[str]
    candidate_chunk_ids: list[str]
    top_rerank_score: float | None
    latency_ms: float
    rerank_used: bool
    degraded_reason: str | None
    failure_type: str
    failure_reason: str
    retrieval_failure: bool
    evidence_failure: bool
    ranking_failure: bool


def run_retrieval_ablation(
    engine: RetrievalEngine,
    cases: list[RetrievalEvalCase],
    *,
    strategies: list[RetrievalStrategy],
    top_k: int,
) -> dict[str, Any]:
    """Run all strategies on the same cases and return comparable metrics."""

    if top_k <= 0:
        raise ValueError("top_k must be positive.")
    reports: dict[str, Any] = {}
    for strategy in strategies:
        results = [
            _evaluate_case(engine, case, strategy=strategy, top_k=top_k)
            for case in cases
        ]
        reports[strategy] = _aggregate(results)
    return reports


def _evaluate_case(
    engine: RetrievalEngine,
    case: RetrievalEvalCase,
    *,
    strategy: RetrievalStrategy,
    top_k: int,
) -> RetrievalCaseResult:
    retrieval = engine.retrieve(case.question, top_k=top_k, strategy=strategy)
    documents = retrieval.documents
    candidates = retrieval.candidate_documents or documents
    sources = [str(doc.metadata.get("source_id", "")) for doc in documents]
    chunk_ids = [str(doc.metadata.get("chunk_id", "")) for doc in documents]
    candidate_sources = {
        str(doc.metadata.get("source_id", "")) for doc in candidates
    }
    if not case.answerable:
        return RetrievalCaseResult(
            id=case.id,
            split=case.split,
            domain=case.domain,
            difficulty=case.difficulty,
            category=case.category,
            tags=list(case.tags),
            question=case.question,
            gold_source_ids=list(case.source_ids),
            gold_evidence_phrases=list(case.evidence_phrases),
            answerable=False,
            source_hit=None,
            candidate_source_hit=None,
            reciprocal_rank=None,
            ndcg=None,
            evidence_recall=None,
            retrieved_sources=sources,
            retrieved_chunk_ids=chunk_ids,
            candidate_chunk_ids=[
                str(doc.metadata.get("chunk_id", "")) for doc in candidates
            ],
            top_rerank_score=_top_rerank_score(documents),
            latency_ms=retrieval.latency_ms,
            rerank_used=retrieval.rerank_used,
            degraded_reason=retrieval.degraded_reason,
            failure_type="none",
            failure_reason="no gold source expected",
            retrieval_failure=False,
            evidence_failure=False,
            ranking_failure=False,
        )

    source_ranks = [
        rank
        for rank, source_id in enumerate(sources, start=1)
        if source_id in case.source_ids
    ]
    first_rank = min(source_ranks) if source_ranks else None
    combined = "\n".join(document.page_content for document in documents)
    evidence_hits = sum(phrase in combined for phrase in case.evidence_phrases)
    evidence_recall = evidence_hits / len(case.evidence_phrases)
    source_hit = first_rank is not None
    candidate_source_hit = bool(candidate_sources & set(case.source_ids))
    retrieval_failure = not candidate_source_hit
    ranking_failure = candidate_source_hit and not source_hit
    evidence_failure = source_hit and evidence_recall < 1.0
    failure_type = (
        "retrieval_failure"
        if retrieval_failure
        else "ranking_failure"
        if ranking_failure
        else "evidence_failure"
        if evidence_failure
        else "none"
    )
    failure_reason = {
        "retrieval_failure": "gold source was absent from the candidate pool",
        "ranking_failure": "gold source was recalled but not present in top-k",
        "evidence_failure": "gold source was in top-k but evidence phrase coverage was incomplete",
        "none": "gold source and evidence were found in top-k",
    }[failure_type]
    return RetrievalCaseResult(
        id=case.id,
        split=case.split,
        domain=case.domain,
        difficulty=case.difficulty,
        category=case.category,
        tags=list(case.tags),
        question=case.question,
        gold_source_ids=list(case.source_ids),
        gold_evidence_phrases=list(case.evidence_phrases),
        answerable=True,
        source_hit=source_hit,
        candidate_source_hit=candidate_source_hit,
        reciprocal_rank=1 / first_rank if first_rank else 0.0,
        ndcg=1 / math.log2(first_rank + 1) if first_rank else 0.0,
        evidence_recall=evidence_recall,
        retrieved_sources=sources,
        retrieved_chunk_ids=chunk_ids,
        candidate_chunk_ids=[
            str(doc.metadata.get("chunk_id", "")) for doc in candidates
        ],
        top_rerank_score=_top_rerank_score(documents),
        latency_ms=retrieval.latency_ms,
        rerank_used=retrieval.rerank_used,
        degraded_reason=retrieval.degraded_reason,
        failure_type=failure_type,
        failure_reason=failure_reason,
        retrieval_failure=retrieval_failure,
        evidence_failure=evidence_failure,
        ranking_failure=ranking_failure,
    )


def _aggregate(results: list[RetrievalCaseResult]) -> dict[str, Any]:
    """Aggregate one strategy and retain case-level failure evidence."""

    report = _summary(results)
    failure_counts = defaultdict(int)
    for result in results:
        failure_counts[result.failure_type] += 1
    report.update(
        {
            "failure_counts": dict(sorted(failure_counts.items())),
            "failure_rates": {
                name: count / len(results) if results else 0.0
                for name, count in sorted(failure_counts.items())
            },
            "retrieval_failure_count": sum(item.retrieval_failure for item in results),
            "evidence_failure_count": sum(item.evidence_failure for item in results),
            "ranking_failure_count": sum(item.ranking_failure for item in results),
            "by_split": _group_results(results, lambda item: item.split),
            "by_domain": _group_results(results, lambda item: item.domain),
            "by_category": _group_results(results, lambda item: item.category),
            "by_difficulty": _group_results(results, lambda item: item.difficulty),
            "by_tag": _group_results(
                results,
                lambda item: item.tags or ["untagged"],
            ),
            "results": [asdict(result) for result in results],
        }
    )
    return report


def _summary(results: list[RetrievalCaseResult]) -> dict[str, Any]:
    answerable = [result for result in results if result.answerable]
    no_answer = [result for result in results if not result.answerable]
    latencies = [result.latency_ms for result in results]
    degradation_count = sum(result.degraded_reason is not None for result in results)
    return {
        "case_count": len(results),
        "answerable_case_count": len(answerable),
        "no_answer_case_count": len(no_answer),
        "source_hit_at_k": _mean(result.source_hit for result in answerable),
        "mrr_at_k": _mean(result.reciprocal_rank for result in answerable),
        "ndcg_at_k": _mean(result.ndcg for result in answerable),
        "evidence_recall_at_k": _mean(
            result.evidence_recall for result in answerable
        ),
        "full_evidence_hit_rate": _mean(
            result.evidence_recall == 1.0 for result in answerable
        ),
        "latency_p50_ms": statistics.median(latencies) if latencies else 0.0,
        "latency_p95_ms": _percentile(latencies, 0.95),
        "rerank_degradation_count": degradation_count,
        "answerable_min_top_rerank_score": _minimum_score(answerable),
        "no_answer_max_top_rerank_score": _maximum_score(no_answer),
        "no_answer_candidate_rate": _mean(
            result.candidate_source_hit for result in no_answer
        ),
    }


def _group_results(
    results: list[RetrievalCaseResult],
    key: Callable[[RetrievalCaseResult], str | list[str]],
) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[RetrievalCaseResult]] = defaultdict(list)
    for result in results:
        value = key(result)
        values = value if isinstance(value, list) else [value]
        for item in values:
            groups[str(item)].append(result)
    return {name: _summary(items) for name, items in sorted(groups.items())}


def _mean(values: Iterable[float | bool | None]) -> float:
    cleaned = [float(value) for value in values if value is not None]
    return sum(cleaned) / len(cleaned) if cleaned else 0.0


def _percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _top_rerank_score(documents: list[Document]) -> float | None:
    if not documents:
        return None
    score = documents[0].metadata.get("rerank_score")
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        return None
    return float(score)


def _minimum_score(results: list[RetrievalCaseResult]) -> float | None:
    valid = [
        result.top_rerank_score
        for result in results
        if result.top_rerank_score is not None
    ]
    return min(valid) if valid else None


def _maximum_score(results: list[RetrievalCaseResult]) -> float | None:
    valid = [
        result.top_rerank_score
        for result in results
        if result.top_rerank_score is not None
    ]
    return max(valid) if valid else None
