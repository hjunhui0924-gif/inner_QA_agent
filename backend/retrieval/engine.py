"""Four-stage retrieval behind one stable interface."""

from __future__ import annotations

import math
import re
import time
from collections import Counter
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal, Protocol

from langchain_core.documents import Document

from backend.retrieval.reranker import Reranker


RetrievalStrategy = Literal["dense", "lexical", "fusion", "rerank"]


class DenseStore(Protocol):
    def similarity_search(self, query: str, k: int = 4) -> list[Document]: ...


@dataclass(frozen=True)
class RetrievalConfig:
    """Candidate sizes and fusion weights for one retrieval experiment."""

    dense_candidate_k: int = 20
    lexical_candidate_k: int = 20
    rerank_candidate_k: int = 12
    rrf_k: int = 60
    dense_weight: float = 0.5
    lexical_weight: float = 0.5
    production_strategy: RetrievalStrategy = "rerank"

    def validate(self) -> None:
        if min(
            self.dense_candidate_k,
            self.lexical_candidate_k,
            self.rerank_candidate_k,
            self.rrf_k,
        ) <= 0:
            raise ValueError("Retrieval candidate sizes and rrf_k must be positive.")
        if self.dense_weight < 0 or self.lexical_weight < 0:
            raise ValueError("Retrieval weights must not be negative.")
        if self.dense_weight + self.lexical_weight == 0:
            raise ValueError("At least one retrieval weight must be positive.")


@dataclass(frozen=True)
class RetrievalFilter:
    """Optional metadata constraints for one retrieval call."""

    source_ids: frozenset[str] | None = None
    versions: frozenset[str] | None = None
    departments: frozenset[str] | None = None
    statuses: frozenset[str] | None = None
    access_scopes: frozenset[str] | None = None
    as_of: date | str | None = None
    prefer_current: bool | None = None

    def validate(self) -> None:
        for name, values in (
            ("source_ids", self.source_ids),
            ("versions", self.versions),
            ("departments", self.departments),
            ("statuses", self.statuses),
            ("access_scopes", self.access_scopes),
        ):
            if values is not None and any(not str(value).strip() for value in values):
                raise ValueError(f"Retrieval filter {name} must not contain empty values.")
        if self.as_of is not None:
            if isinstance(self.as_of, date):
                return
            try:
                date.fromisoformat(str(self.as_of).strip())
            except ValueError as exc:
                raise ValueError("Retrieval filter as_of must be an ISO date.") from exc

    def as_dict(self) -> dict[str, Any]:
        """Return compact diagnostics suitable for traces and reports."""

        return {
            "source_ids": sorted(self.source_ids) if self.source_ids is not None else None,
            "versions": sorted(self.versions) if self.versions is not None else None,
            "departments": sorted(self.departments)
            if self.departments is not None
            else None,
            "statuses": sorted(self.statuses) if self.statuses is not None else None,
            "access_scopes": sorted(self.access_scopes)
            if self.access_scopes is not None
            else None,
            "as_of": self.as_of.isoformat()
            if isinstance(self.as_of, date)
            else self.as_of,
            "prefer_current": self.prefer_current,
        }


@dataclass(frozen=True)
class RetrievalResult:
    """Documents plus diagnostics required by evaluation and tracing."""

    documents: list[Document]
    strategy: RetrievalStrategy
    dense_candidates: int
    lexical_candidates: int
    fused_candidates: int
    rerank_used: bool
    degraded_reason: str | None
    latency_ms: float
    candidate_documents: list[Document] | None = None
    filtered_candidate_count: int = 0
    applied_filter: dict[str, Any] | None = None


@dataclass(frozen=True)
class _RankedCandidate:
    document: Document
    fused_score: float
    dense_rank: int | None
    lexical_rank: int | None


