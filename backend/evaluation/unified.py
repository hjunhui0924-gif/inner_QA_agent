"""Canonical catalog for all RAG evaluation suites.

The project keeps the original datasets as source fixtures, while this module
normalizes their different schemas into one case shape.  Corpus identity is
kept on every case so a unified report never implies that unrelated corpora
were indexed together.
"""

from __future__ import annotations

import json
import math
import statistics
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Literal

from langchain_core.documents import Document

from backend.evaluation.schema import EvalCase, parse_eval_cases
from backend.retrieval.engine import RetrievalResult


BASE_DIR = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST_PATH = BASE_DIR / "data" / "evals" / "unified_rag_eval_manifest.json"

UnifiedRole = Literal["safety", "main_regression", "legacy_compatibility"]


@dataclass(frozen=True)
class UnifiedDatasetSpec:
    """One source dataset and the corpus used to evaluate it."""

    id: str
    path: Path
    corpus_id: str
    parser: str
    role: UnifiedRole
    default_split: str
    default_domain: str
    gate: dict[str, Any]
    threshold_file: Path | None = None


@dataclass(frozen=True)
class UnifiedEvalCase:
    """Normalized case shared by retrieval and answer-level runners."""

    id: str
    source_case_id: str
    dataset_id: str
    corpus_id: str
    role: UnifiedRole
    split: str
    domain: str
    difficulty: str
    category: str
    tags: list[str]
    question: str
    answerable: bool
    source_ids: list[str]
    evidence_phrases: list[str]
    expected_facts: list[str]
    must_cite: bool
    expected_refusal_reason: str | None = None
    source_title: str = ""
    gold_answer: str = ""
    expected_keywords: list[str] | None = None

    def to_eval_case(self) -> EvalCase:
        """Convert a structured case to the shared retrieval evaluator shape."""

        split = (
            self.split
            if self.split in {"development", "regression", "held_out"}
            else "development"
        )
        return EvalCase(
            id=self.id,
            category=self.category,
            question=self.question,
            source_ids=list(self.source_ids),
            evidence_phrases=list(self.evidence_phrases),
            split=split,  # type: ignore[arg-type]
            domain=self.domain,
            difficulty=self.difficulty,
            expected_facts=list(self.expected_facts),
            must_cite=self.must_cite,
            expected_refusal_reason=self.expected_refusal_reason,
            tags=list(self.tags),
            answerability="answerable" if self.answerable else "unanswerable",
        )


@dataclass(frozen=True)
class UnifiedEvalSuite:
    """All normalized cases plus their source specifications."""

    manifest_path: Path
    specs: tuple[UnifiedDatasetSpec, ...]
    cases: tuple[UnifiedEvalCase, ...]

    def cases_for(self, dataset_id: str) -> list[UnifiedEvalCase]:
        """Return cases belonging to one catalog dataset."""

        return [case for case in self.cases if case.dataset_id == dataset_id]

    def summary(self) -> dict[str, Any]:
        """Return counts that are safe to show before running a benchmark."""

        by_dataset: dict[str, dict[str, Any]] = {}
        for spec in self.specs:
            selected = self.cases_for(spec.id)
            by_dataset[spec.id] = {
                "case_count": len(selected),
                "answerable_count": sum(case.answerable for case in selected),
                "unanswerable_count": sum(not case.answerable for case in selected),
                "split_counts": dict(Counter(case.split for case in selected)),
                "domain_counts": dict(Counter(case.domain for case in selected)),
                "role": spec.role,
                "corpus_id": spec.corpus_id,
                "gate": dict(spec.gate),
            }
        return {
            "suite_id": "unified_rag",
            "case_count": len(self.cases),
            "answerable_count": sum(case.answerable for case in self.cases),
            "unanswerable_count": sum(not case.answerable for case in self.cases),
            "dataset_counts": by_dataset,
            "split_counts": dict(Counter(case.split for case in self.cases)),
        }


