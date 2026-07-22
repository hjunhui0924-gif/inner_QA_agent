"""Deterministic retrieval ablation metrics."""

from __future__ import annotations

import math
import statistics
from dataclasses import asdict, dataclass
from typing import Any, Iterable

from langchain_core.documents import Document

from backend.retrieval.engine import RetrievalEngine, RetrievalStrategy


@dataclass(frozen=True)
class RetrievalEvalCase:
    id: str
    category: str
    question: str
    source_ids: list[str]
    evidence_phrases: list[str]

    @property
    def answerable(self) -> bool:
        return bool(self.source_ids)


@dataclass(frozen=True)
class RetrievalCaseResult:
    id: str
    category: str
    question: str
    answerable: bool
    source_hit: bool | None
    reciprocal_rank: float | None
    ndcg: float | None
    evidence_recall: float | None
    retrieved_sources: list[str]
    retrieved_chunk_ids: list[str]
    top_rerank_score: float | None
    latency_ms: float
    rerank_used: bool
    degraded_reason: str | None


def parse_cases(data: Any) -> list[RetrievalEvalCase]:
    if not isinstance(data, list):
        raise ValueError("Retrieval evaluation dataset must be a JSON array.")
    cases: list[RetrievalEvalCase] = []
    seen: set[str] = set()
    for raw in data:
        if not isinstance(raw, dict):
            raise ValueError("Every retrieval case must be a JSON object.")
        case = RetrievalEvalCase(
            id=str(raw.get("id", "")).strip(),
            category=str(raw.get("category", "")).strip(),
            question=str(raw.get("question", "")).strip(),
            source_ids=[str(item).strip() for item in raw.get("source_ids", [])],
            evidence_phrases=[
                str(item).strip() for item in raw.get("evidence_phrases", [])
            ],
        )
        if not case.id or not case.category or not case.question:
            raise ValueError("Case id, category, and question are required.")
        if case.id in seen:
            raise ValueError(f"Duplicate retrieval case id: {case.id}")
        if case.answerable and not case.evidence_phrases:
            raise ValueError(f"Answerable case {case.id} has no evidence phrases.")
        if not case.answerable and case.evidence_phrases:
            raise ValueError(f"No-answer case {case.id} must not contain evidence.")
        seen.add(case.id)
        cases.append(case)
    return cases


def validate_cases_against_corpus(
    cases: Iterable[RetrievalEvalCase],
    documents: Iterable[Document],
) -> None:
    """Fail when a gold source or evidence phrase is absent from the corpus."""

    content_by_source: dict[str, list[str]] = {}
    for document in documents:
        source_id = str(document.metadata.get("source_id", "")).strip()
        if source_id:
            content_by_source.setdefault(source_id, []).append(document.page_content)
    errors: list[str] = []
    for case in cases:
        for source_id in case.source_ids:
            if source_id not in content_by_source:
                errors.append(f"{case.id}: unknown source {source_id}")
        source_text = "\n".join(
            text
            for source_id in case.source_ids
            for text in content_by_source.get(source_id, [])
        )
        for phrase in case.evidence_phrases:
            if phrase not in source_text:
                errors.append(f"{case.id}: evidence not found: {phrase}")
    if errors:
        raise ValueError("Invalid retrieval gold data:\n" + "\n".join(errors))


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
    sources = [str(doc.metadata.get("source_id", "")) for doc in documents]
    chunk_ids = [str(doc.metadata.get("chunk_id", "")) for doc in documents]
    if not case.answerable:
        return RetrievalCaseResult(
            id=case.id,
            category=case.category,
            question=case.question,
            answerable=False,
            source_hit=None,
            reciprocal_rank=None,
            ndcg=None,
            evidence_recall=None,
            retrieved_sources=sources,
            retrieved_chunk_ids=chunk_ids,
            top_rerank_score=_top_rerank_score(documents),
            latency_ms=retrieval.latency_ms,
            rerank_used=retrieval.rerank_used,
            degraded_reason=retrieval.degraded_reason,
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
    return RetrievalCaseResult(
        id=case.id,
        category=case.category,
        question=case.question,
        answerable=True,
        source_hit=first_rank is not None,
        reciprocal_rank=1 / first_rank if first_rank else 0.0,
        ndcg=1 / math.log2(first_rank + 1) if first_rank else 0.0,
        evidence_recall=evidence_recall,
        retrieved_sources=sources,
        retrieved_chunk_ids=chunk_ids,
        top_rerank_score=_top_rerank_score(documents),
        latency_ms=retrieval.latency_ms,
        rerank_used=retrieval.rerank_used,
        degraded_reason=retrieval.degraded_reason,
    )


def _aggregate(results: list[RetrievalCaseResult]) -> dict[str, Any]:
    answerable = [result for result in results if result.answerable]
    no_answer = [result for result in results if not result.answerable]
    latencies = [result.latency_ms for result in results]
    degradation_count = sum(result.degraded_reason is not None for result in results)
    return {
        "case_count": len(results),
        "answerable_case_count": len(answerable),
        "no_answer_case_count": len(results) - len(answerable),
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
        "results": [asdict(result) for result in results],
    }


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