class RetrievalEngine:
    """Run dense recall, BM25 recall, RRF fusion, and optional reranking."""

    def __init__(
        self,
        dense_store: DenseStore,
        documents: list[Document],
        *,
        config: RetrievalConfig | None = None,
        reranker: Reranker | None = None,
    ) -> None:
        self._dense_store = dense_store
        self._documents = list(documents)
        self._config = config or RetrievalConfig()
        self._config.validate()
        self._reranker = reranker
        self._rebuild_lexical_index()

    def add_documents(self, documents: list[Document]) -> None:
        """Add newly persisted documents to the lexical index."""

        known = {_document_key(document) for document in self._documents}
        for document in documents:
            key = _document_key(document)
            if key in known:
                continue
            self._documents.append(document)
            known.add(key)
        self._rebuild_lexical_index()

    def retrieve(
        self,
        query: str,
        top_k: int = 4,
        *,
        strategy: RetrievalStrategy | None = None,
        filters: RetrievalFilter | None = None,
    ) -> RetrievalResult:
        """Retrieve documents using the requested ablation strategy."""

        started = time.perf_counter()
        selected = strategy or self._config.production_strategy
        if selected not in {"dense", "lexical", "fusion", "rerank"}:
            raise ValueError(f"Unknown retrieval strategy: {selected}")
        if filters is not None:
            filters.validate()
        prefer_current = (
            filters.prefer_current
            if filters is not None and filters.prefer_current is not None
            else _query_prefers_current(query)
        )
        applied_filter = filters.as_dict() if filters is not None else None
        if top_k <= 0 or not query.strip():
            return self._result(
                [],
                selected,
                0,
                0,
                0,
                False,
                None,
                started,
                applied_filter=applied_filter,
            )

        dense: list[Document] = []
        lexical: list[Document] = []
        if selected in {"dense", "fusion", "rerank"}:
            dense = self._dense_store.similarity_search(
                query,
                k=max(
                    top_k,
                    self._config.dense_candidate_k,
                    len(self._documents) if filters is not None else 0,
                ),
            )
            dense = self._filter_documents(dense, filters)
        if selected in {"lexical", "fusion", "rerank"}:
            lexical = self._lexical_search(
                query,
                max(
                    top_k,
                    self._config.lexical_candidate_k,
                    len(self._documents) if filters is not None else 0,
                ),
            )
            lexical = self._filter_documents(lexical, filters)

        if selected == "dense":
            if prefer_current:
                dense = _prefer_current_documents(dense)
            documents = [
                _with_metadata(document, retrieval_stage="dense", dense_rank=rank)
                for rank, document in enumerate(dense[:top_k], start=1)
            ]
            return self._result(
                documents,
                selected,
                len(dense),
                0,
                0,
                False,
                None,
                started,
                candidate_documents=dense,
                filtered_candidate_count=len(dense),
                applied_filter=applied_filter,
            )
        if selected == "lexical":
            if prefer_current:
                lexical = _prefer_current_documents(lexical)
            documents = [
                _with_metadata(document, retrieval_stage="lexical", lexical_rank=rank)
                for rank, document in enumerate(lexical[:top_k], start=1)
            ]
            return self._result(
                documents,
                selected,
                0,
                len(lexical),
                0,
                False,
                None,
                started,
                candidate_documents=lexical,
                filtered_candidate_count=len(lexical),
                applied_filter=applied_filter,
            )

        fused = self._fuse(dense, lexical, prefer_current=prefer_current)
        if selected == "fusion":
            documents = [
                _candidate_document(candidate, retrieval_stage="fusion")
                for candidate in fused[:top_k]
            ]
            return self._result(
                documents,
                selected,
                len(dense),
                len(lexical),
                len(fused),
                False,
                None,
                started,
                candidate_documents=[candidate.document for candidate in fused],
                filtered_candidate_count=len(fused),
                applied_filter=applied_filter,
            )

        rerank_pool = fused[: max(top_k, self._config.rerank_candidate_k)]
        if self._reranker is None:
            documents = [
                _candidate_document(candidate, retrieval_stage="fusion_fallback")
                for candidate in rerank_pool[:top_k]
            ]
            return self._result(
                documents,
                selected,
                len(dense),
                len(lexical),
                len(fused),
                False,
                "reranker_not_configured",
                started,
                candidate_documents=[candidate.document for candidate in rerank_pool],
                filtered_candidate_count=len(rerank_pool),
                applied_filter=applied_filter,
            )
        try:
            rerank_top_n = len(rerank_pool) if prefer_current else top_k
            scores = self._reranker.rerank(
                query,
                [candidate.document for candidate in rerank_pool],
                rerank_top_n,
            )
            documents = []
            for rank, score in enumerate(scores, start=1):
                candidate = rerank_pool[score.index]
                documents.append(
                    _candidate_document(
                        candidate,
                        retrieval_stage="rerank",
                        rerank_rank=rank,
                        rerank_score=score.score,
                    )
                )
            selected_keys = {_document_key(document) for document in documents}
            for candidate in rerank_pool:
                if len(documents) >= top_k:
                    break
                if _document_key(candidate.document) in selected_keys:
                    continue
                documents.append(
                    _candidate_document(
                        candidate,
                        retrieval_stage="rerank_unscored_fallback",
                    )
                )
            if prefer_current:
                documents = _prefer_current_documents(documents)
            documents = documents[:top_k]
            return self._result(
                documents,
                selected,
                len(dense),
                len(lexical),
                len(fused),
                True,
                None,
                started,
                candidate_documents=[candidate.document for candidate in rerank_pool],
                filtered_candidate_count=len(rerank_pool),
                applied_filter=applied_filter,
            )
        except Exception as exc:
            documents = [
                _candidate_document(candidate, retrieval_stage="fusion_fallback")
                for candidate in rerank_pool[:top_k]
            ]
            reason = f"{type(exc).__name__}: {exc}"[:300]
            return self._result(
                documents,
                selected,
                len(dense),
                len(lexical),
                len(fused),
                False,
                reason,
                started,
                candidate_documents=[candidate.document for candidate in rerank_pool],
                filtered_candidate_count=len(rerank_pool),
                applied_filter=applied_filter,
            )

    def _fuse(
        self,
        dense: list[Document],
        lexical: list[Document],
        *,
        prefer_current: bool = False,
    ) -> list[_RankedCandidate]:
        total_weight = self._config.dense_weight + self._config.lexical_weight
        dense_weight = self._config.dense_weight / total_weight
        lexical_weight = self._config.lexical_weight / total_weight
        fused: dict[str, _RankedCandidate] = {}
        for rank, document in enumerate(dense, start=1):
            key = _document_key(document)
            fused[key] = _RankedCandidate(
                document=document,
                fused_score=dense_weight / (self._config.rrf_k + rank),
                dense_rank=rank,
                lexical_rank=None,
            )
        for rank, document in enumerate(lexical, start=1):
            key = _document_key(document)
            previous = fused.get(key)
            increment = lexical_weight / (self._config.rrf_k + rank)
            if previous is None:
                fused[key] = _RankedCandidate(
                    document=document,
                    fused_score=increment,
                    dense_rank=None,
                    lexical_rank=rank,
                )
            else:
                fused[key] = _RankedCandidate(
                    document=previous.document,
                    fused_score=previous.fused_score + increment,
                    dense_rank=previous.dense_rank,
                    lexical_rank=rank,
                )
        ordered = sorted(
            fused.values(),
            key=lambda item: (
                -item.fused_score,
                item.dense_rank or 10**9,
                item.lexical_rank or 10**9,
                _document_key(item.document),
            ),
        )
        return _prefer_current_candidates(ordered) if prefer_current else ordered

    def _filter_documents(
        self,
        documents: list[Document],
        filters: RetrievalFilter | None,
    ) -> list[Document]:
        if filters is None:
            return documents
        return [document for document in documents if _matches_filter(document, filters)]

    def _rebuild_lexical_index(self) -> None:
        self._token_counts = [Counter(tokenize(doc.page_content)) for doc in self._documents]
        self._document_lengths = [sum(counts.values()) for counts in self._token_counts]
        self._average_length = (
            sum(self._document_lengths) / len(self._document_lengths)
            if self._document_lengths
            else 0.0
        )
        self._document_frequency: Counter[str] = Counter()
        for counts in self._token_counts:
            self._document_frequency.update(counts.keys())

    def _lexical_search(self, query: str, limit: int) -> list[Document]:
        query_tokens = set(tokenize(query))
        if not query_tokens or not self._documents:
            return []
        scored: list[tuple[float, str, Document]] = []
        document_count = len(self._documents)
        average_length = self._average_length or 1.0
        k1 = 1.5
        b = 0.75
        for document, counts, length in zip(
            self._documents,
            self._token_counts,
            self._document_lengths,
            strict=True,
        ):
            score = 0.0
            for token in query_tokens:
                frequency = counts.get(token, 0)
                if not frequency:
                    continue
                doc_frequency = self._document_frequency[token]
                inverse_frequency = math.log(
                    1 + (document_count - doc_frequency + 0.5) / (doc_frequency + 0.5)
                )
                denominator = frequency + k1 * (
                    1 - b + b * length / average_length
                )
                score += inverse_frequency * frequency * (k1 + 1) / denominator
            if score > 0:
                scored.append((score, _document_key(document), document))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [document for _, _, document in scored[:limit]]

    @staticmethod
    def _result(
        documents: list[Document],
        strategy: RetrievalStrategy,
        dense_candidates: int,
        lexical_candidates: int,
        fused_candidates: int,
        rerank_used: bool,
        degraded_reason: str | None,
        started: float,
        *,
        candidate_documents: list[Document] | None = None,
        filtered_candidate_count: int = 0,
        applied_filter: dict[str, Any] | None = None,
    ) -> RetrievalResult:
        return RetrievalResult(
            documents=documents,
            strategy=strategy,
            dense_candidates=dense_candidates,
            lexical_candidates=lexical_candidates,
            fused_candidates=fused_candidates,
            rerank_used=rerank_used,
            degraded_reason=degraded_reason,
            latency_ms=(time.perf_counter() - started) * 1000,
            candidate_documents=candidate_documents,
            filtered_candidate_count=filtered_candidate_count,
            applied_filter=applied_filter,
        )