@dataclass(frozen=True)
class UnifiedRetrievalCaseResult:
    """Comparable retrieval outcome for one normalized evaluation case."""

    id: str
    source_case_id: str
    dataset_id: str
    corpus_id: str
    role: UnifiedRole
    split: str
    domain: str
    category: str
    question: str
    answerable: bool
    gold_source_ids: list[str]
    expected_evidence: list[str]
    evidence_support_score: float
    source_hit: bool | None
    candidate_source_hit: bool | None
    reciprocal_rank: float | None
    ndcg: float | None
    evidence_recall: float | None
    retrieved_sources: list[str]
    retrieved_titles: list[str]
    retrieved_chunk_ids: list[str]
    candidate_chunk_ids: list[str]
    top_rerank_score: float | None
    retrieved_nonempty: bool
    latency_ms: float
    rerank_used: bool
    degraded_reason: str | None
    failure_type: str
    failure_reason: str
    retrieval_failure: bool
    evidence_failure: bool
    ranking_failure: bool


def evaluate_unified_retrieval_case(
    case: UnifiedEvalCase,
    retrieval: RetrievalResult,
) -> UnifiedRetrievalCaseResult:
    """Evaluate one retrieval result without mixing corpus-specific cases.

    Structured cases use ``source_ids`` as the relevance key.  The legacy
    single-document cases have no source IDs in their original schema, so they
    use the exact ``source_title`` instead.  Both paths expose the same result
    interface to the unified runner.
    """

    documents = list(retrieval.documents)
    candidates = list(retrieval.candidate_documents or documents)
    retrieved_sources = [
        str(document.metadata.get("source_id", "")).strip()
        for document in documents
    ]
    retrieved_titles = [
        str(document.metadata.get("title", "")).strip() for document in documents
    ]
    candidate_sources = {
        str(document.metadata.get("source_id", "")).strip()
        for document in candidates
    }
    retrieved_chunk_ids = [
        str(document.metadata.get("chunk_id", "")).strip()
        for document in documents
    ]
    candidate_chunk_ids = [
        str(document.metadata.get("chunk_id", "")).strip()
        for document in candidates
    ]
    top_rerank_score = _top_rerank_score(documents)

    if not case.answerable:
        return UnifiedRetrievalCaseResult(
            id=case.id,
            source_case_id=case.source_case_id,
            dataset_id=case.dataset_id,
            corpus_id=case.corpus_id,
            role=case.role,
            split=case.split,
            domain=case.domain,
            category=case.category,
            question=case.question,
            answerable=False,
            gold_source_ids=list(case.source_ids),
            expected_evidence=list(case.evidence_phrases),
            evidence_support_score=0.0,
            source_hit=None,
            candidate_source_hit=None,
            reciprocal_rank=None,
            ndcg=None,
            evidence_recall=None,
            retrieved_sources=retrieved_sources,
            retrieved_titles=retrieved_titles,
            retrieved_chunk_ids=retrieved_chunk_ids,
            candidate_chunk_ids=candidate_chunk_ids,
            top_rerank_score=top_rerank_score,
            retrieved_nonempty=bool(documents),
            latency_ms=retrieval.latency_ms,
            rerank_used=retrieval.rerank_used,
            degraded_reason=retrieval.degraded_reason,
            failure_type="none",
            failure_reason="no gold answer expected",
            retrieval_failure=False,
            evidence_failure=False,
            ranking_failure=False,
        )

    if case.source_ids:
        gold_hit = lambda document: str(  # noqa: E731
            document.metadata.get("source_id", "")
        ).strip() in set(case.source_ids)
        candidate_source_hit = bool(candidate_sources & set(case.source_ids))
    else:
        expected_title = case.source_title.strip()
        gold_hit = lambda document: (  # noqa: E731
            str(document.metadata.get("title", "")).strip() == expected_title
        )
        candidate_source_hit = any(gold_hit(document) for document in candidates)

    ranks = [
        rank for rank, document in enumerate(documents, start=1) if gold_hit(document)
    ]
    first_rank = min(ranks) if ranks else None
    combined = "\n".join(document.page_content for document in documents)
    expected_evidence = list(case.evidence_phrases)
    evidence_recall = (
        sum(
            _evidence_phrase_present(
                phrase,
                combined,
                legacy=case.dataset_id == "dongshan_legacy",
            )
            for phrase in expected_evidence
        )
        / len(expected_evidence)
        if expected_evidence
        else 1.0
    )
    source_hit = first_rank is not None
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
        "evidence_failure": "gold source was in top-k but evidence coverage was incomplete",
        "none": "gold source and evidence were found in top-k",
    }[failure_type]
    return UnifiedRetrievalCaseResult(
        id=case.id,
        source_case_id=case.source_case_id,
        dataset_id=case.dataset_id,
        corpus_id=case.corpus_id,
        role=case.role,
        split=case.split,
        domain=case.domain,
        category=case.category,
        question=case.question,
        answerable=True,
        gold_source_ids=list(case.source_ids),
        expected_evidence=expected_evidence,
        evidence_support_score=evidence_recall,
        source_hit=source_hit,
        candidate_source_hit=candidate_source_hit,
        reciprocal_rank=1 / first_rank if first_rank else 0.0,
        ndcg=1 / math.log2(first_rank + 1) if first_rank else 0.0,
        evidence_recall=evidence_recall,
        retrieved_sources=retrieved_sources,
        retrieved_titles=retrieved_titles,
        retrieved_chunk_ids=retrieved_chunk_ids,
        candidate_chunk_ids=candidate_chunk_ids,
        top_rerank_score=top_rerank_score,
        retrieved_nonempty=bool(documents),
        latency_ms=retrieval.latency_ms,
        rerank_used=retrieval.rerank_used,
        degraded_reason=retrieval.degraded_reason,
        failure_type=failure_type,
        failure_reason=failure_reason,
        retrieval_failure=retrieval_failure,
        evidence_failure=evidence_failure,
        ranking_failure=ranking_failure,
    )


