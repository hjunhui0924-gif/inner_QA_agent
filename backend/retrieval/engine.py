"""Four-stage retrieval behind one stable interface."""

from __future__ import annotations

import math
import re
import time
from collections import Counter
from dataclasses import dataclass
from typing import Literal, Protocol

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
    ) -> RetrievalResult:
        """Retrieve documents using the requested ablation strategy."""

        started = time.perf_counter()
        selected = strategy or self._config.production_strategy
        if top_k <= 0 or not query.strip():
            return self._result([], selected, 0, 0, 0, False, None, started)

        dense: list[Document] = []
        lexical: list[Document] = []
        if selected in {"dense", "fusion", "rerank"}:
            dense = self._dense_store.similarity_search(
                query,
                k=max(top_k, self._config.dense_candidate_k),
            )
        if selected in {"lexical", "fusion", "rerank"}:
            lexical = self._lexical_search(
                query,
                max(top_k, self._config.lexical_candidate_k),
            )

        if selected == "dense":
            documents = [
                _with_metadata(document, retrieval_stage="dense", dense_rank=rank)
                for rank, document in enumerate(dense[:top_k], start=1)
            ]
            return self._result(
                documents, selected, len(dense), 0, 0, False, None, started
            )
        if selected == "lexical":
            documents = [
                _with_metadata(document, retrieval_stage="lexical", lexical_rank=rank)
                for rank, document in enumerate(lexical[:top_k], start=1)
            ]
            return self._result(
                documents, selected, 0, len(lexical), 0, False, None, started
            )

        fused = self._fuse(dense, lexical)
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
            )
        try:
            scores = self._reranker.rerank(
                query,
                [candidate.document for candidate in rerank_pool],
                top_k,
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
            return self._result(
                documents,
                selected,
                len(dense),
                len(lexical),
                len(fused),
                True,
                None,
                started,
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
            )

    def _fuse(
        self,
        dense: list[Document],
        lexical: list[Document],
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
        return sorted(
            fused.values(),
            key=lambda item: (
                -item.fused_score,
                item.dense_rank or 10**9,
                item.lexical_rank or 10**9,
                _document_key(item.document),
            ),
        )

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