def tokenize(text: str) -> list[str]:
    """Tokenize mixed Chinese and ASCII text for lexical retrieval."""

    lowered = text.casefold()
    tokens = re.findall(r"[a-z0-9_]+", lowered)
    for run in re.findall(r"[\u4e00-\u9fff]+", lowered):
        tokens.extend(run)
        if len(run) >= 2:
            tokens.extend(run[index : index + 2] for index in range(len(run) - 1))
        if len(run) >= 3:
            tokens.extend(run[index : index + 3] for index in range(len(run) - 2))
    return tokens


def _query_prefers_current(query: str) -> bool:
    lowered = query.casefold()
    return any(
        term in lowered
        for term in ("现行", "当前", "最新", "目前", "现在", "有效", "生效", "current", "latest")
    )


def _matches_filter(document: Document, filters: RetrievalFilter) -> bool:
    metadata = document.metadata
    if filters.source_ids is not None and str(
        metadata.get("source_id", metadata.get("document_id", ""))
    ).strip() not in filters.source_ids:
        return False
    if filters.versions is not None and str(metadata.get("version", "")).strip() not in filters.versions:
        return False
    if filters.departments is not None and str(metadata.get("department", "")).strip() not in filters.departments:
        return False
    if filters.statuses is not None and str(metadata.get("status", "")).strip().lower() not in {
        value.casefold() for value in filters.statuses
    }:
        return False
    if filters.access_scopes is not None and str(metadata.get("access_scope", "")).strip() not in filters.access_scopes:
        return False
    if filters.as_of is not None:
        target = filters.as_of if isinstance(filters.as_of, date) else date.fromisoformat(str(filters.as_of).strip())
        effective_from = _metadata_date(metadata.get("effective_from"))
        effective_to = _metadata_date(metadata.get("effective_to"))
        if effective_from is not None and effective_from > target:
            return False
        if effective_to is not None and effective_to < target:
            return False
    return True