def _evidence_phrase_present(phrase: str, content: str, *, legacy: bool) -> bool:
    """Match legacy numeric variants without weakening structured gold checks."""

    if phrase in content:
        return True
    if not legacy:
        return False
    normalize = lambda value: "".join(  # noqa: E731
        character for character in value if character not in {" ", "\t", ",", "，"}
    )
    normalized_phrase = normalize(phrase)
    return bool(normalized_phrase) and normalized_phrase in normalize(content)


def aggregate_unified_retrieval(
    results: Iterable[UnifiedRetrievalCaseResult],
) -> dict[str, Any]:
    """Aggregate normalized retrieval rows while excluding no-answer rows."""

    rows = list(results)
    answerable = [row for row in rows if row.answerable]
    no_answer = [row for row in rows if not row.answerable]
    failure_counts = Counter(row.failure_type for row in rows)
    return {
        "case_count": len(rows),
        "answerable_case_count": len(answerable),
        "no_answer_case_count": len(no_answer),
        "source_hit_at_k": _mean(row.source_hit for row in answerable),
        "mrr_at_k": _mean(row.reciprocal_rank for row in answerable),
        "ndcg_at_k": _mean(row.ndcg for row in answerable),
        "evidence_recall_at_k": _mean(row.evidence_recall for row in answerable),
        "full_evidence_hit_rate": _mean(
            row.evidence_recall == 1.0 for row in answerable
        ),
        "latency_p50_ms": statistics.median(
            [row.latency_ms for row in rows]
        ) if rows else 0.0,
        "latency_p95_ms": _percentile(
            [row.latency_ms for row in rows], 0.95
        ),
        "rerank_degradation_count": sum(
            row.degraded_reason is not None for row in rows
        ),
        "retrieval_failure_count": sum(row.retrieval_failure for row in rows),
        "evidence_failure_count": sum(row.evidence_failure for row in rows),
        "ranking_failure_count": sum(row.ranking_failure for row in rows),
        "no_answer_retrieved_nonempty_rate": _mean(
            row.retrieved_nonempty for row in no_answer
        ),
        "not_generation_refusal_evaluation": True,
        "failure_counts": dict(sorted(failure_counts.items())),
        "results": [asdict(row) for row in rows],
    }