def _metadata_date(value: object) -> date | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError:
        return None


def _current_priority(document: Document) -> int:
    status = str(document.metadata.get("status", "active")).strip().casefold()
    return {"active": 3, "draft": 2, "deprecated": 1, "archived": 0}.get(status, 1)


def _version_sort_key(document: Document) -> tuple[int, int, tuple[int, str]]:
    effective_from = _metadata_date(document.metadata.get("effective_from"))
    version = str(document.metadata.get("version", "")).strip().casefold()
    version_match = re.search(r"(?:v|version[-_ ]?)(\d+)", version)
    version_key = (
        (int(version_match.group(1)), version)
        if version_match
        else (0, version)
    )
    return (
        _current_priority(document),
        effective_from.toordinal() if effective_from is not None else 0,
        version_key,
    )


def _prefer_current_documents(documents: list[Document]) -> list[Document]:
    """Prefer current chunks within a versioned family without global bias."""

    families = _versioned_families(documents)
    positions_by_family: dict[str, list[int]] = {}
    for index, document in enumerate(documents):
        family = str(document.metadata.get("document_family", "")).strip()
        if family in families:
            positions_by_family.setdefault(family, []).append(index)
    ordered = list(documents)
    for family, positions in positions_by_family.items():
        members = [documents[index] for index in positions]
        members.sort(key=_version_sort_key, reverse=True)
        for index, document in zip(positions, members, strict=True):
            ordered[index] = document
    return ordered


def _prefer_current_candidates(
    candidates: list[_RankedCandidate],
) -> list[_RankedCandidate]:
    documents = _prefer_current_documents([candidate.document for candidate in candidates])
    by_key = {_document_key(candidate.document): candidate for candidate in candidates}
    return [by_key[_document_key(document)] for document in documents]


def _versioned_families(documents: list[Document]) -> set[str]:
    statuses_by_family: dict[str, set[str]] = {}
    for document in documents:
        family = str(document.metadata.get("document_family", "")).strip()
        if not family:
            continue
        status = str(document.metadata.get("status", "active")).strip().casefold()
        statuses_by_family.setdefault(family, set()).add(status)
    return {
        family
        for family, statuses in statuses_by_family.items()
        if len(statuses) > 1
    }


def _candidate_document(
    candidate: _RankedCandidate,
    *,
    retrieval_stage: str,
    rerank_rank: int | None = None,
    rerank_score: float | None = None,
) -> Document:
    return _with_metadata(
        candidate.document,
        retrieval_stage=retrieval_stage,
        retrieval_score=candidate.fused_score,
        dense_rank=candidate.dense_rank,
        lexical_rank=candidate.lexical_rank,
        rerank_rank=rerank_rank,
        rerank_score=rerank_score,
    )


def _with_metadata(document: Document, **values: object) -> Document:
    metadata = dict(document.metadata)
    metadata.update(values)
    return Document(page_content=document.page_content, metadata=metadata)


def _document_key(document: Document) -> str:
    chunk_id = str(document.metadata.get("chunk_id", "")).strip()
    if chunk_id:
        return chunk_id
    return "|".join(
        (
            str(document.metadata.get("title", "")),
            str(document.metadata.get("chunk_index", "")),
            document.page_content,
        )
    )