def _top_rerank_score(documents: list[Document]) -> float | None:
    if not documents:
        return None
    value = documents[0].metadata.get("rerank_score")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


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


def load_unified_suite(
    manifest_path: str | Path = DEFAULT_MANIFEST_PATH,
    *,
    dataset_ids: set[str] | None = None,
) -> UnifiedEvalSuite:
    """Load and validate the manifest and all selected source datasets."""

    manifest_file = Path(manifest_path)
    if not manifest_file.is_absolute():
        manifest_file = BASE_DIR / manifest_file
    manifest = _load_json(manifest_file)
    if not isinstance(manifest, dict):
        raise ValueError("Unified evaluation manifest must be a JSON object.")
    raw_specs = manifest.get("datasets")
    if not isinstance(raw_specs, list) or not raw_specs:
        raise ValueError("Unified evaluation manifest must define datasets.")

    specs: list[UnifiedDatasetSpec] = []
    cases: list[UnifiedEvalCase] = []
    seen_ids: set[str] = set()
    available_ids: set[str] = set()
    for raw_spec in raw_specs:
        if isinstance(raw_spec, dict) and str(raw_spec.get("id", "")).strip():
            available_ids.add(str(raw_spec["id"]).strip())
    if dataset_ids is not None:
        unknown_ids = sorted(dataset_ids - available_ids)
        if unknown_ids:
            raise ValueError(
                "Unknown unified evaluation datasets: " + ", ".join(unknown_ids)
            )
    for raw_spec in raw_specs:
        spec = _parse_spec(raw_spec, manifest_file.parent)
        if dataset_ids is not None and spec.id not in dataset_ids:
            continue
        if any(item.id == spec.id for item in specs):
            raise ValueError(f"Duplicate unified dataset id: {spec.id}")
        specs.append(spec)
        source_data = _load_json(spec.path)
        normalized = _normalize_cases(spec, source_data)
        for case in normalized:
            if case.id in seen_ids:
                raise ValueError(f"Duplicate unified case id: {case.id}")
            seen_ids.add(case.id)
            cases.append(case)

    if not specs:
        raise ValueError("No unified evaluation datasets were selected.")
    return UnifiedEvalSuite(
        manifest_path=manifest_file,
        specs=tuple(specs),
        cases=tuple(cases),
    )


def _parse_spec(raw: Any, manifest_parent: Path) -> UnifiedDatasetSpec:
    if not isinstance(raw, dict):
        raise ValueError("Unified dataset specification must be an object.")
    dataset_id = _required_text(raw, "id")
    parser = _required_text(raw, "parser")
    role = _required_text(raw, "role")
    if role not in {"safety", "main_regression", "legacy_compatibility"}:
        raise ValueError(f"Unknown unified dataset role: {role}")
    path_value = _required_text(raw, "path")
    path = Path(path_value)
    if not path.is_absolute():
        project_path = BASE_DIR / path
        path = project_path if project_path.exists() else manifest_parent / path
    return UnifiedDatasetSpec(
        id=dataset_id,
        path=path,
        corpus_id=_required_text(raw, "corpus_id"),
        parser=parser,
        role=role,  # type: ignore[arg-type]
        default_split=_required_text(raw, "default_split"),
        default_domain=_required_text(raw, "default_domain"),
        gate=_parse_gate(raw.get("gate"), dataset_id),
        threshold_file=_optional_path(
            raw.get("threshold_file"),
            manifest_parent,
        ),
    )


def _normalize_cases(spec: UnifiedDatasetSpec, data: Any) -> list[UnifiedEvalCase]:
    if spec.parser == "official_policy":
        return _normalize_structured_cases(spec, parse_eval_cases(data))
    if spec.parser == "enterprise_rag":
        return _normalize_structured_cases(
            spec,
            parse_eval_cases(data, require_structured=True),
        )
    if spec.parser == "dongshan_legacy":
        return _normalize_legacy_cases(spec, data)
    raise ValueError(f"Unknown unified dataset parser: {spec.parser}")


def _normalize_structured_cases(
    spec: UnifiedDatasetSpec,
    cases: list[EvalCase],
) -> list[UnifiedEvalCase]:
    normalized: list[UnifiedEvalCase] = []
    for case in cases:
        normalized.append(
            UnifiedEvalCase(
                id=f"{spec.id}:{case.id}",
                source_case_id=case.id,
                dataset_id=spec.id,
                corpus_id=spec.corpus_id,
                role=spec.role,
                split=(
                    spec.default_split
                    if spec.parser == "official_policy"
                    else case.split
                ),
                domain=(spec.default_domain if case.domain == "unknown" else case.domain),
                difficulty=case.difficulty,
                category=case.category,
                tags=[*case.tags, spec.id],
                question=case.question,
                answerable=case.answerable,
                source_ids=list(case.source_ids),
                evidence_phrases=list(case.evidence_phrases),
                expected_facts=list(case.facts),
                must_cite=case.must_cite,
                expected_refusal_reason=case.expected_refusal_reason,
            )
        )
    return normalized


def _normalize_legacy_cases(
    spec: UnifiedDatasetSpec,
    data: Any,
) -> list[UnifiedEvalCase]:
    if not isinstance(data, list):
        raise ValueError("Legacy evaluation dataset must be a JSON array.")
    normalized: list[UnifiedEvalCase] = []
    seen: set[str] = set()
    for index, raw in enumerate(data):
        if not isinstance(raw, dict):
            raise ValueError(f"Legacy evaluation case {index} must be an object.")
        source_case_id = _required_text(raw, "id", context=f"legacy case {index}")
        if source_case_id in seen:
            raise ValueError(f"Duplicate legacy case id: {source_case_id}")
        seen.add(source_case_id)
        question = _required_text(raw, "question", context=source_case_id)
        gold_answer = _required_text(raw, "gold_answer", context=source_case_id)
        source_title = _required_text(raw, "source_title", context=source_case_id)
        expected_keywords = _string_list(raw, "expected_keywords", source_case_id)
        normalized.append(
            UnifiedEvalCase(
                id=f"{spec.id}:{source_case_id}",
                source_case_id=source_case_id,
                dataset_id=spec.id,
                corpus_id=spec.corpus_id,
                role=spec.role,
                split=spec.default_split,
                domain=spec.default_domain,
                difficulty="legacy",
                category="legacy_single_document",
                tags=["legacy", "single_document", spec.id],
                question=question,
                answerable=True,
                source_ids=[],
                evidence_phrases=list(expected_keywords),
                expected_facts=[gold_answer],
                must_cite=True,
                source_title=source_title,
                gold_answer=gold_answer,
                expected_keywords=list(expected_keywords),
            )
        )
    return normalized


def _load_json(path: Path) -> Any:
    if not path.exists():
        raise ValueError(f"Unified evaluation file does not exist: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in unified evaluation file: {path}") from exc


def _required_text(raw: dict[str, Any], name: str, *, context: str = "dataset") -> str:
    value = str(raw.get(name, "")).strip()
    if not value:
        raise ValueError(f"{context} requires {name}.")
    return value


def _string_list(raw: dict[str, Any], name: str, context: str) -> list[str]:
    value = raw.get(name)
    if not isinstance(value, list) or any(not str(item).strip() for item in value):
        raise ValueError(f"{context}.{name} must be a non-empty string array.")
    return [str(item).strip() for item in value]


def _parse_gate(value: Any, dataset_id: str) -> dict[str, Any]:
    if value is None:
        return {"blocking": False}
    if not isinstance(value, dict):
        raise ValueError(f"Dataset {dataset_id}.gate must be an object.")
    blocking = value.get("blocking", False)
    if not isinstance(blocking, bool):
        raise ValueError(f"Dataset {dataset_id}.gate.blocking must be boolean.")
    gate = dict(value)
    gate["blocking"] = blocking
    return gate


def _optional_path(value: Any, manifest_parent: Path) -> Path | None:
    if value is None or str(value).strip() == "":
        return None
    path = Path(str(value).strip())
    if path.is_absolute():
        return path
    project_path = BASE_DIR / path
    return project_path if project_path.exists() else manifest_parent / path
